"""Provider-neutral communication models for Gmail and Google Contacts.

The integration adapters convert Google wire payloads into these models before
the data reaches a service, tool, or agent.  Keeping provider field names out
of the domain contract makes later provider changes and deterministic tests
straightforward.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PageItem = TypeVar("PageItem")


class Page(BaseModel, Generic[PageItem]):
    """Stable page envelope shared by Gmail and Contacts read operations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[PageItem] = Field(default_factory=list)
    next_page_token: str | None = None
    result_size_estimate: int | None = Field(default=None, ge=0)

    @field_validator("next_page_token", mode="after")
    @classmethod
    def normalize_page_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class EmailAddress(BaseModel):
    """Normalized display name and mailbox address."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    display_name: str | None = None
    email: str

    @model_validator(mode="before")
    @classmethod
    def accept_plain_address(cls, value: object) -> object:
        if isinstance(value, str):
            return {"email": value}
        return value

    @field_validator("display_name", mode="after")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @field_validator("email", mode="after")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized or any(char in normalized for char in "\r\n"):
            raise ValueError("Email address cannot be blank or contain line breaks.")
        if "@" not in normalized or normalized.startswith("@") or normalized.endswith("@"):
            raise ValueError("A valid mailbox address is required.")
        return normalized


class GmailMessageSummary(BaseModel):
    """Normalized result returned by Gmail message-list endpoints."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(..., min_length=1)
    thread_id: str = Field(..., min_length=1)
    label_ids: list[str] = Field(default_factory=list)
    snippet: str = ""
    internal_date: datetime | None = None

    @field_validator("id", "thread_id", mode="after")
    @classmethod
    def normalize_identifiers(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Gmail resource identifiers cannot be blank.")
        return normalized

    @field_validator("internal_date", mode="after")
    @classmethod
    def ensure_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class GmailMessage(GmailMessageSummary):
    """Normalized full Gmail message, independent of Gmail API payload shape."""

    headers: dict[str, str] = Field(default_factory=dict)
    subject: str | None = None
    sender: EmailAddress | None = None
    reply_to: EmailAddress | None = None
    to: list[EmailAddress] = Field(default_factory=list)
    cc: list[EmailAddress] = Field(default_factory=list)
    bcc: list[EmailAddress] = Field(default_factory=list)
    body_text: str = ""
    body_html: str | None = None
    has_attachments: bool = False
    size_estimate: int | None = Field(default=None, ge=0)
    history_id: str | None = None

    @property
    def from_address(self) -> EmailAddress | None:
        """Compatibility alias for callers that use the Gmail header name."""
        return self.sender

    @property
    def recipients(self) -> list[EmailAddress]:
        """Return direct recipients without exposing provider header details."""
        return list(self.to)


class GmailThreadSummary(BaseModel):
    """Normalized result returned by Gmail thread-list endpoints."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(..., min_length=1)
    snippet: str = ""
    history_id: str | None = None
    message_count: int | None = Field(default=None, ge=0)

    @field_validator("id", mode="after")
    @classmethod
    def normalize_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Gmail thread identifiers cannot be blank.")
        return normalized


class GmailThread(GmailThreadSummary):
    """Normalized Gmail thread with full normalized messages."""

    messages: list[GmailMessage] = Field(default_factory=list)


class GmailMessagePage(Page[GmailMessageSummary]):
    """Typed Gmail message search page."""


class GmailThreadPage(Page[GmailThreadSummary]):
    """Typed Gmail thread listing page."""


class GmailDraft(BaseModel):
    """Normalized Gmail draft response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(..., min_length=1)
    message: GmailMessage | None = None


class GmailSendResult(BaseModel):
    """Provider-neutral result of sending a message or draft."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(..., min_length=1)
    thread_id: str | None = None
    label_ids: list[str] = Field(default_factory=list)


class GmailMutationResult(BaseModel):
    """Normalized result of a non-send Gmail mutation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_id: str = Field(..., min_length=1)
    operation: str = Field(..., min_length=1)
    thread_id: str | None = None
    label_ids: list[str] = Field(default_factory=list)


class GmailLabel(BaseModel):
    """Normalized Gmail label metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    label_type: str | None = None


class GmailComposeRequest(BaseModel):
    """Typed input for draft and outbound-message composition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    to: list[str | EmailAddress] = Field(..., min_length=1)
    subject: str = ""
    body_text: str = ""
    cc: list[str | EmailAddress] = Field(default_factory=list)
    bcc: list[str | EmailAddress] = Field(default_factory=list)
    body_html: str | None = None
    thread_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None

    @field_validator("subject", "body_text", mode="after")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n")


class ContactEmail(BaseModel):
    """Normalized email address from Google Contacts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str
    type: str | None = None

    @field_validator("value", mode="after")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not normalized:
            raise ValueError("Contact email cannot be blank.")
        return normalized


class ContactPhone(BaseModel):
    """Normalized phone number from Google Contacts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: str
    type: str | None = None

    @field_validator("value", mode="after")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Contact phone cannot be blank.")
        return normalized


class Contact(BaseModel):
    """Normalized Google Contact record used by deterministic resolution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_name: str = Field(..., min_length=1)
    etag: str | None = None
    display_name: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    emails: list[ContactEmail] = Field(default_factory=list)
    phones: list[ContactPhone] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)

    @field_validator("resource_name", mode="after")
    @classmethod
    def normalize_resource_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Contact resource name cannot be blank.")
        return normalized

    @field_validator("display_name", "given_name", "family_name", mode="after")
    @classmethod
    def normalize_names(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @property
    def email_values(self) -> tuple[str, ...]:
        """Return normalized email values for exact matching."""
        return tuple(email.value for email in self.emails)

    @property
    def full_name(self) -> str | None:
        """Return a deterministic full-name candidate when names are split."""
        parts = [part for part in (self.given_name, self.family_name) if part]
        return " ".join(parts) or self.display_name


class ContactPage(Page[Contact]):
    """Typed Contacts search/list page."""


class ContactResolutionStatus(StrEnum):
    """Outcome of deterministic contact resolution."""

    EXACT = "exact"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class ContactResolution(BaseModel):
    """Resolution result that never silently chooses among multiple contacts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(..., min_length=1)
    status: ContactResolutionStatus
    contact: Contact | None = None
    candidates: list[Contact] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_resolution_shape(self) -> "ContactResolution":
        if self.status == ContactResolutionStatus.EXACT and self.contact is None:
            raise ValueError("An exact contact resolution must include a contact.")
        if self.status != ContactResolutionStatus.EXACT and self.contact is not None:
            raise ValueError("Only an exact contact resolution may include a contact.")
        return self


__all__ = [
    "Contact",
    "ContactEmail",
    "ContactPage",
    "ContactPhone",
    "ContactResolution",
    "ContactResolutionStatus",
    "EmailAddress",
    "GmailComposeRequest",
    "GmailDraft",
    "GmailLabel",
    "GmailMessage",
    "GmailMessagePage",
    "GmailMessageSummary",
    "GmailMutationResult",
    "GmailSendResult",
    "GmailThread",
    "GmailThreadPage",
    "GmailThreadSummary",
    "Page",
]
