"""A1-style references: ``"B2"``, ``"A:C"``, ``"'My Sheet'!$B$2:D10"``.

Everything here works in **zero-based, half-open** coordinates, which is what the zarr
arrays are indexed in: ``"B2:D10"`` is rows ``1:10`` and columns ``1:4``. Whole-column
and whole-row ranges (``"A:C"``, ``"2:5"``) leave the other axis open (``None``), and
it is the caller's job to clip that to the sheet's shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .exceptions import InvalidReferenceError

__all__ = [
    "MAX_COLS",
    "MAX_ROWS",
    "RangeRef",
    "col_to_index",
    "format_range",
    "index_to_col",
    "parse_range",
]

#: Excel's own limits: ``XFD1048576`` is the last cell of a sheet.
MAX_ROWS = 1_048_576
MAX_COLS = 16_384

_CELL = re.compile(r"^\$?([A-Za-z]{1,3})\$?([0-9]+)$")
_COL = re.compile(r"^\$?([A-Za-z]{1,3})$")
_ROW = re.compile(r"^\$?([0-9]+)$")


def col_to_index(letters: str) -> int:
    """Convert column letters to a zero-based index (``"A"`` is 0, ``"AA"`` is 26).

    Parameters
    ----------
    letters : str
        One to three letters, either case.

    Returns
    -------
    int
        The zero-based column index.

    Raises
    ------
    InvalidReferenceError
        If ``letters`` isn't a column Excel could have.
    """
    if (
        not letters
        or not letters.isascii()
        or not letters.isalpha()
        or len(letters) > 3
    ):
        raise InvalidReferenceError(f"not a column: {letters!r}")
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - ord("A") + 1)
    if n > MAX_COLS:
        raise InvalidReferenceError(f"column {letters!r} is past XFD")
    return n - 1


def index_to_col(index: int) -> str:
    """Convert a zero-based column index to letters (0 is ``"A"``, 26 is ``"AA"``).

    Parameters
    ----------
    index : int
        A column index in ``[0, MAX_COLS)``.

    Returns
    -------
    str
        The upper-case column letters.

    Raises
    ------
    InvalidReferenceError
        If ``index`` is outside the sheet.
    """
    if not 0 <= index < MAX_COLS:
        raise InvalidReferenceError(f"column index {index} is outside the sheet")
    out = ""
    n = index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


@dataclass(frozen=True)
class RangeRef:
    """A parsed range: an optional sheet plus half-open, zero-based row/col bounds.

    ``None`` on a bound means "open on that side": ``"A:C"`` has ``row_start`` and
    ``row_stop`` both ``None``.
    """

    sheet: str | None
    row_start: int | None
    row_stop: int | None
    col_start: int | None
    col_stop: int | None

    def resolve(self, shape: tuple[int, int]) -> tuple[slice, slice]:
        """Clip the open bounds to a sheet ``shape`` and return two slices.

        Parameters
        ----------
        shape : tuple of int
            The ``(rows, cols)`` extent of the sheet.

        Returns
        -------
        tuple of slice
            ``(rows, cols)`` slices, never reaching past ``shape``.
        """
        nrows, ncols = shape
        r0 = 0 if self.row_start is None else min(self.row_start, nrows)
        r1 = nrows if self.row_stop is None else min(self.row_stop, nrows)
        c0 = 0 if self.col_start is None else min(self.col_start, ncols)
        c1 = ncols if self.col_stop is None else min(self.col_stop, ncols)
        return slice(r0, max(r0, r1)), slice(c0, max(c0, c1))


def _split_sheet(ref: str) -> tuple[str | None, str]:
    if "!" not in ref:
        return None, ref
    sheet, _, rest = ref.rpartition("!")
    if len(sheet) >= 2 and sheet[0] == sheet[-1] == "'":
        sheet = sheet[1:-1].replace("''", "'")
    if not sheet:
        raise InvalidReferenceError(f"empty sheet name in {ref!r}")
    return sheet, rest


def _parse_endpoint(part: str) -> tuple[int | None, int | None]:
    """Parse one side of a range into (row, col), either of which may be open."""
    if m := _CELL.match(part):
        row = int(m.group(2))
        if not 1 <= row <= MAX_ROWS:
            raise InvalidReferenceError(f"row {row} is outside the sheet")
        return row - 1, col_to_index(m.group(1))
    if m := _COL.match(part):
        return None, col_to_index(m.group(1))
    if m := _ROW.match(part):
        row = int(m.group(1))
        if not 1 <= row <= MAX_ROWS:
            raise InvalidReferenceError(f"row {row} is outside the sheet")
        return row - 1, None
    raise InvalidReferenceError(f"not a cell, column or row: {part!r}")


def parse_range(ref: str) -> RangeRef:
    """Parse an A1-style reference into a :class:`RangeRef`.

    Accepts a single cell (``"B2"``), a rectangle (``"B2:D10"``, either corner first),
    whole columns (``"A:C"``), whole rows (``"2:5"``), ``$`` absolute markers and an
    optional sheet prefix, quoted the way Excel quotes it (``"'Q1 ''24'!A1"``).

    Parameters
    ----------
    ref : str
        The reference to parse.

    Returns
    -------
    RangeRef
        Zero-based, half-open bounds.

    Raises
    ------
    InvalidReferenceError
        If ``ref`` doesn't parse, or mixes a column-only end with a row-only end.
    """
    sheet, body = _split_sheet(ref.strip())
    parts = body.split(":")
    if len(parts) == 1:
        if not _CELL.match(parts[0]):
            raise InvalidReferenceError(f"not a cell reference: {ref!r}")
        parts = [parts[0], parts[0]]
    if len(parts) != 2:
        raise InvalidReferenceError(f"too many ':' in {ref!r}")
    (ra, ca), (rb, cb) = (_parse_endpoint(p) for p in parts)
    if (ra is None) != (rb is None) or (ca is None) != (cb is None):
        raise InvalidReferenceError(f"mismatched range ends in {ref!r}")
    if ra is None and ca is None:  # pragma: no cover - unreachable by the regexes
        raise InvalidReferenceError(f"empty range {ref!r}")

    def bounds(a: int | None, b: int | None) -> tuple[int | None, int | None]:
        if a is None or b is None:
            return None, None
        return min(a, b), max(a, b) + 1

    r0, r1 = bounds(ra, rb)
    c0, c1 = bounds(ca, cb)
    return RangeRef(sheet, r0, r1, c0, c1)


def format_range(row_start: int, row_stop: int, col_start: int, col_stop: int) -> str:
    """Format half-open zero-based bounds back to an A1 range (``"B2:D10"``).

    Parameters
    ----------
    row_start : int
        First row, zero-based.
    row_stop : int
        One past the last row.
    col_start : int
        First column, zero-based.
    col_stop : int
        One past the last column.

    Returns
    -------
    str
        The A1 range, or a single cell reference when the range is one cell.
    """
    top_left = f"{index_to_col(col_start)}{row_start + 1}"
    bottom_right = f"{index_to_col(col_stop - 1)}{row_stop}"
    return top_left if top_left == bottom_right else f"{top_left}:{bottom_right}"
