"""Version-controlled spreadsheets: a workbook in an Icechunk repository."""

from pathlib import Path

import pytest

import excelizarr as xz

icechunk = pytest.importorskip("icechunk")


def test_time_travel(sample_xlsx: Path) -> None:
    """Commit a workbook, edit a cell, commit again; the old snapshot still has the
    old value, and the branch head has the new one.
    """
    repo = icechunk.Repository.create(icechunk.in_memory_storage())

    session = repo.writable_session("main")
    xz.xlsx_to_zarr(sample_xlsx, session.store, mode="w")
    v1 = session.commit("import sample.xlsx")

    session = repo.writable_session("main")
    xz.open_workbook(session.store, mode="r+")["Lookup"]["B2"] = 1.75
    v2 = session.commit("bump alpha")

    then = xz.open_workbook(repo.readonly_session(snapshot_id=v1).store)
    now = xz.open_workbook(repo.readonly_session("main").store)
    assert then["Lookup"]["B2"] == 1.5
    assert now["Lookup"]["B2"] == 1.75
    assert v1 != v2

    # and the rest of the workbook didn't notice
    assert then["Inputs!A1:D3"].tolist() == now["Inputs!A1:D3"].tolist()
    df = xz.read_excel(now, sheet_name="Lookup")
    assert df["value"].tolist() == [1.75, 2.5]


def test_branching_scenarios(sample_xlsx: Path) -> None:
    """Two branches of the same model hold different inputs side by side."""
    repo = icechunk.Repository.create(icechunk.in_memory_storage())
    session = repo.writable_session("main")
    xz.xlsx_to_zarr(sample_xlsx, session.store, mode="w")
    base = session.commit("baseline")

    repo.create_branch("high-rates", snapshot_id=base)
    session = repo.writable_session("high-rates")
    xz.open_workbook(session.store, mode="r+")["Lookup"]["B2:B3"] = [[9.0], [9.5]]
    session.commit("what if rates were silly")

    main = xz.read_excel(repo.readonly_session("main").store, sheet_name="Lookup")
    high = xz.read_excel(repo.readonly_session("high-rates").store, sheet_name="Lookup")
    assert main["value"].tolist() == [1.5, 2.5]
    assert high["value"].tolist() == [9.0, 9.5]
