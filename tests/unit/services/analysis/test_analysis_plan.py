"""Number parsing and sorting of the spreadsheet query interpreter."""

from __future__ import annotations

from app.services.analysis.plan import Aggregate, QueryPlan, run_plan, to_number


def test_us_formatted_numbers_parse() -> None:
    assert to_number("1,234.56") == 1234.56
    assert to_number("12,345,678.9") == 12345678.9
    assert to_number("2.500,75") == 2500.75


def test_us_formatted_cells_count_in_sums() -> None:
    columns = ["Khu vực", "Doanh thu"]
    rows = [{"Khu vực": "A", "Doanh thu": "1,234.50"}, {"Khu vực": "A", "Doanh thu": "10"}]
    plan = QueryPlan(aggregates=[Aggregate(fn="sum", column="Doanh thu")])
    assert run_plan(plan, columns, rows)[1] == [[1244.5]]


def test_non_numeric_rows_sort_last_in_both_directions() -> None:
    columns = ["Tên", "Điểm"]
    rows = [
        {"Tên": "a", "Điểm": "n/a"},
        {"Tên": "b", "Điểm": "5"},
        {"Tên": "c", "Điểm": ""},
        {"Tên": "d", "Điểm": "9"},
    ]
    for descending, expected in ((True, ["d", "b"]), (False, ["b", "d"])):
        plan = QueryPlan(select=["Tên", "Điểm"], sort_by="Điểm", descending=descending)
        names = [row[0] for row in run_plan(plan, columns, rows)[1]]
        assert names[:2] == expected
        assert set(names[2:]) == {"a", "c"}
