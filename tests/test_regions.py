"""Range reads agree with slicing the whole sheet, and empty space costs nothing."""

import numpy as np
import zarr
from hypothesis import given
from hypothesis import strategies as st
from strategies import grids, ranges

import excelizarr as xz
from excelizarr._refs import format_range
from excelizarr.io_zarr import write_model
from excelizarr.model import SheetGrid, WorkbookModel


def _open(grid: SheetGrid, chunks: tuple[int, int] = (3, 2)) -> xz.ZarrWorkbook:
    store = zarr.storage.MemoryStore()
    write_model(WorkbookModel([grid]), store, chunks=chunks)
    return xz.open_workbook(store)


@given(st.data(), grids(max_rows=15, max_cols=7))
def test_range_read_is_a_slice_of_the_whole(
    data: st.DataObject, grid: SheetGrid
) -> None:
    """``sheet["B2:D10"]`` is exactly ``whole[1:10, 1:4]``, even past the edge."""
    r0, r1, c0, c1 = data.draw(ranges(grid.shape))
    sheet = _open(grid)[grid.name]
    whole = grid.values()
    want = whole[r0:r1, c0:c1]
    got_a1 = sheet[format_range(r0, r1, c0, c1)]
    got_np = sheet[r0:r1, c0:c1]
    if (r1 - r0, c1 - c0) == (1, 1):
        cell = want[0, 0] if want.size else None
        assert got_a1 == cell
        assert np.array_equal(got_np, want)
    else:
        assert np.array_equal(got_a1, want)
        assert np.array_equal(got_np, want)


@given(grids(max_rows=10, max_cols=5))
def test_whole_columns_and_rows(grid: SheetGrid) -> None:
    """Open ranges (``"A:B"``, ``"2:3"``) clip to the used range."""
    sheet = _open(grid)[grid.name]
    whole = grid.values()
    assert np.array_equal(sheet["A:B"], whole[:, 0:2])
    assert np.array_equal(sheet["2:3"], whole[1:3, :])


@given(st.integers(0, 2_000), st.integers(0, 300))
def test_sparse_sheets_write_one_chunk(row: int, col: int) -> None:
    """One value anywhere in a big empty sheet writes one chunk per slot it touches.

    That's ``kind`` and ``number``. Every other slot, and every other chunk of those
    two, is all fill value, so zarr never writes it.
    """
    grid = SheetGrid.empty("s", (row + 1, col + 1))
    grid.set_cell(row, col, 42)
    store = zarr.storage.MemoryStore()
    write_model(WorkbookModel([grid]), store, chunks=(64, 16))
    chunk_keys = [k for k in store._store_dict if "/c/" in k]
    assert sorted(k.split("/")[-4] for k in chunk_keys) == ["kind", "number"]
    assert xz.open_workbook(store)["s"][row, col] == 42


@given(grids(max_rows=8, max_cols=5), st.data())
def test_cell_writes_land_and_touch_nothing_else(
    grid: SheetGrid, data: st.DataObject
) -> None:
    """Writing one cell in place changes that cell and no other, growing the sheet
    if the cell is past the edge.
    """
    from strategies import cell_values

    store = zarr.storage.MemoryStore()
    write_model(WorkbookModel([grid]), store, chunks=(3, 2))
    sheet = xz.open_workbook(store, mode="r+")[grid.name]
    i = data.draw(st.integers(0, grid.shape[0] + 2))
    j = data.draw(st.integers(0, grid.shape[1] + 2))
    # assigning "=..." writes a formula, not text: that's another test's business
    v = data.draw(
        cell_values.filter(lambda x: not (isinstance(x, str) and x.startswith("=")))
    )
    sheet[i, j] = v
    nr, nc = max(grid.shape[0], i + 1), max(grid.shape[1], j + 1)
    assert sheet.shape == (nr, nc)
    want = np.full((nr, nc), None, dtype=object)
    want[: grid.shape[0], : grid.shape[1]] = grid.values()
    want[i, j] = None if v == "" else v
    assert np.array_equal(sheet[0:nr, 0:nc], want)
