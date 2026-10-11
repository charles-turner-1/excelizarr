"""``xz.read_excel(store)`` is ``pd.read_excel(xlsx)``.

The property draws a workbook *and* a set of ``read_excel`` keywords, writes the
workbook to ``.xlsx``, converts that to zarr, and reads both. The frames must be equal,
dtypes included. Where pandas raises on the ``.xlsx`` (a ``header`` row past the end of
the data, say), reading the store must raise the same exception type.
"""

from pathlib import Path

import pandas as pd
import pytest
import zarr
from hypothesis import given
from hypothesis import strategies as st
from strategies import plain_values, workbooks

import excelizarr as xz
from excelizarr.io_xlsx import dump_xlsx
from excelizarr.model import WorkbookModel


@st.composite
def read_kwargs(draw: st.DrawFn) -> dict[str, object]:
    """Draw a plausible combination of ``read_excel`` keywords."""
    kw: dict[str, object] = {}
    kw["header"] = draw(st.sampled_from([0, None, 1]))
    if draw(st.booleans()):
        kw["nrows"] = draw(st.integers(0, 6))
    if draw(st.booleans()):
        kw["skiprows"] = draw(
            st.one_of(st.integers(0, 3), st.lists(st.integers(1, 6), max_size=2))
        )
    if draw(st.booleans()):
        kw["usecols"] = draw(st.sampled_from(["A", "A:B", "B:D", "A,C"]))
    if draw(st.booleans()):
        kw["index_col"] = 0
    return kw


def _read(fn, *args, **kw):  # type: ignore[no-untyped-def]
    try:
        return fn(*args, **kw), None
    except Exception as e:  # noqa: BLE001 - the *type* of failure is what's compared
        return None, type(e)


def _compare(
    tmp: Path, model: WorkbookModel, kw: dict[str, object], sheet: object = 0
) -> None:
    dump_xlsx(model, tmp / "a.xlsx")
    store = zarr.storage.MemoryStore()
    xz.xlsx_to_zarr(tmp / "a.xlsx", store)
    want, want_err = _read(
        pd.read_excel, tmp / "a.xlsx", sheet_name=sheet, engine="openpyxl", **kw
    )
    got, got_err = _read(xz.read_excel, store, sheet_name=sheet, **kw)
    assert got_err == want_err, (got_err, want_err)
    if want is None:
        return
    if isinstance(want, dict):
        assert isinstance(got, dict) and list(got) == list(want)
        for k in want:
            pd.testing.assert_frame_equal(got[k], want[k])
    else:
        pd.testing.assert_frame_equal(got, want)


@given(workbooks(with_layout=False), read_kwargs())
def test_read_excel_matches_pandas(
    tmp_path_factory, model: WorkbookModel, kw: dict[str, object]
) -> None:
    """Mixed-type sheets, formulas and all: the frames match pandas' exactly."""
    _compare(tmp_path_factory.mktemp("rx"), model, kw)


@given(
    workbooks(values=plain_values, with_formulas=False, with_layout=False),
    read_kwargs(),
)
def test_read_excel_matches_pandas_plain(
    tmp_path_factory, model: WorkbookModel, kw: dict[str, object]
) -> None:
    """Plain values, more of them reach the frame (fewer typed oddities to coerce)."""
    _compare(tmp_path_factory.mktemp("rp"), model, kw)


@given(workbooks(max_sheets=3, with_layout=False))
def test_read_excel_all_sheets(tmp_path_factory, model: WorkbookModel) -> None:
    """``sheet_name=None`` returns every sheet, in tab order, as pandas does."""
    _compare(tmp_path_factory.mktemp("ra"), model, {}, sheet=None)


def test_read_excel_by_name_and_from_workbook(sample_xlsx: Path) -> None:
    """A sheet by name, from a path store or an open workbook, both match pandas."""
    store = sample_xlsx.with_suffix(".zarr")
    wb = xz.xlsx_to_zarr(sample_xlsx, store)
    want = pd.read_excel(sample_xlsx, sheet_name="Lookup", index_col=0)
    pd.testing.assert_frame_equal(
        xz.read_excel(store, sheet_name="Lookup", index_col=0), want
    )
    pd.testing.assert_frame_equal(
        xz.read_excel(wb, sheet_name="Lookup", index_col=0), want
    )
    pd.testing.assert_frame_equal(wb["Lookup"].to_pandas(index_col=0), want)


def test_read_excel_missing_sheet_raises_like_pandas(sample_xlsx: Path) -> None:
    """A missing sheet raises pandas' own ``ValueError``, by name or by index."""
    store = zarr.storage.MemoryStore()
    xz.xlsx_to_zarr(sample_xlsx, store)
    with pytest.raises(ValueError, match="not found"):
        xz.read_excel(store, sheet_name="Nope")
    with pytest.raises(ValueError, match="invalid"):
        xz.read_excel(store, sheet_name=7)
