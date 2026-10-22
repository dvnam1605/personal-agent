"""Put the equations of the sources into the sections that present them.

Ingestion stores equations as "Công thức (3.5): $$<latex>$$" (or, when the
LaTeX could not be rebuilt, "Công thức: <text> (3.5)") in their own chunks, so
section retrieval often misses them. The source equations are collected once,
with their section and preceding text, and inserted into each written section:
after the sentence that mentions them by number, or, for the source sections the
section was planned from, after the most related paragraph (else at the end).
Each formula appears at most once in the document, as an equation line
"$$<latex>$$ (3.5)" that the renderers typeset.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.text import unaccent_vietnamese

# Equation lines as stored at ingestion: LaTeX, or the raw text layer.
_STORED_LATEX = re.compile(
    r"Công thức \((?P<num>\d+(?:\.\d+)+)\):\s*\$\$(?P<latex>.+?)\$\$", re.DOTALL
)
_STORED = re.compile(r"Công thức:\s*(?P<body>.+?)\s*\((?P<num>\d+(?:\.\d+)+)\)", re.MULTILINE)
# A typeset equation line in a written section: "$$<latex>$$ (3.5)".
EQUATION_LINE = re.compile(
    r"^\$\$(?P<latex>.+?)\$\$\s*(?:\((?P<num>\d+(?:\.\d+)+)\))?\s*$", re.DOTALL
)
# A mention in written text: "Công thức (3.5)", "biểu thức 3.3".
_MENTION = re.compile(r"(?i)(?:công thức|biểu thức|phương trình)\s*\(?(?P<num>\d+(?:\.\d+)+)\)?")
# Colons/semicolons do not end a sentence, so an equation never splits one there.
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")
_LEAD_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZĐÀ-Ỹ])")
_SECTION_NO = re.compile(r"^(\d+(?:\.\d+)+)\b")
# Content words a paragraph must share with a formula's context to host it.
MIN_CONTEXT_OVERLAP = 4
_WORD = re.compile(r"\w+", re.UNICODE)
_STOP = frozenset(
    "va cua cac la duoc cho trong voi theo mot nhung de khi tu nay do co the cong thuc "
    "bieu dien xac dinh he thong ky thuat gia tri".split()
)


@dataclass(frozen=True)
class Formula:
    number: str
    body: str
    section: str = ""  # source heading the formula sits under
    context: str = ""  # the source text right before the formula
    latex: str = ""


def _words(text: str) -> set[str]:
    return {w for w in _WORD.findall(unaccent_vietnamese(text).lower()) if len(w) > 2} - _STOP


def _key(text: str) -> str:
    return re.sub(r"[\W_]+", "", unaccent_vietnamese(text).lower())


def collect_formulas(chunks: list[tuple[str, str]]) -> dict[str, Formula]:
    """Formulas by number from stored (heading, text) chunks."""
    formulas: dict[str, Formula] = {}
    for heading, text in chunks:
        matches = [(m, m.group("latex").strip()) for m in _STORED_LATEX.finditer(text)]
        matches += [(m, "") for m in _STORED.finditer(_STORED_LATEX.sub(" ", text))]
        for m, latex in matches:
            num = m.group("num")
            body = latex or re.sub(r"\s+", " ", m.group("body")).strip()
            if not body or num in formulas:
                continue
            source = text if latex else _STORED_LATEX.sub(" ", text)
            before = _STORED.sub(" ", _STORED_LATEX.sub(" ", source[: m.start()]))
            formulas[num] = Formula(
                number=num,
                body=body,
                section=heading,
                context=re.sub(r"\s+", " ", before).strip()[-400:],
                latex=latex,
            )
    return formulas


def formula_line(formula: Formula) -> str:
    if formula.latex:
        return f"$${formula.latex}$$ ({formula.number})"
    return f"Công thức ({formula.number}): {formula.body}"


def _covers(sources: list[str], heading: str) -> bool:
    if not heading:
        return False
    target = _key(heading)
    target_no = _SECTION_NO.match(heading.strip())
    for source in sources:
        key = _key(source)
        if key and (key in target or target in key):
            return True
        # A section number covers its subsections (3.4 covers 3.4.2).
        source_no = _SECTION_NO.match(source.strip())
        if (
            source_no
            and target_no
            and (
                target_no.group(1) == source_no.group(1)
                or target_no.group(1).startswith(source_no.group(1) + ".")
            )
        ):
            return True
    return False


covers = _covers  # shared with figures.py


def formula_hint(formulas: dict[str, Formula], sources: list[str]) -> str:
    """Formulas of a section's source headings, for the writer prompt."""
    lines = [
        f"- ({f.number}) {f.body[:160]} — ngữ cảnh: {f.context[-160:]}"
        for f in sorted(formulas.values(), key=lambda f: [int(p) for p in f.number.split(".")])
        if _covers(sources, f.section)
    ]
    return "\n".join(lines)


