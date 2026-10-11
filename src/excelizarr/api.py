"""The top-level verbs: convert, open, read, convert back."""

from __future__ import annotations

import os
from typing import Any

import zarr
from zarr.storage import StoreLike

from .io_xlsx import Engine, load_xlsx
from .io_zarr import DEFAULT_CHUNKS, open_root, write_model
from .workbook import ZarrWorkbook

__all__ = ["open_workbook", "read_excel", "xlsx_to_zarr", "zarr_to_xlsx"]


def xlsx_to_zarr(
    src: str | os.PathLike[str],
    store: StoreLike,
    *,
    engine: Engine = "openpyxl",
    chunks: tuple[int, int] = DEFAULT_CHUNKS,
    shards: tuple[int, int] | None = None,
    mode: str = "w-",
    storage_options: dict[str, Any] | None = None,
) -> ZarrWorkbook:
    """Convert an ``.xlsx`` file into a zarr workbook store.

    Parameters
    ----------
    src : str or os.PathLike
        The ``.xlsx`` file.
    store : StoreLike
        Where to write: a path, an ``s3://``/``gs://`` URL, a ``MemoryStore``, a
        ``ZipStore``, an Icechunk session's ``store``, ...
    engine : {"openpyxl", "calamine"}
        ``openpyxl`` keeps formulas, number formats and layout; ``calamine`` (the
        ``fast`` extra) keeps values only, and is much faster.
    chunks : tuple of int
        ``(rows, cols)`` per chunk.
    shards : tuple of int, optional
        ``(rows, cols)`` per shard, for big sheets on object stores.
    mode : str
        ``"w-"`` (default) refuses to overwrite an existing store; ``"w"`` replaces it.
    storage_options : dict, optional
        Passed through to fsspec for URL stores.

    Returns
    -------
    ZarrWorkbook
        The written workbook, open for reading.
    """
    model = load_xlsx(src, engine=engine)
    root = write_model(
        model,
        store,
        chunks=chunks,
        shards=shards,
        mode=mode,
        storage_options=storage_options,
    )
    return ZarrWorkbook(root)


def open_workbook(
    store: StoreLike | zarr.Group,
    *,
    mode: str = "r",
    storage_options: dict[str, Any] | None = None,
) -> ZarrWorkbook:
    """Open a zarr workbook store lazily.

    Parameters
    ----------
    store : StoreLike or zarr.Group
        The store, or its already-open root group.
    mode : str
        ``"r"`` (default) for read-only; ``"r+"`` to allow in-place cell writes.
    storage_options : dict, optional
        Passed through to fsspec for URL stores.

    Returns
    -------
    ZarrWorkbook
        The workbook. Nothing but its root attributes has been read yet.
    """
    return ZarrWorkbook(open_root(store, mode=mode, storage_options=storage_options))


def read_excel(
    store: StoreLike | zarr.Group | ZarrWorkbook,
    sheet_name: str | int | list[str | int] | None = 0,
    *,
    storage_options: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """``pandas.read_excel``, reading from a zarr workbook store instead of ``.xlsx``.

    pandas' own argument handling runs on the cells, so every keyword ``read_excel``
    takes works the same way here, and the frame you get is the frame you'd have got
    from the original file.

    Parameters
    ----------
    store : StoreLike, zarr.Group or ZarrWorkbook
        The workbook.
    sheet_name : str, int, list or None
        As for ``pandas.read_excel``: a name, a position, a list of either (returns a
        dict), or ``None`` for every sheet (returns a dict).
    storage_options : dict, optional
        Passed through to fsspec for URL stores.
    **kwargs
        Anything else ``pandas.read_excel`` takes: ``header``, ``index_col``,
        ``usecols``, ``skiprows``, ``nrows``, ``dtype``, ``parse_dates``, ...

    Returns
    -------
    pandas.DataFrame or dict of pandas.DataFrame
        As ``pandas.read_excel`` would return.
    """
    from ._pandas import ZarrExcelReader

    wb = (
        store
        if isinstance(store, ZarrWorkbook)
        else open_workbook(store, storage_options=storage_options)
    )
    return ZarrExcelReader(wb).parse(sheet_name=sheet_name, **kwargs)


def zarr_to_xlsx(
    store: StoreLike | zarr.Group | ZarrWorkbook,
    dst: str | os.PathLike[str],
    *,
    storage_options: dict[str, Any] | None = None,
) -> None:
    """Convert a zarr workbook store back into an ``.xlsx`` file.

    Parameters
    ----------
    store : StoreLike, zarr.Group or ZarrWorkbook
        The workbook.
    dst : str or os.PathLike
        The ``.xlsx`` file to write.
    storage_options : dict, optional
        Passed through to fsspec for URL stores.
    """
    wb = (
        store
        if isinstance(store, ZarrWorkbook)
        else open_workbook(store, storage_options=storage_options)
    )
    wb.to_xlsx(dst)
