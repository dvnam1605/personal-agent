"""Google Gmail adapter and message parsing utilities."""

from .adapter import (
    GMAIL_FORMATS,
    GMAIL_MODIFY_SCOPE,
    GMAIL_READ_SCOPE,
    GmailAdapter,
    GmailFormat,
    html_to_text,
)

__all__ = [
    "GMAIL_FORMATS",
    "GMAIL_MODIFY_SCOPE",
    "GMAIL_READ_SCOPE",
    "GmailAdapter",
    "GmailFormat",
    "html_to_text",
]
