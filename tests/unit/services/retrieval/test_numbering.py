"""Figures and equations are numbered by where they appear in the answer or report."""

from __future__ import annotations

from app.core.images import image_ref
from app.services.retrieval.numbering import renumber

ARCH = image_ref("Hình 4.3: Sơ đồ kiến trúc — mô tả tự động: ba tầng", "a" * 24)
UI = image_ref("Hình 22 — mô tả tự động: màn hình đăng nhập", "b" * 24)
SECTION = (
    "Kiến trúc như Hình 4.3 gồm ba tầng [1].\n"
    f"{ARCH}\n"
    "Điểm RRF tính theo Công thức (3.5), khoảng giá trị theo Công thức (3.6) [2]:\n"
    "$$S(d) = \\sum_i \\frac{1}{k + r_i}$$ (3.5)\n"
    "$$range(s) = [a, b]$$ (3.6)\n"
    f"{UI}\n"
    "Tài liệu gốc còn có Hình 2.1 (không được đưa vào)."
)


def test_a_report_section_numbers_its_own_figures_and_equations() -> None:
    lines = renumber(SECTION, "3").split("\n")
    assert lines[0] == "Kiến trúc như Hình 3.1 gồm ba tầng [1]."
    assert lines[1] == image_ref("Hình 3.1: Sơ đồ kiến trúc — mô tả tự động: ba tầng", "a" * 24)
    assert lines[2] == (
        "Điểm RRF tính theo Công thức (3.1), khoảng giá trị theo Công thức (3.2) [2]:"
    )
    assert lines[3].endswith("$$ (3.1)") and lines[4].endswith("$$ (3.2)")
    assert lines[5] == image_ref("Hình 3.2 — mô tả tự động: màn hình đăng nhập", "b" * 24)
    assert lines[6] == "Tài liệu gốc còn có Hình 2.1 (không được đưa vào)."  # not shown


def test_a_chat_answer_numbers_from_one() -> None:
    out = renumber(SECTION)
    assert "Hình 1: Sơ đồ kiến trúc" in out and "Công thức (1)" in out and "$$ (2)" in out


def test_numbering_twice_changes_nothing() -> None:
    once = renumber(SECTION, "3")
    assert renumber(once, "3") == once


def test_text_without_figures_is_unchanged() -> None:
    text = "Theo Công thức (3.5) của đồ án [1]."
    assert renumber(text, "2") == text
