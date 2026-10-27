"""A constrained query plan over a table, and the interpreter that runs it.

The model never writes code. It returns a ``QueryPlan`` — filters, grouping, aggregates,
sorting — whose column names are checked against the table and whose operators come
from a fixed list; this module evaluates it over plain Python rows.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.services.skills.matching import unaccent_vietnamese

MAX_RESULT_ROWS = 50

FilterOp = Literal["=", "!=", ">", ">=", "<", "<=", "contains"]
AggregateFn = Literal["sum", "avg", "count", "min", "max"]


class Filter(BaseModel):
    column: str
    op: FilterOp
    value: str | float


class Aggregate(BaseModel):
    fn: AggregateFn
    column: str = Field(default="*", description="Column to aggregate; '*' for count of rows")


class QueryPlan(BaseModel):
    """What to compute: filter rows, group them, aggregate, sort, and keep the top rows."""

    filters: list[Filter] = Field(default_factory=list)
    group_by: list[str] = Field(default_factory=list)
    aggregates: list[Aggregate] = Field(default_factory=list)
    select: list[str] = Field(default_factory=list, description="Columns to show without grouping")
    sort_by: str | None = None
    descending: bool = True
    limit: int = Field(default=20, ge=1, le=MAX_RESULT_ROWS)

    @field_validator("limit", mode="before")
    @classmethod
    def clamp_limit(cls, value: Any) -> int:
        try:
            return max(1, min(int(value), MAX_RESULT_ROWS))
        except (TypeError, ValueError):
            return 20


class PlanError(ValueError):
    """The plan does not fit the table (unknown column, nothing to compute)."""


_NUMBER_CLEAN = re.compile(r"[^\d,.\-]")


def to_number(value: Any) -> float | None:
    """Parse 1234, "1.234.567", "1,5", "12%", "3.000 đ"; ``None`` when not a number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    text = _NUMBER_CLEAN.sub("", str(value or "").strip())
    if not text or text in {"-", ".", ","}:
        return None
    if text.count(".") > 1 or ("." in text and "," in text and text.rfind(".") < text.rfind(",")):
        text = text.replace(".", "").replace(",", ".")
    elif text.count(",") > 1:
        text = text.replace(",", "")
    elif "," in text and "." not in text:
        integer, _, fraction = text.partition(",")
        text = f"{integer}{fraction}" if len(fraction) == 3 else f"{integer}.{fraction}"
    elif "." in text and "," not in text:
        # Vietnamese sheets write thousands as "3.000"; a decimal point has 1–2 or 4+ digits.
        integer, _, fraction = text.partition(".")
        text = f"{integer}{fraction}" if len(fraction) == 3 else text
    try:
        return float(text)
    except ValueError:
        return None


def resolve_column(name: str, columns: list[str]) -> str:
    """Match a column name ignoring case and accents; raise ``PlanError`` when absent."""
    if name == "*":
        return name
    wanted = unaccent_vietnamese(name).strip()
    for column in columns:
        if unaccent_vietnamese(column).strip() == wanted:
            return column
    raise PlanError(f"Không có cột «{name}» trong bảng.")


def _matches(row: dict[str, Any], item: Filter) -> bool:
    cell = row.get(item.column)
    if item.op == "contains":
        return unaccent_vietnamese(str(item.value)) in unaccent_vietnamese(str(cell or ""))
    left, right = to_number(cell), to_number(item.value)
    if left is not None and right is not None:
        a: Any = left
        b: Any = right
    else:
        a, b = (
            unaccent_vietnamese(str(cell or "")).strip(),
            unaccent_vietnamese(str(item.value)).strip(),
        )
    if item.op == "=":
        return a == b
    if item.op == "!=":
        return a != b
    if isinstance(a, str) != isinstance(b, str):
        return False
    return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b}[item.op]


def _aggregate(fn: str, column: str, rows: list[dict[str, Any]]) -> float | int | None:
    if fn == "count":
        return (
            len(rows)
            if column == "*"
            else sum(1 for row in rows if str(row.get(column) or "").strip())
        )
    numbers = [n for n in (to_number(row.get(column)) for row in rows) if n is not None]
    if not numbers:
        return None
    if fn == "sum":
        return sum(numbers)
    if fn == "avg":
        return sum(numbers) / len(numbers)
    return min(numbers) if fn == "min" else max(numbers)


def run_plan(
    plan: QueryPlan, columns: list[str], rows: list[dict[str, Any]]
) -> tuple[list[str], list[list[Any]]]:
    """Evaluate ``plan`` over ``rows``; returns the result header and rows."""
    filters = [
        item.model_copy(update={"column": resolve_column(item.column, columns)})
        for item in plan.filters
    ]
    group_by = [resolve_column(name, columns) for name in plan.group_by]
    aggregates = [
        item.model_copy(update={"column": resolve_column(item.column, columns)})
        for item in plan.aggregates
    ]
    for item in aggregates:
        if item.column == "*" and item.fn != "count":
            raise PlanError("Chỉ đếm (count) mới dùng được cho toàn bộ dòng.")
    selected = [resolve_column(name, columns) for name in plan.select]
    kept = [row for row in rows if all(_matches(row, item) for item in filters)]

    if aggregates:
        groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for row in kept:
            groups[tuple(row.get(name) for name in group_by)].append(row)
        if not group_by:
            groups = {(): kept}
        header = [*group_by, *(f"{a.fn}({a.column})" for a in aggregates)]
        table = [
            [*key, *(_aggregate(a.fn, a.column, members) for a in aggregates)]
            for key, members in groups.items()
        ]
    else:
        if not selected:
            raise PlanError("Kế hoạch không có cột nào để hiển thị hay tính.")
        header = selected
        table = [[row.get(name) for name in selected] for row in kept]

    if plan.sort_by:
        index = (
            header.index(plan.sort_by)
            if plan.sort_by in header
            else header.index(resolve_column(plan.sort_by, header))
        )

        def key(item: list[Any]) -> tuple[int, Any]:
            number = to_number(item[index])
            return (0, number) if number is not None else (1, str(item[index] or ""))

        table.sort(key=key, reverse=plan.descending)
    return header, table[: plan.limit]
