"""Web research: explicit-intent detection and cited answers from web search results."""

from app.services.web.answer import WebAnswer, WebAnswerService, WebSource, sources_markdown
from app.services.web.intent import parse_web_request

__all__ = ["WebAnswer", "WebAnswerService", "WebSource", "parse_web_request", "sources_markdown"]
