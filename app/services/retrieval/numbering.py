"""Number an answer's own figures and equations in the order they appear.

Images and equations come from source documents with the sources' numbers
("Hình 4.3", "(3.5)"); in a report they belong to the report's sections, in
a chat answer to the answer itself. :func:`renumber` gives them new numbers
in order — "Hình 3.1", "(3.1)" in section 3 of a report, "Hình 1", "(1)" in an
answer — and rewrites the text's references to them ("theo Công thức (3.5)",
"như Hình 4.3") to match. References to figures the text does not show are
left as they are.
"""

from __future__ import annotations

import re

from app.core.images import IMAGE_REF, image_ref

_NUM = r"\d+(?:\.\d+)*"
# "$$…$$ (3.5)" or, when no LaTeX could be rebuilt, "Công thức (3.5): …".
_EQUATION_LINE = re.compile(rf"^(?P<head>\s*\$\$.+\$\$\s*)\((?P<num>{_NUM})\)\s*$")
_FORMULA_LINE = re.compile(rf"^(?P<head>\s*Công thức\s*)\((?P<num>{_NUM})\)(?P<tail>\s*:.*)$")
_IMAGE_LABEL = re.compile(
    rf"^(?:Hình\s*(?P<num>{_NUM}))?\s*:?\s*(?P<rest>.*)$", re.IGNORECASE | re.S
)
_FORMULA_MENTION = re.compile(
    rf"(?P<word>(?:[Cc]ông thức|[Bb]iểu thức|[Pp]hương trình)\s*)\(?(?P<num>{_NUM})\)?(?![\d.]\d)"
)
_FIGURE_MENTION = re.compile(rf"(?P<word>Hình\s*)(?P<num>{_NUM})(?![\d.]\d)")


def renumber(text: str, prefix: str | None = None) -> str:
    """``text`` with its figures and equations numbered prefix.1, prefix.2, … (or 1, 2, …)."""
    lines = text.split("\n")
    figures: dict[str, str] = {}
    formulas: dict[str, str] = {}

    def new_number(count: int) -> str:
        return f"{prefix}.{count}" if prefix else str(count)

    out: list[str] = []
    for line in lines:
        if (picture := IMAGE_REF.fullmatch(line.strip())) is not None:
            label = _IMAGE_LABEL.match(picture.group("alt"))
            old = label.group("num") if label else None
            new = new_number(len(figures) + 1)
            figures[old or f"#{len(figures)}"] = new
            rest = label.group("rest").strip() if label else picture.group("alt")
            alt = (
                f"Hình {new}: {rest}" if rest and not rest.startswith("—") else f"Hình {new} {rest}"
            )
            out.append(image_ref(alt.strip(), picture.group("key")))
            continue
        equation = _EQUATION_LINE.match(line) or _FORMULA_LINE.match(line)
        if equation is not None:
            new = new_number(len(formulas) + 1)
            formulas.setdefault(equation.group("num"), new)
            tail = equation.groupdict().get("tail") or ""
            out.append(f"{equation.group('head')}({new}){tail}")
            continue
        out.append(line)

    def formula_mention(m: re.Match[str]) -> str:
        new = formulas.get(m.group("num"))
        return f"{m.group('word')}({new})" if new else m.group(0)

    def figure_mention(m: re.Match[str]) -> str:
        new = figures.get(m.group("num"))
        return f"{m.group('word')}{new}" if new else m.group(0)

    result: list[str] = []
    for line in out:
        if IMAGE_REF.fullmatch(line.strip()) or _EQUATION_LINE.match(line):
            result.append(line)
            continue
        if _FORMULA_LINE.match(line):  # its own number is already the new one
            result.append(line)
            continue
        if formulas:
            line = _FORMULA_MENTION.sub(formula_mention, line)
        if figures:
            line = _FIGURE_MENTION.sub(figure_mention, line)
        result.append(line)
    return "\n".join(result)
