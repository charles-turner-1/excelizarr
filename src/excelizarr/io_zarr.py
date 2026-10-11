"""The zarr side: a :class:`~.model.WorkbookModel` as a hierarchy of chunked arrays.

Layout (format 2)::

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

From format 2, the root also carries zarr's consolidated metadata: every node's
metadata, folded into the root's ``zarr.json``. Opening the root then opens everything,
so a range read on an object store costs its chunks and nothing else. Every node still
has its own ``zarr.json`` too, so a reader that ignores consolidated metadata (it isn't
in the zarr v3 spec yet) still works. Icechunk stores skip it: Icechunk keeps its own
metadata, and opening a node there is already cheap.

Metadata is still written exactly once per node, which is what lets append-only stores
(``ZipStore``) hold a workbook: :func:`write_model` builds the whole hierarchy's
metadata in memory first, consolidates it there, then writes every node in one go
before any chunk.

zarr's synchronous API blocks on each call until its event loop has finished the I/O,
so a loop over the slots pays one round trip per slot. :func:`_concurrently` runs those
calls from threads instead, so they overlap, and reading or writing all seven slots
costs about one round trip rather than seven.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, TypeVar
from urllib.parse import quote

import numpy as np
import numpy.typing as npt
import zarr
from zarr.storage import StoreLike, StorePath

from .exceptions import FormatVersionError
from .kinds import FILL, SLOT_DTYPES, Slot
from .model import NumberFormats, SheetGrid, WorkbookModel

T = TypeVar("T")

__all__ = [
    "DEFAULT_CHUNKS",
    "FORMAT_VERSION",
    "LazySlots",
    "OpenArrays",
    "open_root",
    "read_model",
    "sheet_key",
    "write_model",
    "write_sheet",
]

#: 2 added consolidated metadata. Format-1 readers must refuse it: their in-place
#: writes can grow an array without updating the root's copy of its metadata.
FORMAT_VERSION = 2
ATTR = "excelizarr"

#: Most blocking zarr calls :func:`_concurrently` runs at once.
MAX_CONCURRENCY = 32

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


def _concurrently(calls: Sequence[Callable[[], T]]) -> list[T]:
    """Run blocking zarr calls at the same time, and return their results in order."""
    if len(calls) <= 1:
        return [call() for call in calls]
    with ThreadPoolExecutor(max_workers=min(len(calls), MAX_CONCURRENCY)) as pool:
        return list(pool.map(lambda call: call(), calls))


def _store_path(
    store: StoreLike, mode: str, storage_options: dict[str, Any] | None
) -> StorePath:
    """Resolve ``store`` the way ``zarr.open_group`` does (a path, a URL, a store...).

    ``make_store_path`` is zarr-internal, but it is the only way to get from a
    ``StoreLike`` to a ``Store`` without writing anything, and :func:`write_model` has
    to write the root last-but-not-twice. If a zarr release moves it, this is the one
    place to change.
    """
    from zarr.storage._common import make_store_path

    path: StorePath = _sync(
        make_store_path(store, mode=mode, storage_options=storage_options)  # type: ignore[arg-type]
    )
    return path


def _sync(coro: Any) -> Any:
    """Run a zarr coroutine on zarr's own event loop (zarr-internal, like the above)."""
    from zarr.core.sync import sync

    return sync(coro)


def _is_icechunk(store: Any) -> bool:
    return type(store).__module__.partition(".")[0] == "icechunk"


def _consolidate(store: StoreLike, path: str = "") -> zarr.Group:
    with warnings.catch_warnings():
        # "not part of the Zarr format 3 specification": known, and every node keeps
        # its own metadata for readers that don't use it
        warnings.filterwarnings("ignore", message="Consolidated metadata")
        return zarr.consolidate_metadata(store, path=path or None)


def _reconsolidate(root: zarr.Group) -> zarr.Group:
    """Refresh ``root``'s consolidated metadata after a node's metadata changed.

    A no-op returning ``root`` for stores without it (Icechunk, format 1).
    """
    if root.metadata.consolidated_metadata is None:
        return root
    return _consolidate(root.store, root.path)


