"""Workbooks on obstore-backed stores (``zarr.storage.ObjectStore``).

On S3 these skip the HEAD that s3fs sends before every GET. Here they run against
obstore's own memory and local-disk stores, which go through the same zarr code.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import zarr

import excelizarr as xz

obstore = pytest.importorskip("obstore")


@pytest.fixture(params=["memory", "local"])
def object_store(request: pytest.FixtureRequest, tmp_path: Path) -> Callable[[], Any]:
    """Make a fresh ``ObjectStore`` handle on one underlying obstore store."""
    if request.param == "memory":
        backing = obstore.store.MemoryStore()
    else:
        backing = obstore.store.LocalStore(tmp_path / "book.zarr", mkdir=True)
    return lambda: zarr.storage.ObjectStore(backing)


def test_round_trip(sample_xlsx: Path, object_store: Callable[[], Any]) -> None:
    """Convert, open and read back: the same frame pandas reads from the xlsx."""
    xz.xlsx_to_zarr(sample_xlsx, object_store())
    wb = xz.open_workbook(object_store())
    assert wb.root.metadata.consolidated_metadata is not None
    assert wb["Lookup"]["A2"] == "alpha"
    pd.testing.assert_frame_equal(
        xz.read_excel(object_store(), sheet_name="Lookup"),
        pd.read_excel(sample_xlsx, sheet_name="Lookup"),
    )


def test_overwrite_and_grow(sample_xlsx: Path, object_store: Callable[[], Any]) -> None:
    """``mode="w"`` replaces the workbook, and growing a sheet in place is visible
    to a fresh open.
    """
    xz.xlsx_to_zarr(sample_xlsx, object_store())
    xz.xlsx_to_zarr(sample_xlsx, object_store(), mode="w")
    wb = xz.open_workbook(object_store(), mode="r+")
    before = wb["Inputs"].shape
    wb["Inputs"]["Z99"] = 7
    again = xz.open_workbook(object_store())
    assert again["Inputs"].shape == (max(before[0], 99), max(before[1], 26))
    assert again["Inputs"]["Z99"] == 7
