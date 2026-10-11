"""The in-memory form every reader and writer meets in.

``.xlsx`` → :class:`WorkbookModel` → zarr, and back again. Keeping one neutral form in
the middle means the xlsx side and the zarr side never need to know about each other,
and the round-trip properties in the test suite have one thing to compare.

A :class:`SheetGrid` is a set of aligned numpy arrays, one per :class:`~.kinds.Slot`,
always anchored at ``A1``. Anchoring at ``A1`` costs nothing in zarr (leading empty
chunks are never written) and is what pandas' readers assume, so row ``i`` of the grid
is row ``i + 1`` of the sheet, with no offset to carry around.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from .kinds import FILL, KIND_SLOT, SLOT_DTYPES, CellKind, Slot, encode

__all__ = [
    "GENERAL",
    "NumberFormats",
    "SheetGrid",
    "WorkbookModel",
    "as_ints",
    "decode",
    "value_slots",
]

#: The number format every cell has unless told otherwise. Always index 0.
GENERAL = "General"

#: The slots that carry a cell's *value*, as opposed to its formula or format.
VALUE_SLOTS = (Slot.NUMBER, Slot.STRING, Slot.BOOL, Slot.TEMPORAL)


class NumberFormats:
    """The workbook's table of distinct number-format strings, ``General`` first.

    Each sheet's ``numfmt`` array holds indices into this table, so a format string like
    ``"yyyy-mm-dd hh:mm:ss"`` is stored once per workbook, not once per cell.

    Parameters
    ----------
    formats : iterable of str, optional
        An existing table to start from; ``General`` is prepended if absent.
    """

    def __init__(self, formats: Iterable[str] = ()) -> None:
        self._formats: list[str] = [GENERAL]
        self._index: dict[str, int] = {GENERAL: 0}
        for f in formats:
            self.index(f)

    def index(self, fmt: str | None) -> int:
        """Return the index of ``fmt``, adding it to the table if it's new.

        Parameters
        ----------
        fmt : str or None
            A number-format string; ``None`` means ``General``.

        Returns
        -------
        int
            Its position in the table.
        """
        if fmt is None:
            return 0
        if (i := self._index.get(fmt)) is None:
            i = self._index[fmt] = len(self._formats)
            self._formats.append(fmt)
        return i

    def __getitem__(self, i: int) -> str:
        return self._formats[i]

    def __len__(self) -> int:
        return len(self._formats)

    def tolist(self) -> list[str]:
        """Return the table as a plain list, ``General`` first.

        Returns
        -------
        list of str
            The formats in index order.
        """
        return list(self._formats)


def _empty(slot: str, shape: tuple[int, int]) -> npt.NDArray[Any]:
    return np.full(shape, FILL[slot], dtype=SLOT_DTYPES[slot])


@dataclass
class SheetGrid:
    """One sheet: aligned typed arrays plus the sheet-level metadata we keep.

    Attributes
    ----------
    name : str
        The sheet's name as Excel shows it.
    arrays : dict
        One 2D array per :class:`~.kinds.Slot`, all the same shape.
    merged : list of str
        Merged ranges, A1-style (``"B2:C3"``).
    col_widths : dict
        Column letter → width, for the columns that have one set.
    freeze_panes : str or None
        The top-left unfrozen cell, if panes are frozen.
    """

    name: str
    arrays: dict[str, npt.NDArray[Any]]
    merged: list[str] = field(default_factory=list)
    col_widths: dict[str, float] = field(default_factory=dict)
    freeze_panes: str | None = None

    @property
    def shape(self) -> tuple[int, int]:
        """Return the ``(rows, cols)`` extent, anchored at ``A1``.

        Returns
        -------
        tuple of int
            ``(rows, cols)``, anchored at ``A1``.
        """
        r, c = self.arrays[Slot.KIND].shape
        return int(r), int(c)

    @classmethod
    def empty(cls, name: str, shape: tuple[int, int]) -> SheetGrid:
        """Make a sheet of ``shape`` with every cell empty.

        Parameters
        ----------
        name : str
            The sheet's name.
        shape : tuple of int
            ``(rows, cols)``.

        Returns
        -------
        SheetGrid
            All slots at their fill values.
        """
        return cls(name, {slot: _empty(slot, shape) for slot in SLOT_DTYPES})

    @classmethod
    def from_values(
        cls,
        name: str,
        values: Sequence[Sequence[object]],
        formulas: Sequence[Sequence[str | None]] | None = None,
        number_formats: Mapping[tuple[int, int], int] | None = None,
    ) -> SheetGrid:
        """Build a sheet from rows of Python values (ragged rows are padded).

        Parameters
        ----------
        name : str
            The sheet's name.
        values : sequence of sequences
            Row-major cell values; anything :func:`~.kinds.encode` accepts.
        formulas : sequence of sequences, optional
            Formula text (``"=A1+1"``) aligned with ``values``; ``None`` or ``""`` for
            none. A formula cell's entry in ``values`` is its cached result.
        number_formats : mapping, optional
            ``(row, col)`` → index into the workbook's :class:`NumberFormats`.

        Returns
        -------
        SheetGrid
            The encoded sheet.
        """
        nrows = len(values)
        ncols = max((len(r) for r in values), default=0)
        if formulas is not None:
            nrows = max(nrows, len(formulas))
            ncols = max(ncols, max((len(r) for r in formulas), default=0))
        grid = cls.empty(name, (nrows, ncols))
        for i, row in enumerate(values):
            for j, v in enumerate(row):
                grid.set_cell(i, j, v)
        if formulas is not None:
            f = grid.arrays[Slot.FORMULA]
            for i, frow in enumerate(formulas):
                for j, text in enumerate(frow):
                    if text:
                        f[i, j] = text
        if number_formats:
            nf = grid.arrays[Slot.NUMFMT]
            for (i, j), idx in number_formats.items():
                nf[i, j] = idx
        return grid

    def set_cell(self, row: int, col: int, value: object) -> None:
        """Encode ``value`` into the cell at ``(row, col)``, clearing its other slots.

        Parameters
        ----------
        row : int
            Zero-based row.
        col : int
            Zero-based column.
        value : object
            Anything :func:`~.kinds.encode` accepts.
        """
        kind, stored = encode(value)
        self.arrays[Slot.KIND][row, col] = kind
        for slot in VALUE_SLOTS:
            self.arrays[slot][row, col] = FILL[slot]
        if kind is not CellKind.EMPTY:
            self.arrays[KIND_SLOT[kind]][row, col] = stored

    def values(self) -> npt.NDArray[np.object_]:
        """Decode the whole sheet to an object array of Python values.

        Returns
        -------
        numpy.ndarray
            ``None`` for empty cells.
        """
        return decode(self.arrays)

    def region(self, rows: slice, cols: slice) -> SheetGrid:
        """Return the sub-grid at ``rows, cols`` (views, not copies).

        Parameters
        ----------
        rows : slice
            Row slice.
        cols : slice
            Column slice.

        Returns
        -------
        SheetGrid
            The region, with the metadata dropped (it no longer refers to ``A1``).
        """
        return SheetGrid(self.name, {k: a[rows, cols] for k, a in self.arrays.items()})


@dataclass
class WorkbookModel:
    """A whole workbook: the sheets in tab order, plus workbook-level metadata.

    Attributes
    ----------
    sheets : list of SheetGrid
        In tab order.
    number_formats : NumberFormats
        The table each sheet's ``numfmt`` indexes into.
    defined_names : dict
        Workbook-scoped defined names → their formula text (``"Sheet1!$A$1:$B$4"``).
    """

    sheets: list[SheetGrid]
    number_formats: NumberFormats = field(default_factory=NumberFormats)
    defined_names: dict[str, str] = field(default_factory=dict)

    @property
    def sheet_names(self) -> list[str]:
        """Return the sheet names, in tab order.

        Returns
        -------
        list of str
            Sheet names, in tab order.
        """
        return [s.name for s in self.sheets]

    def __getitem__(self, name: str) -> SheetGrid:
        for s in self.sheets:
            if s.name == name:
                return s
        raise KeyError(name)


_INT64_SAFE = 2.0**63


def as_ints(numbers: npt.NDArray[np.float64]) -> npt.NDArray[np.object_]:
    """Whole-number floats to Python ints, without int64's silent overflow.

    Parameters
    ----------
    numbers : numpy.ndarray
        Finite, whole-valued floats.

    Returns
    -------
    numpy.ndarray
        Object array of ``int``. Values past int64 go through ``int()`` one at a time,
        which is exact for any float.
    """
    out = np.empty(numbers.shape, dtype=object)
    fits = np.abs(numbers) < _INT64_SAFE
    out[fits] = numbers[fits].astype(np.int64).astype(object)
    if not fits.all():
        out[~fits] = [int(x) for x in numbers[~fits]]
    return out


def _times(us: npt.NDArray[np.int64]) -> list[dt.time]:
    return [(dt.datetime.min + dt.timedelta(microseconds=int(u))).time() for u in us]


def value_slots(kind: npt.NDArray[Any]) -> list[str]:
    """Return the slots :func:`decode` reads for a region with these ``kind`` codes.

    A reader can fetch them all at once before decoding, rather than one by one as
    :func:`decode` asks for them.

    Parameters
    ----------
    kind : numpy.ndarray
        The region's ``kind`` array.

    Returns
    -------
    list of str
        Slot names, in the order :func:`decode` asks for them.
    """
    present = sorted(set(np.unique(kind).tolist()) - {CellKind.EMPTY})
    return list(dict.fromkeys(KIND_SLOT[CellKind(code)] for code in present))


def decode(arrays: Mapping[str, npt.NDArray[Any]]) -> npt.NDArray[np.object_]:
    """Decode aligned slot arrays back to an object array of Python cell values.

    Only the slots that the ``kind`` array actually uses are touched, so a caller can
    pass a lazy mapping and pay for reading a slot only when the region needs it.

    Parameters
    ----------
    arrays : mapping
        Slot name → array (or anything that slices to one), all the same shape. Must
        contain ``kind``; the value slots are looked up only if needed.

    Returns
    -------
    numpy.ndarray
        Object array of ``None``/``int``/``float``/``str``/``bool``/``datetime``/
        ``date``/``time``/``timedelta``.
    """
    kind = np.asarray(arrays[Slot.KIND])
    out = np.full(kind.shape, None, dtype=object)
    present = set(np.unique(kind).tolist()) - {CellKind.EMPTY}
    if not present:
        return out
    cache: dict[str, npt.NDArray[Any]] = {}

    def slot(name: str) -> npt.NDArray[Any]:
        if name not in cache:
            cache[name] = np.asarray(arrays[name])
        return cache[name]

    for code in sorted(present):
        k = CellKind(code)
        mask = kind == code
        raw = slot(KIND_SLOT[k])[mask]
        if k is CellKind.INT:
            decoded: Any = as_ints(raw)
        elif k is CellKind.FLOAT:
            decoded = raw.astype(object)
        elif k in (CellKind.STRING, CellKind.ERROR):
            decoded = raw.astype(object)  # StringDType -> Python str, no loop
        elif k is CellKind.BOOL:
            decoded = raw.astype(object)
        elif k is CellKind.DATETIME:
            decoded = raw.astype("datetime64[us]").astype(object)
        elif k is CellKind.DATE:
            decoded = (
                raw.astype("datetime64[us]").astype("datetime64[D]").astype(object)
            )
        elif k is CellKind.TIME:
            decoded = np.empty(raw.shape, dtype=object)
            decoded[:] = _times(raw)
        elif k is CellKind.DURATION:
            decoded = raw.astype("timedelta64[us]").astype(object)
        else:  # pragma: no cover - every CellKind is handled above
            raise AssertionError(k)
        out[mask] = decoded
    return out
