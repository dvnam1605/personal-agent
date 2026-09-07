"""Unit tests for web search domain contracts (spec P13)."""

import pytest

from app.domain.models import WebSearchPage, WebSearchResultItem


def test_blank_url_rejected_after_strip() -> None:
    with pytest.raises(ValueError):
        WebSearchResultItem(url="   ")
    with pytest.raises(ValueError):
        WebSearchResultItem(url="")


def test_provider_text_stripped() -> None:
    item = WebSearchResultItem(
        url="  https://example.com/a  ",
        title="  Title  ",
        snippet=" snip ",
        source=" web ",
    )
    assert item.url == "https://example.com/a"
    assert item.title == "Title"
    assert item.snippet == "snip"
    assert item.source == "web"


def test_page_defaults_to_empty() -> None:
    page = WebSearchPage()
    assert page.items == []
    assert page.query == ""
