"""Round trips: model ↔ zarr is the identity, and xlsx → zarr → xlsx loses nothing.

The xlsx properties use ``load_xlsx`` as the canonicaliser. A generated model is
written to ``.xlsx`` once and read back (``m1``), which settles everything openpyxl
normalises on the way, such as dates becoming datetimes and temporal cells gaining a
date format. Then ``m1`` goes through zarr and back out to ``.xlsx`` and is read again
(``m2``). Anything ``m1`` and ``m2`` disagree on is something excelizarr lost.
"""

import datetime as dt
from pathlib import Path

import numpy as np
import zarr
from hypothesis import given
from hypothesis import strategies as st
from strategies import arrays_equal, workbooks

from excelizarr.io_xlsx import dump_xlsx, load_xlsx
from excelizarr.io_zarr import read_model, write_model
from excelizarr.kinds import SLOT_DTYPES
from excelizarr.model import WorkbookModel

chunk_shapes = st.tuples(st.integers(1, 7), st.integers(1, 5))


def assert_models_equal(a: WorkbookModel, b: WorkbookModel) -> None:
    """Every slot of every sheet, and all the metadata, is identical."""
    assert a.sheet_names == b.sheet_names
    assert a.defined_names == b.defined_names
    for sa, sb in zip(a.sheets, b.sheets, strict=True):
        assert sa.shape == sb.shape, sa.name
        for slot in SLOT_DTYPES:
            if slot == "numfmt":
                # compare format *strings*: the two tables may number them differently
                fa = np.array(a.number_formats.tolist(), dtype=object)[sa.arrays[slot]]
                fb = np.array(b.number_formats.tolist(), dtype=object)[sb.arrays[slot]]
                assert np.array_equal(fa, fb), (sa.name, slot)
            else:
                assert arrays_equal(sa.arrays[slot], sb.arrays[slot]), (sa.name, slot)
        assert sa.merged == sb.merged
        assert sa.col_widths == sb.col_widths
        assert sa.freeze_panes == sb.freeze_panes


@given(workbooks(), chunk_shapes, st.booleans())
def test_zarr_round_trip_is_identity(
    model: WorkbookModel, chunks: tuple[int, int], shard: bool
) -> None:
    """model → zarr → model gives back every slot exactly, whatever the chunking."""
    shards = (chunks[0] * 2, chunks[1] * 2) if shard else None
    store = zarr.storage.MemoryStore()
    write_model(model, store, chunks=chunks, shards=shards)
    assert_models_equal(model, read_model(store))


@given(workbooks())
def test_xlsx_zarr_xlsx_loses_nothing(tmp_path_factory, model: WorkbookModel) -> None:
    """xlsx → zarr → xlsx: everything openpyxl can see in the file survives."""
    d = tmp_path_factory.mktemp("rt")
    dump_xlsx(model, d / "a.xlsx")
    m1 = load_xlsx(d / "a.xlsx")
    store = zarr.storage.MemoryStore()
    write_model(m1, store)
    dump_xlsx(read_model(store), d / "b.xlsx")
    assert_models_equal(m1, load_xlsx(d / "b.xlsx"))


def _xlsx_normal_form(v: object) -> object:
    # openpyxl reads a date cell back as a datetime at midnight
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        return dt.datetime.combine(v, dt.time())
    return v


@given(workbooks())
def test_xlsx_reader_sees_what_was_written(
    tmp_path_factory, model: WorkbookModel
) -> None:
    """The openpyxl reader recovers every generated value and formula, and the shape.

    This checks the readers themselves, not just their consistency: a reader that
    dropped a kind on both legs would pass the round-trip property above.
    """
    path = Path(tmp_path_factory.mktemp("rd")) / "a.xlsx"
    dump_xlsx(model, path)
    back = load_xlsx(path)
    for want, got in zip(model.sheets, back.sheets, strict=True):
        wv, gv = want.values(), got.values()
        # openpyxl's used range can only be the generated one or smaller (a trailing
        # all-empty row or column isn't written to the file)
        assert gv.shape[0] <= wv.shape[0] and gv.shape[1] <= wv.shape[1]
        padded = np.full(wv.shape, None, dtype=object)
        padded[: gv.shape[0], : gv.shape[1]] = gv
        for (i, j), w in np.ndenumerate(wv):
            assert padded[i, j] == _xlsx_normal_form(w), (
                want.name,
                i,
                j,
                w,
                padded[i, j],
            )
        f = np.full(wv.shape, "", dtype=object)
        f[: gv.shape[0], : gv.shape[1]] = got.arrays["formula"].astype(object)
        assert np.array_equal(f, want.arrays["formula"].astype(object))


@given(workbooks(), chunk_shapes, chunk_shapes)
def test_chunking_does_not_change_contents(
    model: WorkbookModel, a: tuple[int, int], b: tuple[int, int]
) -> None:
    """Two stores of the same workbook with different chunk shapes read back the same."""
    sa, sb = zarr.storage.MemoryStore(), zarr.storage.MemoryStore()
    write_model(model, sa, chunks=a)
    write_model(model, sb, chunks=b)
    assert_models_equal(read_model(sa), read_model(sb))
