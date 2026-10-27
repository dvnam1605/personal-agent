"""Telegram bot settings."""

from pydantic import BaseModel, Field


class TelegramSettings(BaseModel):
    """Chat with the assistant and approve actions from Telegram.

    The bot runs only when ``bot_token`` is set. It uses long polling, so no public
    webhook URL is needed; only chats linked to an account with a one-time code are
    served.
    """

    bot_token: str | None = Field(default=None, repr=False, description="BotFather token")
    bot_username: str | None = Field(default=None, description="Bot @username shown in the app")
    api_base: str = Field(default="https://api.telegram.org", description="Bot API base URL")
    poll_timeout_seconds: int = Field(default=25, ge=1, le=50, description="Long-poll wait")
    link_code_ttl_minutes: int = Field(default=10, ge=1, le=60, description="Link code lifetime")