def _create_sheet(
    parent: zarr.Group,
    grid: SheetGrid,
    chunks: tuple[int, int],
    shards: tuple[int, int] | None,
) -> zarr.Group:
    """Create one sheet's group and arrays under ``parent``: metadata only, no chunks."""
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
        g.create_array(
            slot,
            shape=shape,
            dtype=dtype,
            chunks=c,
            shards=s,
            fill_value=FILL[slot],
            dimension_names=("row", "col"),
        )
    return g


def _sheet_writes(group: zarr.Group, grid: SheetGrid) -> list[Callable[[], None]]:
    """List the chunk writes that fill ``group`` with ``grid``, for slots with data."""
    writes: list[Callable[[], None]] = []
    for slot in SLOT_DTYPES:
        data = grid.arrays[slot]
        if data.size and _has_data(slot, data):
            arr = group[slot]
            assert isinstance(arr, zarr.Array)
            writes.append(partial(arr.__setitem__, Ellipsis, data))
    return writes


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

    Notes
    -----
    This doesn't touch the root's consolidated metadata, so a sheet added this way to a
    format-2 workbook is invisible to readers that use it. :func:`write_model` writes
    whole workbooks properly.
    """
    g = _create_sheet(parent, grid, chunks, shards)
    _concurrently(_sheet_writes(g, grid))
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
    target = _store_path(store, mode, storage_options)  # "w-" raises if it's taken

    # Every node's metadata, built in memory first. The root goes out with the others,
    # consolidated, so nothing is written twice (an append-only store couldn't).
    scratch = zarr.create_group(zarr.storage.MemoryStore(), attributes={ATTR: meta})
    scratch_sheets = scratch.create_group("sheets")
    for grid in model.sheets:
        _create_sheet(scratch_sheets, grid, chunks, shards)
    nodes: dict[str, Any] = {
        path: node.metadata for path, node in scratch.members(max_depth=None)
    }
    nodes[""] = (
        scratch.metadata
        if _is_icechunk(target.store)
        else _consolidate(scratch.store).metadata
    )

    if mode == "w" and target.store.supports_deletes:
        # one delete of the old tree, here, rather than create_hierarchy's overwrite:
        # that deletes every node at once, and on LocalStore those deletes race
        _sync(target.delete_dir())
    prefix = f"{target.path}/" if target.path else ""
    nodes = {f"{prefix}{path}".rstrip("/"): m for path, m in nodes.items()}
    for _ in zarr.create_hierarchy(store=target.store, nodes=nodes):
        pass

    root = zarr.open_group(target.store, path=target.path, mode="r+")
    writes = []
    for grid in model.sheets:
        group = root[f"sheets/{sheet_key(grid.name)}"]
        assert isinstance(group, zarr.Group)
        writes += _sheet_writes(group, grid)
    _concurrently(writes)
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
        # excelizarr only writes zarr v3; saying so skips probing for v2's three files
        root = zarr.open_group(
            store,
            mode=mode,  # type: ignore[arg-type]
            zarr_format=3,
            storage_options=storage_options,
        )
    meta = root.attrs.get(ATTR)
    if not isinstance(meta, dict) or "format" not in meta:
        raise FormatVersionError("not an excelizarr workbook (no 'excelizarr' attrs)")
    if meta["format"] > FORMAT_VERSION:
        raise FormatVersionError(
            f"workbook is format {meta['format']}; this excelizarr reads up to {FORMAT_VERSION}"
        )
    return root


class OpenArrays(Mapping[str, "zarr.Array[Any]"]):
    """A sheet group's arrays, each opened once and then kept.

    With consolidated metadata, opening costs nothing; without it, each open is a
    metadata read, and this saves repeating it.

    Parameters
    ----------
    group : zarr.Group
        The sheet's group.
    """

    def __init__(self, group: zarr.Group) -> None:
        self.group = group
        self._open: dict[str, zarr.Array[Any]] = {}

    def __getitem__(self, slot: str) -> zarr.Array[Any]:
        if slot not in self._open:
            if slot not in SLOT_DTYPES:
                raise KeyError(slot)
            arr = self.group[slot]
            assert isinstance(arr, zarr.Array)
            self._open[slot] = arr
        return self._open[slot]

    def __iter__(self) -> Iterator[str]:
        return iter(SLOT_DTYPES)

    def __len__(self) -> int:
        return len(SLOT_DTYPES)


class LazySlots(Mapping[str, npt.NDArray[Any]]):
    """A sheet's slots, sliced to a region and read only when first asked for.

    :func:`~.model.decode` looks a slot up only if the region holds a cell of a kind
    that lives there, so a numbers-only region never touches the ``string`` array.
    :meth:`prefetch` reads several slots at once, which on an object store costs one
    round trip instead of one per slot.

    Parameters
    ----------
    arrays : zarr.Group or OpenArrays
        The sheet's group, or its arrays.
    rows : slice
        Row slice of the region.
    cols : slice
        Column slice of the region.
    """

    def __init__(
        self, arrays: zarr.Group | OpenArrays, rows: slice, cols: slice
    ) -> None:
        self._arrays = arrays if isinstance(arrays, OpenArrays) else OpenArrays(arrays)
        self._sel = (rows, cols)
        self._cache: dict[str, npt.NDArray[Any]] = {}

    def _read(self, slot: str) -> npt.NDArray[Any]:
        return np.asarray(self._arrays[slot][self._sel])

    def __getitem__(self, slot: str) -> npt.NDArray[Any]:
        if slot not in self._cache:
            if slot not in SLOT_DTYPES:
                raise KeyError(slot)
            self._cache[slot] = self._read(slot)
        return self._cache[slot]

    def prefetch(self, slots: Iterable[str]) -> None:
        """Read ``slots`` concurrently, so later lookups of them are free.

        Parameters
        ----------
        slots : iterable of str
            Slot names; ones already read, or unknown, are skipped.
        """
        todo = [
            s for s in dict.fromkeys(slots) if s in SLOT_DTYPES and s not in self._cache
        ]
        for slot, data in zip(
            todo, _concurrently([partial(self._read, s) for s in todo]), strict=True
        ):
            self._cache[slot] = data

    def __iter__(self) -> Iterator[str]:
        return iter(SLOT_DTYPES)

    def __len__(self) -> int:
        return len(SLOT_DTYPES)


def _read_grids(groups: Sequence[zarr.Group]) -> list[SheetGrid]:
    """Read whole sheets into memory, every slot of every sheet at once."""
    opened = [OpenArrays(g) for g in groups]
    reads = [
        partial(lambda a, s: np.asarray(a[s][...]), arrays, slot)
        for arrays in opened
        for slot in SLOT_DTYPES
    ]
    data = iter(_concurrently(reads))
    grids = []
    for group in groups:
        arrays = {slot: next(data) for slot in SLOT_DTYPES}
        # zarr hands strings back as StringDType already; normalise in case a store
        # came from somewhere that wrote them differently
        for slot in (Slot.STRING, Slot.FORMULA):
            arrays[slot] = arrays[slot].astype(SLOT_DTYPES[slot])
        meta = group.attrs[ATTR]
        assert isinstance(meta, dict)
        grids.append(
            SheetGrid(
                name=str(meta["name"]),
                arrays=arrays,
                merged=list(meta.get("merged", [])),
                col_widths=dict(meta.get("col_widths", {})),
                freeze_panes=meta.get("freeze_panes"),
            )
        )
    return grids


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
    groups = []
    for entry in meta["sheets"]:
        g = root[f"sheets/{entry['key']}"]
        assert isinstance(g, zarr.Group)
        groups.append(g)
    return WorkbookModel(
        _read_grids(groups),
        NumberFormats(meta["number_formats"]),
        dict(meta.get("defined_names", {})),
    )
