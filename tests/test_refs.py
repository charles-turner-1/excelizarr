"""A1 references: the column bijection, range parsing, and the errors."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from excelizarr._refs import (
    MAX_COLS,
    MAX_ROWS,
    RangeRef,
    col_to_index,
    format_range,
    index_to_col,
    parse_range,
)
from excelizarr.exceptions import InvalidReferenceError


@given(st.integers(0, MAX_COLS - 1))
def test_column_letters_round_trip(i: int) -> None:
    """``col_to_index`` undoes ``index_to_col`` for every column a sheet has."""
    assert col_to_index(index_to_col(i)) == i


@given(st.text("ABCDEFGHIJKLMNOPQRSTUVWXYZ", min_size=1, max_size=3))
def test_column_index_round_trip(letters: str) -> None:
    """``index_to_col`` undoes ``col_to_index``, up to XFD."""
    try:
        i = col_to_index(letters)
    except InvalidReferenceError:
        assert len(letters) == 3 and letters > "XFD"
        return
    assert index_to_col(i) == letters


@given(
    st.integers(0, MAX_ROWS - 1),
    st.integers(1, 50),
    st.integers(0, MAX_COLS - 1),
    st.integers(1, 50),
)
def test_format_parse_round_trip(r0: int, nr: int, c0: int, nc: int) -> None:
    """Formatting bounds as A1 and parsing them back gives the same bounds."""
    r1, c1 = min(r0 + nr, MAX_ROWS), min(c0 + nc, MAX_COLS)
    ref = parse_range(format_range(r0, r1, c0, c1))
    assert (ref.row_start, ref.row_stop, ref.col_start, ref.col_stop) == (
        r0,
        r1,
        c0,
        c1,
    )


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("A1", RangeRef(None, 0, 1, 0, 1)),
        ("b2:d10", RangeRef(None, 1, 10, 1, 4)),
        ("D10:B2", RangeRef(None, 1, 10, 1, 4)),
        ("$B$2:D$10", RangeRef(None, 1, 10, 1, 4)),
        ("A:C", RangeRef(None, None, None, 0, 3)),
        ("2:5", RangeRef(None, 1, 5, None, None)),
        ("Sheet1!A1", RangeRef("Sheet1", 0, 1, 0, 1)),
        ("'My Sheet'!A1:B2", RangeRef("My Sheet", 0, 2, 0, 2)),
        ("'Q1 ''24'!C:C", RangeRef("Q1 '24", None, None, 2, 3)),
        ("XFD1048576", RangeRef(None, MAX_ROWS - 1, MAX_ROWS, MAX_COLS - 1, MAX_COLS)),
    ],
)
def test_parse_range_goldens(ref: str, expected: RangeRef) -> None:
    """Each spelling Excel accepts parses to the expected half-open bounds."""
    assert parse_range(ref) == expected


@pytest.mark.parametrize(
    "ref",
    [
        "",
        "A",
        "1",
        "A0",
        "XFE1",
        "A1048577",
        "A1:B2:C3",
        "A:1",
        "A1:C",
        "!A1",
        "A1!",
        "1A",
    ],
)
def test_parse_range_rejects(ref: str) -> None:
    """References Excel would reject raise ``InvalidReferenceError``."""
    with pytest.raises(InvalidReferenceError):
        parse_range(ref)


def test_resolve_clips_open_ranges() -> None:
    """An open range is clipped to the sheet, and a range past the edge is empty."""
    assert parse_range("A:C").resolve((10, 2)) == (slice(0, 10), slice(0, 2))
    assert parse_range("2:5").resolve((3, 4)) == (slice(1, 3), slice(0, 4))
    assert parse_range("Z99").resolve((3, 4)) == (slice(3, 3), slice(4, 4))


def test_index_to_col_rejects_out_of_range() -> None:
    """Indices outside the sheet raise."""
    with pytest.raises(InvalidReferenceError):
        index_to_col(MAX_COLS)
    with pytest.raises(InvalidReferenceError):
        index_to_col(-1)
