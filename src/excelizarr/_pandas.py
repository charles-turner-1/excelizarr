"""``pandas.read_excel``, but the "Excel file" is a zarr store.

Rather than re-implementing ``read_excel``'s argument handling (``header``,
``index_col``, ``usecols``, ``skiprows``, ``nrows``, ``parse_dates``, ``dtype``, ...),
this plugs a zarr-backed sheet source into pandas' own ``BaseExcelReader``. pandas then
does everything it does for a real ``.xlsx`` after the cells have been read, so the
result matches ``pd.read_excel(path, engine="openpyxl")`` by construction, and the
test suite checks that it does.

The one thing pandas asks of a reader is ``get_sheet_data``: rows of Python values,
converted the way its openpyxl reader converts them (empty → ``""``, error → ``NaN``,
integral numbers → ``int``), with trailing empty cells and rows trimmed. When pandas
only needs the first ``n`` rows (``nrows``), only those rows are read from the store.

``BaseExcelReader`` is pandas-internal API. It has been stable for years, but if a
pandas release moves it, this module is the only place that needs to change.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from pandas.io.excel._base import BaseExcelReader  # type: ignore[attr-defined]

from .io_zarr import LazySlots
from .kinds import CellKind, Slot
from .model import as_ints, decode
from .workbook import ZarrSheet, ZarrWorkbook

__all__ = ["ZarrExcelReader", "sheet_data"]


def sheet_data(
    sheet: ZarrSheet, file_rows_needed: int | None = None
) -> list[list[Any]]:
    """Read a sheet as pandas' openpyxl reader would hand it to its parser.

    Parameters
    ----------
    sheet : ZarrSheet
        The sheet.
    file_rows_needed : int, optional
        Read at most this many rows from the top.

    Returns
    -------
    list of list
        Row-major cell values, rectangular, trailing empties trimmed.
    """
    nrows, ncols = sheet.shape
    if file_rows_needed is not None:
        nrows = min(nrows, file_rows_needed)
    if nrows == 0 or ncols == 0:
        return []
    slots = LazySlots(sheet.group, slice(0, nrows), slice(0, ncols))
    kind = slots[Slot.KIND]
    vals = decode(slots)

    # pandas' openpyxl reader: whole-number floats come back as ints, errors as NaN
    is_float = kind == CellKind.FLOAT
    if is_float.any():
        number = slots[Slot.NUMBER]
        whole = is_float & (number == np.floor(number))
        if whole.any():
            vals[whole] = as_ints(number[whole])
    vals[kind == CellKind.ERROR] = np.nan

    # pandas' openpyxl reader spells an empty cell "", and trims on that
    empty = kind == CellKind.EMPTY
    vals[empty] = ""

    filled = ~empty
    if not filled.any():
        return []
    last_row = int(np.flatnonzero(filled.any(axis=1))[-1]) + 1
    last_col = int(np.flatnonzero(filled.any(axis=0))[-1]) + 1
    rows: list[list[Any]] = vals[:last_row, :last_col].tolist()
    return rows


class ZarrExcelReader(BaseExcelReader[ZarrWorkbook]):
    """A pandas Excel reader whose "file" is a :class:`~.workbook.ZarrWorkbook`.

    Parameters
    ----------
    workbook : ZarrWorkbook
        The workbook to read sheets from.
    """

    def __init__(self, workbook: ZarrWorkbook) -> None:
        super().__init__(workbook)

    @property
    def _workbook_class(self) -> type[ZarrWorkbook]:
        return ZarrWorkbook

    def load_workbook(
        self, filepath_or_buffer: Any, engine_kwargs: Any
    ) -> ZarrWorkbook:
        """Never called: the workbook is always handed over already open.

        Parameters
        ----------
        filepath_or_buffer : object
            Unused.
        engine_kwargs : object
            Unused.

        Returns
        -------
        ZarrWorkbook
            Never returns.

        Raises
        ------
        TypeError
            Always.
        """
        raise TypeError("ZarrExcelReader takes an open ZarrWorkbook")

    @property
    def sheet_names(self) -> list[str]:
        """Return the workbook's sheet names, in tab order.

        Returns
        -------
        list of str
            Sheet names, in tab order.
        """
        book: ZarrWorkbook = self.book
        return book.sheet_names

    def get_sheet_by_name(self, name: str) -> ZarrSheet:
        """Return a sheet by name, with pandas' error for a missing one.

        Parameters
        ----------
        name : str
            The sheet name.

        Returns
        -------
        ZarrSheet
            The sheet.
        """
        self.raise_if_bad_sheet_by_name(name)
        book: ZarrWorkbook = self.book
        return book.sheet(name)

    def get_sheet_by_index(self, index: int) -> ZarrSheet:
        """Return a sheet by tab position, with pandas' error for a missing one.

        Parameters
        ----------
        index : int
            Zero-based tab position.

        Returns
        -------
        ZarrSheet
            The sheet.
        """
        self.raise_if_bad_sheet_by_index(index)
        book: ZarrWorkbook = self.book
        return book.sheet(index)

    def get_sheet_data(
        self, sheet: ZarrSheet, file_rows_needed: int | None = None
    ) -> list[list[Any]]:
        """Rows of Python values for pandas' parser; see :func:`sheet_data`.

        Parameters
        ----------
        sheet : ZarrSheet
            The sheet.
        file_rows_needed : int, optional
            Read at most this many rows from the top.

        Returns
        -------
        list of list
            Row-major cell values.
        """
        return sheet_data(sheet, file_rows_needed)
