"""Google OAuth client, endpoints, scopes and token-encryption settings."""

import json

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.settings.paths import resolve_project_path


class GoogleOAuthSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    client_id: str | None = Field(default=None, repr=False, description="Google OAuth Client ID")
    client_secret: str | None = Field(
        default=None, repr=False, exclude=True, description="Google OAuth Client Secret"
    )
    client_secrets_file: str | None = Field(
        default=None,
        description="Path to the downloaded Google OAuth client-secrets JSON file",
    )
    redirect_uri: str = Field(
        default="http://localhost:8000/auth/google/callback",
        description="OAuth redirect URI",
    )
    scopes: list[str] = Field(
        default_factory=lambda: [
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/contacts.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
        ],
        description="OAuth requested scopes",
    )
    authorization_endpoint: str = Field(
        default="https://accounts.google.com/o/oauth2/v2/auth",
        description="Google OAuth authorization endpoint",
    )
    token_endpoint: str = Field(
        default="https://oauth2.googleapis.com/token",
        description="Google OAuth token endpoint",
    )
    revoke_endpoint: str = Field(
        default="https://oauth2.googleapis.com/revoke",
        description="Google OAuth token revocation endpoint",
    )
    userinfo_endpoint: str = Field(
        default="https://openidconnect.googleapis.com/v1/userinfo",
        description="Google OpenID Connect userinfo endpoint",
    )
    token_encryption_key: str | None = Field(
        default=None,
        repr=False,
        exclude=True,
        description="Optional Fernet key for encrypting Google tokens",
    )
    token_encryption_key_file: str = Field(
        default=".secrets/google_token.key",
        description="Local key file used when token_encryption_key is not supplied",
    )
    oauth_state_ttl_seconds: int = Field(
        default=600,
        ge=60,
        le=900,
        description="Maximum lifetime of a pending OAuth state",
    )
    refresh_skew_seconds: int = Field(
        default=60,
        ge=0,
        le=3600,
        description="Refresh an access token this many seconds before expiry",
    )

    @field_validator("client_id", "client_secret", "client_secrets_file", mode="before")
    @classmethod
    def empty_strings_are_none(cls, value: object) -> object:
        """Treat blank dotenv values as unset so the JSON file can supply credentials."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def load_client_secrets_file(self) -> "GoogleOAuthSettings":
        """Load missing client credentials from Google's downloaded JSON configuration."""
        if self.client_id and self.client_secret:
            return self
        if not self.client_secrets_file:
            return self

        path = resolve_project_path(self.client_secrets_file)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            client = payload.get("web") or payload.get("installed")
            if not isinstance(client, dict):
                return self
            client_id = client.get("client_id")
            client_secret = client.get("client_secret")
            updates: dict[str, str] = {}
            if not self.client_id and isinstance(client_id, str) and client_id.strip():
                updates["client_id"] = client_id.strip()
            if not self.client_secret and isinstance(client_secret, str) and client_secret.strip():
                updates["client_secret"] = client_secret.strip()
            if updates:
                for field_name, value in updates.items():
                    setattr(self, field_name, value)
        except (OSError, ValueError, TypeError) as exc:
            import logging

            logging.getLogger(__name__).warning(
                "google_client_secrets_file_unreadable: %s",
                type(exc).__name__,
            )
        return self
