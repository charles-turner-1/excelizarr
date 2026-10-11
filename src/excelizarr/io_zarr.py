"""The zarr side: a :class:`~.model.WorkbookModel` as a hierarchy of chunked arrays.

Layout (format 1)::

    <root>/                   attrs["excelizarr"]: format, sheets [{name, key}],
    │                                              number_formats, defined_names
    └── sheets/
        └── <key>/            attrs["excelizarr"]: name, merged, col_widths, freeze_panes
            ├── kind          uint8    CellKind codes
            ├── number        float64  ints and floats
            ├── string        str      strings and error literals
            ├── bool          bool
            ├── temporal      int64    microseconds (see ``kinds``)
            ├── formula       str      formula text, "" for none
            └── numfmt        uint16   index into the root's number_formats

``<key>`` is the sheet name, percent-escaped just enough to be a legal zarr node name.
The real name lives in the root's ``sheets`` list, which is also the tab order.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any
from urllib.parse import quote

import numpy as np
import numpy.typing as npt
import zarr
from zarr.storage import StoreLike

from .exceptions import FormatVersionError
from .kinds import FILL, SLOT_DTYPES, Slot
from .model import NumberFormats, SheetGrid, WorkbookModel

__all__ = [
    "DEFAULT_CHUNKS",
    "FORMAT_VERSION",
    "LazySlots",
    "open_root",
    "read_model",
    "sheet_key",
    "write_model",
    "write_sheet",
]

FORMAT_VERSION = 1
ATTR = "excelizarr"

#: Rows × columns per chunk. Sheets are long and narrow, and most reads want whole
#: columns, so chunks are tall. Clipped to the sheet's shape.
DEFAULT_CHUNKS = (1024, 256)


def sheet_key(name: str) -> str:
    """Escape a sheet name into a legal zarr node name.

    Parameters
    ----------
    name : str
        The sheet name.

    Returns
    -------
    str
        ``name`` with ``/`` and ``%`` (and a leading ``.`` or ``__``) percent-escaped;
        readable for every name Excel would actually let you type.
    """
    key = quote(name, safe=" !\"#$&'()*+,-.;<=>?@[\\]^_`{|}~")
    if key.startswith(("__", ".")):
        key = f"%{ord(key[0]):02X}{key[1:]}"
    return key


def _chunks(shape: tuple[int, int], chunks: tuple[int, int]) -> tuple[int, int]:
    return (max(1, min(chunks[0], shape[0])), max(1, min(chunks[1], shape[1])))


def _shards(
    shape: tuple[int, int], chunks: tuple[int, int], shards: tuple[int, int]
) -> tuple[int, int]:
    """Fit a whole number of chunks per shard, and never more than the sheet needs."""

    def one(n: int, c: int, s: int) -> int:
        return c * max(1, min(s // c, -(-n // c)))

    return one(shape[0], chunks[0], shards[0]), one(shape[1], chunks[1], shards[1])


def write_sheet(
    parent: zarr.Group,
    grid: SheetGrid,
    chunks: tuple[int, int] = DEFAULT_CHUNKS,
    shards: tuple[int, int] | None = None,
) -> zarr.Group:
    """Write one sheet as a group of typed arrays under ``parent``.

    Parameters
    ----------
    parent : zarr.Group
        The ``sheets`` group.
    grid : SheetGrid
        The sheet to write.
    chunks : tuple of int
        ``(rows, cols)`` per chunk; clipped to the sheet.
    shards : tuple of int, optional
        ``(rows, cols)`` per shard, a multiple of ``chunks``. Sharding packs many chunks
        into one object, which object stores much prefer for big sheets.

    Returns
    -------
    zarr.Group
        The sheet's group.
    """
    # attributes go in at creation: every node's metadata is written exactly once, which
    # is what lets append-only stores (``ZipStore``) hold a workbook at all
    meta = {
        "name": grid.name,
        "merged": grid.merged,
        "col_widths": grid.col_widths,
        "freeze_panes": grid.freeze_panes,
    }
    g = parent.create_group(
        sheet_key(grid.name), overwrite=True, attributes={ATTR: meta}
    )
    shape = grid.shape
    c = _chunks(shape, chunks)
    s = None if shards is None else _shards(shape, c, shards)
    for slot, dtype in SLOT_DTYPES.items():
        arr = g.create_array(
            slot,
            shape=shape,
            dtype=dtype,
            chunks=c,
            shards=s,
            fill_value=FILL[slot],
            dimension_names=("row", "col"),
        )
        data = grid.arrays[slot]
        if data.size and _has_data(slot, data):
            arr[...] = data
    return g


def _has_data(slot: str, data: npt.NDArray[Any]) -> bool:
    fill = FILL[slot]
    if isinstance(fill, float) and np.isnan(fill):
        return not bool(np.isnan(data).all())
    return bool((data != fill).any())


def write_model(
    model: WorkbookModel,
    store: StoreLike,
    *,
    chunks: tuple[int, int] = DEFAULT_CHUNKS,
    shards: tuple[int, int] | None = None,
    mode: str = "w-",
    storage_options: dict[str, Any] | None = None,
) -> zarr.Group:
    """Write a whole workbook to a zarr store.

    Parameters
    ----------
    model : WorkbookModel
        What to write.
    store : StoreLike
        Anything zarr accepts: a path, an ``s3://`` URL, a ``MemoryStore``, a
        ``ZipStore``, an Icechunk session's ``store``.
    chunks : tuple of int
        ``(rows, cols)`` per chunk.
    shards : tuple of int, optional
        ``(rows, cols)`` per shard; see :func:`write_sheet`.
    mode : {"w-", "w"}
        ``"w-"`` refuses to overwrite an existing store, ``"w"`` replaces it.
    storage_options : dict, optional
        Passed through to fsspec for URL stores.

    Returns
    -------
    zarr.Group
        The root group.

    Raises
    ------
    ValueError
        If ``mode`` is neither ``"w"`` nor ``"w-"``.
    """
    if mode not in ("w", "w-"):
        raise ValueError(f"mode must be 'w' or 'w-', not {mode!r}")
    meta = {
        "format": FORMAT_VERSION,
        "sheets": [{"name": s.name, "key": sheet_key(s.name)} for s in model.sheets],
        "number_formats": model.number_formats.tolist(),
        "defined_names": model.defined_names,
    }
    root = zarr.create_group(
        store,
        overwrite=mode == "w",
        attributes={ATTR: meta},
        storage_options=storage_options,
    )
    sheets = root.create_group("sheets")
    for grid in model.sheets:
        write_sheet(sheets, grid, chunks=chunks, shards=shards)
    return root


def open_root(
    store: StoreLike | zarr.Group,
    mode: str = "r",
    storage_options: dict[str, Any] | None = None,
) -> zarr.Group:
    """Open a workbook store's root group and check it is one we can read.

    Parameters
    ----------
    store : StoreLike or zarr.Group
        The store, or an already-open root group.
    mode : str
        zarr's open mode.
    storage_options : dict, optional
        Passed through to fsspec for URL stores.

    Returns
    -------
    zarr.Group
        The root group.

    Raises
    ------
    FormatVersionError
        If the store isn't an excelizarr workbook, or is from a newer format.
    """
    if isinstance(store, zarr.Group):
        root = store
    else:
        root = zarr.open_group(store, mode=mode, storage_options=storage_options)  # type: ignore[arg-type]
    meta = root.attrs.get(ATTR)
    if not isinstance(meta, dict) or "format" not in meta:
        raise FormatVersionError("not an excelizarr workbook (no 'excelizarr' attrs)")
    if meta["format"] > FORMAT_VERSION:
        raise FormatVersionError(
            f"workbook is format {meta['format']}; this excelizarr reads up to {FORMAT_VERSION}"
        )
    return root


class LazySlots(Mapping[str, npt.NDArray[Any]]):
    """A sheet group's slots, sliced to a region and read only when first asked for.

    :func:`~.model.decode` looks a slot up only if the region holds a cell of a kind
    that lives there, so a numbers-only region never touches the ``string`` array.

    Parameters
    ----------
    group : zarr.Group
        The sheet's group.
    rows : slice
        Row slice of the region.
    cols : slice
        Column slice of the region.
    """

    def __init__(self, group: zarr.Group, rows: slice, cols: slice) -> None:
        self._group = group
        self._sel = (rows, cols)
        self._cache: dict[str, npt.NDArray[Any]] = {}

    def __getitem__(self, slot: str) -> npt.NDArray[Any]:
        if slot not in self._cache:
            if slot not in SLOT_DTYPES:
                raise KeyError(slot)
            arr = self._group[slot]
            assert isinstance(arr, zarr.Array)
            self._cache[slot] = np.asarray(arr[self._sel])
        return self._cache[slot]

    def __iter__(self) -> Iterator[str]:
        return iter(SLOT_DTYPES)

    def __len__(self) -> int:
        return len(SLOT_DTYPES)


def _read_grid(group: zarr.Group) -> SheetGrid:
    meta = group.attrs[ATTR]
    assert isinstance(meta, dict)
    arrays = {}
    for slot in SLOT_DTYPES:
        arr = group[slot]
        assert isinstance(arr, zarr.Array)
        arrays[slot] = np.asarray(arr[...])
    # zarr hands strings back as StringDType already; normalise in case a store came
    # from somewhere that wrote them differently
    for slot in (Slot.STRING, Slot.FORMULA):
        arrays[slot] = arrays[slot].astype(SLOT_DTYPES[slot])
    return SheetGrid(
        name=str(meta["name"]),
        arrays=arrays,
        merged=list(meta.get("merged", [])),
        col_widths=dict(meta.get("col_widths", {})),
        freeze_panes=meta.get("freeze_panes"),
    )


def read_model(
    store: StoreLike | zarr.Group, storage_options: dict[str, Any] | None = None
) -> WorkbookModel:
    """Read a whole workbook store back into memory.

    Parameters
    ----------
    store : StoreLike or zarr.Group
        The store, or its open root group.
    storage_options : dict, optional
        Passed through to fsspec for URL stores.

    Returns
    -------
    WorkbookModel
        Every sheet, in tab order.
    """
    root = open_root(store, storage_options=storage_options)
    meta = root.attrs[ATTR]
    assert isinstance(meta, dict)
    sheets_group = root["sheets"]
    assert isinstance(sheets_group, zarr.Group)
    sheets = []
    for entry in meta["sheets"]:
        g = sheets_group[entry["key"]]
        assert isinstance(g, zarr.Group)
        sheets.append(_read_grid(g))
    return WorkbookModel(
        sheets,
        NumberFormats(meta["number_formats"]),
        dict(meta.get("defined_names", {})),
    )
