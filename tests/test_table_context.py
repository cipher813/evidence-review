"""Synthetic tables; conservative layout recognition must never invent columns."""
import pytest
from synthetic import source


def parse(text, start, end=None):
    from evidence_review.table_context import table_context
    return table_context(source(text), start, end or start)


def test_simple_table_retains_cells_labels_lines_units_and_notes():
    text = "Units: USD millions\n| Metric | FY2023 | FY2024 |\n| --- | ---: | ---: |\n| Sales | 100 | 200 |\nNote: restated.\n"
    result = parse(text, 4)
    assert result["status"] == "parsed"
    assert result["headers"] == ["Metric", "FY2023", "FY2024"]
    assert result["rows"] == [{"line": 4, "cells": ["Sales", "100", "200"], "cited": True}]
    assert result["context"][0] == {"line": 1, "text": "Units: USD millions"}
    assert result["notes"] == [{"line": 5, "text": "Note: restated."}]
    assert result["lines"][1] == {"line": 2, "text": "| Metric | FY2023 | FY2024 |"}


def test_escaped_pipes_blank_cells_and_order_survive():
    result = parse("| Label | A | B |\n| --- | --- | --- |\n| A\\|B | | 4 |\n| C | 8 | 9 |", 3)
    assert result["status"] == "parsed"
    assert result["rows"][0]["cells"] == ["A|B", "", "4"]
    assert [row["line"] for row in result["rows"]] == [3, 4]


@pytest.mark.parametrize("text,line", [
    ("| Metric | FY | FY |\n| --- | --- | --- |\n| Sales | 1 | 2 |", 3),
    ("| Metric | FY |\n| Group | Total |\n| --- | --- |\n| Sales | 1 |", 4),
    ("| Metric | FY |\n| --- | --- |\n| Sales | 1 | 2 |", 3),
    ("| Metric | FY |\n| --- | --- |\n| Sales | 1 |\n| Sales | 2 |", 3),
    ("| Metric | FY |\n| --- | --- |\n| **Group** | |\n| Sales | 1 |", 4),
    ("| Metric | FY |\n| --- | --- |\n| `A|B` | 1 |", 3),
])
def test_ambiguous_or_unsupported_structure_keeps_numbered_raw_fallback(text, line):
    result = parse(text, line)
    assert result["status"] == "unparsed"
    assert result["reason"]
    assert any(row["line"] == line for row in result["lines"])
    assert result["rows"] == []


def test_prose_is_readable_without_a_table_claim():
    result = parse("Title\nThe company expects demand to soften.\n", 2)
    assert result["status"] == "unparsed"
    assert result["reason"] == "not a table"


@pytest.mark.parametrize("start,end", [(0,1), (2,1), (1,9)])
def test_invalid_bounds_fail(start, end):
    with pytest.raises(ValueError):
        parse("one\ntwo", start, end)


@pytest.mark.parametrize('row', ['||18.2|21.9|', '|Sales|18.2||'])
def test_empty_boundary_cells_never_shift_column_alignment(row):
    result = parse('| Metric | FY2024 |\n| --- | --- |\n' + row, 3)
    assert result['status'] == 'unparsed'
    assert result['rows'] == []
