"""What a read costs a store: consolidated metadata, and slots fetched concurrently.

Local stores answer in microseconds, so none of this shows up in timings here. On an
object store every request is a round trip, and these are the properties that keep a
range read at two of them.
"""

import asyncio
import collections
import zipfile
from pathlib import Path

import numpy as np
import pytest
import zarr

import excelizarr as xz
from excelizarr.io_zarr import ATTR, CONCURRENCY, FORMAT_VERSION, write_model
from excelizarr.kinds import CellKind, Slot
from excelizarr.model import SheetGrid, WorkbookModel


class CountingStore(zarr.storage.WrapperStore):  # type: ignore[type-arg]
    """Record every key read, and the most reads ever in flight at once.

    Each read sleeps a little, as a request to an object store would, so reads that
    are issued together overlap and reads issued one after another don't.
    """

    def __init__(self, store: zarr.abc.store.Store, latency: float = 0.02) -> None:
        super().__init__(store)
        self.latency = latency
        self.keys: list[str] = []
        self.in_flight = 0
        self.max_in_flight = 0

    def reset(self) -> None:
        self.keys.clear()
        self.max_in_flight = 0

    async def get(self, key, prototype, byte_range=None):  # type: ignore[no-untyped-def]
        self.keys.append(key)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.latency)
            return await self._store.get(key, prototype, byte_range)
        finally:
            self.in_flight -= 1


@pytest.fixture
def counted(sample_xlsx: Path, tmp_path: Path) -> CountingStore:
    """The sample workbook on disk, read through a :class:`CountingStore`."""
    xz.xlsx_to_zarr(sample_xlsx, tmp_path / "book.zarr")
    return CountingStore(
        zarr.storage.LocalStore(tmp_path / "book.zarr", read_only=True)
    )


def test_root_is_consolidated(sample_xlsx: Path, tmp_path: Path) -> None:
    """The root carries every node's metadata, and each node still has its own."""
    wb = xz.xlsx_to_zarr(sample_xlsx, tmp_path / "book.zarr")
    assert wb.root.attrs[ATTR]["format"] == FORMAT_VERSION == 2
    assert wb.root.metadata.consolidated_metadata is not None
    plain = zarr.open_group(tmp_path / "book.zarr", mode="r", use_consolidated=False)
    assert plain["sheets/Inputs/number"].shape == wb["Inputs"].shape


def test_range_read_reads_no_metadata(counted: CountingStore) -> None:
    """Opening is one read; a range read after that is chunks only."""
    wb = xz.open_workbook(counted)
    assert counted.keys == ["zarr.json"]
    counted.reset()
    cells = wb["Inputs!A1:D3"]
    assert cells.shape == (3, 4)
    assert counted.keys  # it did read something...
    assert not [
        k for k in counted.keys if k.endswith("zarr.json")
    ]  # ...but no metadata


def test_value_slots_are_fetched_together(counted: CountingStore) -> None:
    """``kind`` first, then every value slot the range needs, all at once."""
    wb = xz.open_workbook(counted)
    counted.reset()
    wb["Inputs!A1:D3"]  # header strings, numbers, a date: three value slots
    slots = {k.split("/")[2] for k in counted.keys}
    assert {"kind", "string", "number", "temporal"} <= slots
    assert counted.max_in_flight >= 3


def test_whole_workbook_reads_overlap(counted: CountingStore) -> None:
    """Reading every sheet reads every slot of every sheet concurrently."""
    wb = xz.open_workbook(counted)
    counted.reset()
    wb.to_model()
    assert counted.max_in_flight > len(wb.sheet_names)


def test_read_excel_overlaps(counted: CountingStore) -> None:
    """``read_excel`` goes the same way: ``kind``, then its value slots together."""
    counted.reset()
    xz.read_excel(counted, sheet_name="Inputs")
    assert counted.max_in_flight >= 2


def test_zip_store_writes_each_entry_once(sample_xlsx: Path, tmp_path: Path) -> None:
    """Consolidating doesn't mean rewriting the root: a zip never gets a duplicate."""
    path = tmp_path / "book.zarr.zip"
    with zarr.storage.ZipStore(path, mode="w") as store:
        xz.xlsx_to_zarr(sample_xlsx, store)
    names = zipfile.ZipFile(path).namelist()
    assert [n for n, c in collections.Counter(names).items() if c > 1] == []
    with zarr.storage.ZipStore(path, mode="r") as store:
        wb = xz.open_workbook(store)
        assert wb.root.metadata.consolidated_metadata is not None
        assert wb["Lookup"]["A2"] == "alpha"