def _order(number: str) -> list[int]:
    return [int(p) for p in number.split(".")]


def _tidy(text: str) -> str:
    text = re.sub(r"\(\s+", "(", re.sub(r"\s+([,.:;)])", r"\1", text))
    return re.sub(r"\s+", " ", text).strip()


def formula_lead(formula: Formula) -> str:
    """The source's own sentence introducing the formula, e.g. "… theo Công thức (3.2):"."""
    sentences = [s.strip() for s in _LEAD_SPLIT.split(formula.context) if s.strip()]
    # The stored context is cut to its tail, so its first sentence may be partial.
    if sentences and not sentences[0][:1].isupper():
        sentences = sentences[1:]
    if not sentences:
        return ""
    lead = sentences[-1]
    if len(lead) < 90 and len(sentences) > 1:
        lead = f"{sentences[-2]} {lead}"
    lead = _tidy(lead)
    return lead if lead.endswith((":", ".")) else f"{lead}:"


def _blocks(numbers: list[str], formulas: dict[str, Formula], *, with_lead: bool) -> str:
    """Formulas on their own lines; formulas sharing a lead are introduced once."""
    parts: list[str] = []
    last_lead = ""
    for number in sorted(numbers, key=_order):
        lead = formula_lead(formulas[number]) if with_lead else ""
        if lead and lead != last_lead:
            parts.append(lead)
        last_lead = lead
        parts.append(formula_line(formulas[number]))
    return "\n\n".join(parts)


def insert_formulas(
    text: str,
    formulas: dict[str, Formula],
    *,
    sources: list[str] | None = None,
    already_shown: set[str] | None = None,
) -> str:
    """The section's text with the formulas it presents shown on their own lines."""
    if not formulas or not text:
        return text
    shown = already_shown if already_shown is not None else set()
    present = _key(text)

    def written_already(formula: Formula) -> bool:
        return _key(formula.body)[:24] in present

    # Formulas mentioned by number go right after the mentioning sentence.
    after_sentence: dict[int, list[str]] = {}
    for m in _MENTION.finditer(text):
        formula = formulas.get(m.group("num"))
        if formula is None or formula.number in shown or written_already(formula):
            continue
        shown.add(formula.number)
        end = _SENTENCE_END.search(text, m.end())
        after_sentence.setdefault(end.end() if end else len(text), []).append(formula.number)
    if after_sentence:
        out: list[str] = []
        last = 0
        for offset in sorted(after_sentence):
            out.append(text[last:offset])
            out.append("\n\n" + _blocks(after_sentence[offset], formulas, with_lead=False) + "\n\n")
            last = offset
        out.append(text[last:].lstrip(" "))
        text = re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()

    # Formulas of the planned source sections go after the most related paragraph.
    if not sources:
        return text
    paragraphs = text.split("\n\n")
    after_paragraph: dict[int, list[str]] = {}
    trailing: list[str] = []
    for formula in sorted(formulas.values(), key=lambda f: _order(f.number)):
        if formula.number in shown or written_already(formula):
            continue
        if not _covers(sources, formula.section):
            continue
        shown.add(formula.number)
        topic = _words(f"{formula.section} {formula.context}")
        scores = [
            0 if p.startswith(("Công thức (", "$$", "![")) else len(_words(p) & topic)
            for p in paragraphs
        ]
        best = max(range(len(paragraphs)), key=lambda i: scores[i])
        if scores[best] >= MIN_CONTEXT_OVERLAP:
            after_paragraph.setdefault(best, []).append(formula.number)
        else:
            trailing.append(formula.number)
    if not after_paragraph and not trailing:
        return text
    out = []
    for i, paragraph in enumerate(paragraphs):
        out.append(paragraph)
        if i in after_paragraph:
            out.append(_blocks(after_paragraph[i], formulas, with_lead=True))
    if trailing:
        out.append(_blocks(trailing, formulas, with_lead=True))
    return "\n\n".join(out).strip()
