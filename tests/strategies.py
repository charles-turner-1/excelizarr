"""Hypothesis strategies for workbooks Excel (and openpyxl) could actually hold.

The generators are narrowed to what an ``.xlsx`` can represent, because a value Excel
can't store is not an interesting failure here:

- **Numbers** are finite, ints stay within ``2**53``, and floats carry at most 15
  significant digits. Excel stores every number as a double but only promises 15
  digits of it, and openpyxl writes floats with ``%.16g``, so a double needing all 17
  digits doesn't survive the file. Hypothesis found that one: ``1.7976931348623157e308``
  is written as ``1.797693134862316e+308``, which reads back as ``inf``.
- **Strings** avoid the characters XML 1.0 can't carry (openpyxl's
  ``ILLEGAL_CHARACTERS_RE``), ``\\r`` (XML normalises it to ``\\n`` on read), and the
  seven error literals (``"#N/A"`` and friends), which openpyxl stores as errors rather
  than strings.
- **Temporal values** are at whole-second resolution and inside Excel's calendar after
  the 1900 leap-year bug (1900-03-01 onwards). Excel stores them as fractional day
  counts, so sub-second values come back rounded.
- **Formula cells** carry no cached value. openpyxl has no calculation engine, so a
  workbook it wrote never has one, and pretending otherwise would test nothing.
"""

from __future__ import annotations

import datetime as dt
import unicodedata

import hypothesis.strategies as st
import numpy as np

from excelizarr.kinds import ERROR_CODES
from excelizarr.model import NumberFormats, SheetGrid, WorkbookModel

_ILLEGAL = {chr(c) for c in [*range(0, 9), 11, 12, *range(14, 32)]} | {"\r"}

#: Excel's own ban list for sheet titles, plus the apostrophe-at-the-ends rule.
_TITLE_BANNED = set("\\*?:/[]")

ints = st.integers(-(2**53), 2**53)
floats = st.floats(-1e300, 1e300).map(lambda x: float(f"{x:.15g}"))
texts = st.text(
    st.characters(blacklist_categories=("Cs",), blacklist_characters=_ILLEGAL),
    max_size=40,
).filter(lambda s: s not in ERROR_CODES)
bools = st.booleans()
datetimes = st.datetimes(dt.datetime(1900, 3, 1), dt.datetime(9999, 12, 31)).map(
    lambda d: d.replace(microsecond=0)
)
dates = st.dates(dt.date(1900, 3, 1), dt.date(9999, 12, 31))
times = st.times().map(lambda t: t.replace(microsecond=0))
durations = st.integers(0, 10_000 * 86_400).map(lambda s: dt.timedelta(seconds=s))
errors = st.sampled_from(sorted(ERROR_CODES))

#: Everything a cell can hold, empty most often (sheets are mostly empty).
cell_values = st.one_of(
    st.none(),
    st.none(),
    ints,
    floats,
    texts,
    bools,
    datetimes,
    dates,
    times,
    durations,
    errors,
)

#: Plain values only: what every reader (calamine included) agrees on.
plain_values = st.one_of(st.none(), ints, floats, texts, bools)

#: Number formats a non-temporal number might carry.
NUMERIC_FORMATS = ["0.00", "0%", "#,##0", "0.000E+00", '"£"#,##0.00']


@st.composite
def sheet_names(draw: st.DrawFn, n: int) -> list[str]:
    """Draw ``n`` distinct, valid sheet titles."""
    title = st.text(
        st.characters(
            categories=("L", "N", "P", "S", "Zs"),
            exclude_characters=_TITLE_BANNED,
        ),
        min_size=1,
        max_size=31,
    ).filter(
        lambda s: not s.startswith("'")
        and not s.endswith("'")
        and s.strip() == s
        and s.lower() != "history"
        and unicodedata.normalize("NFC", s) == s
    )
    return draw(
        st.lists(title, min_size=n, max_size=n, unique_by=lambda s: s.casefold())
    )


@st.composite
def grids(
    draw: st.DrawFn,
    name: str = "Sheet1",
    values: st.SearchStrategy[object] = cell_values,
    max_rows: int = 12,
    max_cols: int = 8,
    with_formulas: bool = True,
    numfmts: NumberFormats | None = None,
) -> SheetGrid:
    """Draw one sheet: a rectangle of cells, some formulas, some number formats."""
    nrows = draw(st.integers(0, max_rows))
    ncols = draw(st.integers(0, max_cols)) if nrows else 0
    if nrows and not ncols:
        nrows = 0
    rows = [[draw(values) for _ in range(ncols)] for _ in range(nrows)]
    formulas: list[list[str | None]] | None = None
    if with_formulas and nrows:
        formulas = [[None] * ncols for _ in range(nrows)]
        for i, j in draw(
            st.lists(
                st.tuples(st.integers(0, nrows - 1), st.integers(0, ncols - 1)),
                max_size=3,
            )
        ):
            formulas[i][j] = draw(
                st.sampled_from(["=A1+1", "=SUM(A1:B2)", "=1/0", '="x"&A1'])
            )
            rows[i][j] = None
    fmt_cells = {}
    if numfmts is not None:
        for i, row in enumerate(rows):
            for j, v in enumerate(row):
                if (
                    isinstance(v, int | float)
                    and not isinstance(v, bool)
                    and draw(st.booleans())
                ):
                    fmt_cells[(i, j)] = numfmts.index(
                        draw(st.sampled_from(NUMERIC_FORMATS))
                    )
    return SheetGrid.from_values(name, rows, formulas, fmt_cells)


@st.composite
def workbooks(
    draw: st.DrawFn,
    values: st.SearchStrategy[object] = cell_values,
    max_sheets: int = 3,
    with_formulas: bool = True,
    with_layout: bool = True,
) -> WorkbookModel:
    """Draw a workbook of one to ``max_sheets`` sheets."""
    names = draw(sheet_names(draw(st.integers(1, max_sheets))))
    numfmts = NumberFormats()
    sheets = []
    for name in names:
        g = draw(grids(name, values, with_formulas=with_formulas, numfmts=numfmts))
        if with_layout and g.shape[0] >= 2 and g.shape[1] >= 2 and draw(st.booleans()):
            g.freeze_panes = "B2"
        if with_layout and g.shape[1] and draw(st.booleans()):
            g.col_widths = {"A": float(draw(st.integers(5, 60)))}
        sheets.append(g)
    return WorkbookModel(sheets, numfmts)


def ranges(shape: tuple[int, int]) -> st.SearchStrategy[tuple[int, int, int, int]]:
    """Draw ``(r0, r1, c0, c1)`` half-open bounds of a non-empty range inside a sheet
    of ``shape``, plus up to two rows/cols of overhang past the edge.
    """
    nrows, ncols = shape

    @st.composite
    def _draw(draw: st.DrawFn) -> tuple[int, int, int, int]:
        r0 = draw(st.integers(0, nrows + 1))
        r1 = draw(st.integers(r0 + 1, nrows + 3))
        c0 = draw(st.integers(0, ncols + 1))
        c1 = draw(st.integers(c0 + 1, ncols + 3))
        return r0, r1, c0, c1

    return _draw()


def arrays_equal(a: np.ndarray, b: np.ndarray) -> bool:
    """Exact equality of two slot arrays, NaN equal to NaN."""
    if a.shape != b.shape:
        return False
    if a.dtype.kind == "f":
        return bool(np.array_equal(a, b, equal_nan=True))
    return bool(np.array_equal(a, b))