def test_overwrite_leaves_nothing_behind(sample_xlsx: Path, tmp_path: Path) -> None:
    """``mode="w"`` replaces the whole tree, sheets the new workbook lacks included."""
    store = tmp_path / "book.zarr"
    xz.xlsx_to_zarr(sample_xlsx, store)
    (store / "stray").write_text("left over")
    model = xz.open_workbook(store).to_model()
    model.sheets = model.sheets[:1]
    from excelizarr.io_zarr import write_model

    write_model(model, store, mode="w")
    assert not (store / "stray").exists()
    assert not (store / "sheets" / "Lookup").exists()
    assert xz.open_workbook(store).sheet_names == ["Inputs"]


def test_growing_a_sheet_keeps_consolidated_metadata_current(
    sample_xlsx: Path, tmp_path: Path
) -> None:
    """Writing past the edge resizes the arrays, and the root's copy follows."""
    store = tmp_path / "book.zarr"
    xz.xlsx_to_zarr(sample_xlsx, store)
    wb = xz.open_workbook(store, mode="r+")
    before = wb["Inputs"].shape
    wb["Inputs"]["Z99"] = 7
    want = (max(before[0], 99), max(before[1], 26))
    assert wb["Inputs"].shape == want  # a fresh sheet from the same workbook
    again = xz.open_workbook(store)
    assert again["Inputs"].shape == want  # and a fresh open
    assert again["Inputs"]["Z99"] == 7


def test_format_1_stores_still_read(sample_xlsx: Path) -> None:
    """A store from before consolidated metadata reads, and writes in place, as ever."""
    store = zarr.storage.MemoryStore()
    xz.xlsx_to_zarr(sample_xlsx, store)
    # what format 1 wrote: the same tree, no consolidated metadata
    root = zarr.open_group(store, mode="r+", use_consolidated=False)
    meta = dict(root.attrs[ATTR])
    meta["format"] = 1
    root.attrs[ATTR] = meta
    assert zarr.open_group(store, mode="r").metadata.consolidated_metadata is None

    wb = xz.open_workbook(store, mode="r+")
    assert wb["Lookup"]["A2"] == "alpha"
    wb["Lookup"]["C9"] = 1.5
    assert wb.root.metadata.consolidated_metadata is None  # not added behind its back
    assert xz.open_workbook(store)["Lookup"]["C9"] == 1.5
    assert xz.read_excel(store, sheet_name="Lookup")["value"].tolist()[:2] == [1.5, 2.5]


def test_every_handle_sees_a_sheet_grow(sample_xlsx: Path) -> None:
    """Sheets are opened once per workbook, so a handle taken before another one grew
    the sheet still sees the new shape, and the cells past the old edge.
    """
    wb = xz.xlsx_to_zarr(sample_xlsx, zarr.storage.MemoryStore())
    early = wb["Inputs"]
    wb["Inputs"]["Z99"] = 7
    assert early is wb["Inputs"]
    assert early.shape[0] >= 99 and early.shape[1] >= 26
    assert early["Z99"] == 7


def _tall(
    path: Path, rows: int, shards: tuple[int, int] | None
) -> zarr.storage.LocalStore:
    """A one-column sheet of ``rows`` numbers: a chunk per 1024 rows.

    On disk, not in memory: zarr reads a ``MemoryStore`` through a synchronous fast
    path that a :class:`CountingStore` around it never sees.
    """
    grid = SheetGrid.empty("Tall", (rows, 1))
    grid.arrays[Slot.KIND][:] = int(CellKind.FLOAT)
    grid.arrays[Slot.NUMBER][:, 0] = np.arange(rows)
    write_model(WorkbookModel([grid]), path, shards=shards)
    return zarr.storage.LocalStore(path, read_only=True)


def test_concurrency_floor(tmp_path: Path) -> None:
    """A slot read keeps :data:`CONCURRENCY` chunk requests in flight, not zarr's 10,
    and a higher limit set in zarr's own config is left alone.
    """
    counted = CountingStore(_tall(tmp_path, 140 * 1024, shards=None), latency=0.005)
    sheet = xz.open_workbook(counted)["Tall"]
    whole = (slice(0, sheet.shape[0]), slice(0, 1))

    counted.reset()
    sheet.slots(whole)[Slot.NUMBER]
    assert counted.max_in_flight == CONCURRENCY

    counted.reset()
    with zarr.config.set({"async.concurrency": 200}):
        sheet.slots(whole)[Slot.NUMBER]
    assert counted.max_in_flight == 140  # every chunk at once
