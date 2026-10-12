"""Lazy, chunk-at-a-time access to a workbook store.

:class:`ZarrWorkbook` opens nothing but the root group, whose consolidated metadata
(format 2) describes every array, so opening a sheet or an array costs no reads.
Indexing a :class:`ZarrSheet` with a range reads the ``kind`` chunks that range
overlaps, then the chunks of every value slot those kinds use, all at once. So
``wb["Inputs!B2:D10"]`` against an ``s3://`` store costs two round trips no matter
how big the sheet is, or how many slots the range touches.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import numpy as np
import numpy.typing as npt
import zarr

from ._refs import RangeRef, format_range, parse_range
from .exceptions import InvalidReferenceError
from .io_zarr import (
    ATTR,
    LazySlots,
    OpenArrays,
    _gather,
    _read_grids,
    _reconsolidate,
    open_root,
)
from .kinds import FILL, KIND_SLOT, SLOT_DTYPES, CellKind, Slot, encode
from .model import VALUE_SLOTS, NumberFormats, WorkbookModel, decode, value_slots

__all__ = ["ZarrSheet", "ZarrWorkbook"]

Key = str | tuple[int | slice, int | slice]


class ZarrWorkbook:
    """A workbook in a zarr store, read lazily.

    Usually made by :func:`excelizarr.open_workbook` or :func:`excelizarr.xlsx_to_zarr`.

    Parameters
    ----------
    root : zarr.Group
        The workbook's root group.
    """

    def __init__(self, root: zarr.Group) -> None:
        self.root = open_root(root)
        meta = self.root.attrs[ATTR]
        assert isinstance(meta, dict)
        self._meta = meta
        self._keys = {s["name"]: s["key"] for s in meta["sheets"]}
        # one ZarrSheet per sheet: each holds its open arrays, so every handle to a
        # sheet sees it grow when any of them writes past its edge
        self._sheets: dict[str, ZarrSheet] = {}

    @property
    def sheet_names(self) -> list[str]:
        """Return the sheet names, in tab order.

        Returns
        -------
        list of str
            Sheet names, in tab order.
        """
        return list(self._keys)

    @property
    def number_formats(self) -> NumberFormats:
        """Return the workbook's number-format table.

        Returns
        -------
        NumberFormats
            The table every sheet's ``numfmt`` indexes into.
        """
        return NumberFormats(self._meta["number_formats"])

    @property
    def defined_names(self) -> dict[str, str]:
        """Return the workbook-scoped defined names, mapped to their formula text.

        Returns
        -------
        dict
            Name → formula text (``"Sheet1!$A$1:$B$4"``).
        """
        return dict(self._meta.get("defined_names", {}))

    def sheet(self, name_or_index: str | int) -> ZarrSheet:
        """Return one sheet, by name or by tab position.

        Parameters
        ----------
        name_or_index : str or int
            A sheet name, or a zero-based tab position.

        Returns
        -------
        ZarrSheet
            The sheet.

        Raises
        ------
        KeyError
            If there is no such sheet.
        """
        if isinstance(name_or_index, int):
            try:
                name = self.sheet_names[name_or_index]
            except IndexError:
                raise KeyError(name_or_index) from None
        else:
            name = name_or_index
        if name not in self._keys:
            raise KeyError(name)
        if name not in self._sheets:
            group = self.root[f"sheets/{self._keys[name]}"]
            assert isinstance(group, zarr.Group)
            self._sheets[name] = ZarrSheet(group, name, self)
        return self._sheets[name]

    def __getitem__(self, key: str | int) -> Any:
        """Return a sheet (``wb["Inputs"]``, ``wb[0]``) or a range's values.

        ``wb["Inputs!B2:D10"]`` is ``wb["Inputs"]["B2:D10"]``. A name that is both a
        sheet and a parseable reference is a sheet.

        Parameters
        ----------
        key : str or int
            A sheet name, a tab position, or a sheet-qualified A1 reference.

        Returns
        -------
        ZarrSheet or object
            The sheet, or the range's values as :meth:`ZarrSheet.__getitem__` gives them.

        Raises
        ------
        KeyError
            If there is no such sheet, or ``key`` is neither a sheet nor a reference.
        """
        if isinstance(key, int) or key in self._keys:
            return self.sheet(key)
        try:
            ref = parse_range(key)
        except InvalidReferenceError:
            raise KeyError(key) from None
        if ref.sheet is None:
            raise KeyError(key)
        return self.sheet(ref.sheet)[ref]

    def __iter__(self) -> Iterator[ZarrSheet]:
        return (self.sheet(n) for n in self.sheet_names)

    def __len__(self) -> int:
        return len(self._keys)

    def __repr__(self) -> str:
        sheets = ", ".join(f"{s.name!r} {s.shape[0]}x{s.shape[1]}" for s in self)
        return f"<ZarrWorkbook [{sheets}]>"

    def to_model(self) -> WorkbookModel:
        """Read the whole workbook into memory.

        Returns
        -------
        WorkbookModel
            Every sheet, in tab order.
        """
        return WorkbookModel(
            _read_grids([s.group for s in self]),
            self.number_formats,
            self.defined_names,
        )

    def to_xlsx(self, path: str | os.PathLike[str]) -> None:
        """Write the workbook back out as an ``.xlsx`` file.

        Parameters
        ----------
        path : str or os.PathLike
            Where to write it.
        """
        from .io_xlsx import dump_xlsx

        dump_xlsx(self.to_model(), path)


def _as_slices(
    key: Key | RangeRef, shape: tuple[int, int]
) -> tuple[slice, slice, bool]:
    """Normalise an indexing key to ``(rows, cols, scalar)``."""
    if isinstance(key, str):
        key = parse_range(key)
    if isinstance(key, RangeRef):
        rows, cols = key.resolve(shape)
        r0, r1, c0, c1 = key.row_start, key.row_stop, key.col_start, key.col_stop
        scalar = (
            r0 is not None
            and r1 is not None
            and c0 is not None
            and c1 is not None
            and (r1 - r0, c1 - c0) == (1, 1)
        )
        return rows, cols, scalar
    if not (isinstance(key, tuple) and len(key) == 2):
        raise TypeError(f"index a sheet with an A1 string or (rows, cols), not {key!r}")
    r, c = key
    scalar = isinstance(r, int) and isinstance(c, int)

    def to_slice(k: int | slice, n: int) -> slice:
        if isinstance(k, int):
            k = k + n if k < 0 else k
            return slice(k, k + 1)
        start, stop, step = k.indices(n)
        if step != 1:
            raise ValueError("sheet slices must be contiguous (step 1)")
        return slice(start, stop)

    return to_slice(r, shape[0]), to_slice(c, shape[1]), scalar


class ZarrSheet:
    """One sheet of a :class:`ZarrWorkbook`.

    Index it like a spreadsheet (``sheet["B2:D10"]``, ``sheet["A:C"]``, ``sheet["B2"]``)
    or like an array (``sheet[1:10, 1:4]``). Ranges come back as object arrays of
    Python values with ``None`` for empty cells; a single cell comes back as its value.

    Parameters
    ----------
    group : zarr.Group
        The sheet's group.
    name : str
        The sheet's name.
    workbook : ZarrWorkbook
        The workbook it belongs to.
    """

    def __init__(self, group: zarr.Group, name: str, workbook: ZarrWorkbook) -> None:
        self.group = group
        self.name = name
        self.workbook = workbook
        self._arrays = OpenArrays(group)

    def _array(self, slot: str) -> zarr.Array[Any]:
        return self._arrays[slot]

    @property
    def shape(self) -> tuple[int, int]:
        """Return the ``(rows, cols)`` extent, anchored at ``A1``.

        Returns
        -------
        tuple of int
            ``(rows, cols)``, anchored at ``A1``.
        """
        r, c = self._array(Slot.KIND).shape
        return int(r), int(c)

    @property
    def dimensions(self) -> str:
        """Return the used range in A1 form (``"A1:D20"``), as Excel would report it.

        Returns
        -------
        str
            The used range in A1 form (``"A1:D20"``), as Excel would report it.
        """
        r, c = self.shape
        return format_range(0, r, 0, c) if r and c else "A1"

    @property
    def merged(self) -> list[str]:
        """Return the merged ranges.

        Returns
        -------
        list of str
            The merged ranges, A1-style.
        """
        return list(self._meta().get("merged", []))

    def _meta(self) -> dict[str, Any]:
        meta = self.group.attrs[ATTR]
        assert isinstance(meta, dict)
        return meta

    def slots(self, key: Key | RangeRef) -> LazySlots:
        """Return the typed arrays behind a range, each read only when first touched.

        Parameters
        ----------
        key : str, RangeRef or tuple
            The range.

        Returns
        -------
        LazySlots
            Slot name → numpy array for the region.
        """
        rows, cols, _ = _as_slices(key, self.shape)
        return LazySlots(self._arrays, rows, cols)

    def __getitem__(self, key: Key | RangeRef) -> Any:
        """Read a cell or a range.

        Parameters
        ----------
        key : str, RangeRef or tuple
            An A1 reference (``"B2"``, ``"B2:D10"``, ``"A:C"``), or ``(rows, cols)``
            with ints and/or contiguous slices.

        Returns
        -------
        object or numpy.ndarray
            A single cell's value, or an object array of the range's values, ``None``
            for empty cells. The parts of a range past the used area are dropped.
        """
        rows, cols, scalar = _as_slices(key, self.shape)
        if scalar and (rows.start >= rows.stop or cols.start >= cols.stop):
            return None  # a single cell beyond the used range is just empty
        slots = LazySlots(self._arrays, rows, cols)
        slots.prefetch(value_slots(slots[Slot.KIND]))
        out = decode(slots)
        return out[0, 0] if scalar else out

    def formulas(self, key: Key | RangeRef = "A:XFD") -> npt.NDArray[np.object_]:
        """Return the formula text for a range, ``None`` where a cell has no formula.

        Parameters
        ----------
        key : str, RangeRef or tuple
            The range; the whole sheet by default.

        Returns
        -------
        numpy.ndarray
            Object array of ``str`` or ``None``.
        """
        f = self.slots(key)[Slot.FORMULA]
        out = f.astype(object)
        out[f == ""] = None
        return out

    def number_formats(self, key: Key | RangeRef = "A:XFD") -> npt.NDArray[np.object_]:
        """Return the number-format strings for a range.

        Parameters
        ----------
        key : str, RangeRef or tuple
            The range; the whole sheet by default.

        Returns
        -------
        numpy.ndarray
            Object array of format strings (``"General"`` where none was set).
        """
        idx = self.slots(key)[Slot.NUMFMT]
        table = np.array(self.workbook.number_formats.tolist(), dtype=object)
        return table[idx] if idx.size else idx.astype(object)

    def __setitem__(self, key: Key, value: Any) -> None:
        """Write a value (or a 2D block of values) into the sheet, in place.

        Only the chunks the target touches are rewritten. Writing past the current
        shape grows the sheet. A string starting with ``"="`` is stored as a formula
        with no cached value, which is what openpyxl would have done with it too.

        Parameters
        ----------
        key : str or tuple
            A closed A1 reference (``"B2"``, ``"A5:B6"``), or ``(rows, cols)``.
        value : object
            One value, broadcast over the target, or a 2D block of its shape.

        Raises
        ------
        InvalidReferenceError
            If ``key`` is an open range like ``"A:C"``.
        """
        if isinstance(key, str):
            ref = parse_range(key)
            if None in (ref.row_start, ref.col_start):
                raise InvalidReferenceError(f"can't assign to an open range: {key!r}")
            rows, cols, _ = _as_slices(ref, (ref.row_stop or 0, ref.col_stop or 0))
        else:
            # integers may point past the edge (and grow the sheet); slices are clipped
            rows, cols, _ = _as_slices(key, self.shape)
        block = np.empty((rows.stop - rows.start, cols.stop - cols.start), dtype=object)
        block[...] = value if np.ndim(value) == 0 else np.asarray(value, dtype=object)
        need = (max(self.shape[0], rows.stop), max(self.shape[1], cols.stop))
        if need != self.shape:
            arrays = [self._array(slot) for slot in SLOT_DTYPES]
            _gather(a.async_array.resize(need) for a in arrays)
            # the root's consolidated copy of these arrays' metadata is stale now
            self.workbook.root = _reconsolidate(self.workbook.root)

        enc: dict[str, npt.NDArray[Any]] = {
            slot: np.full(block.shape, FILL[slot], dtype=SLOT_DTYPES[slot])
            for slot in (Slot.KIND, Slot.FORMULA, *VALUE_SLOTS)
        }
        for (i, j), v in np.ndenumerate(block):
            if isinstance(v, str) and v.startswith("="):
                enc[Slot.FORMULA][i, j] = v
                continue
            kind, stored = encode(v)
            enc[Slot.KIND][i, j] = kind
            if kind is not CellKind.EMPTY:
                enc[KIND_SLOT[kind]][i, j] = stored
        _gather(
            self._array(slot).async_array.setitem((rows, cols), data)
            for slot, data in enc.items()
        )

    def to_pandas(self, **kwargs: Any) -> Any:
        """Read this sheet into a DataFrame, exactly as ``pandas.read_excel`` would.

        Parameters
        ----------
        **kwargs
            Anything ``pandas.read_excel`` takes besides ``io`` and ``sheet_name``:
            ``header``, ``index_col``, ``usecols``, ``skiprows``, ``nrows``, ...

        Returns
        -------
        pandas.DataFrame
            The sheet as a frame.
        """
        from ._pandas import ZarrExcelReader

        return ZarrExcelReader(self.workbook).parse(sheet_name=self.name, **kwargs)

    def __repr__(self) -> str:
        return f"<ZarrSheet {self.name!r} {self.dimensions}>"
