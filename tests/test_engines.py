"""The calamine reader agrees with openpyxl on everything it can see.

One known difference is narrowed out of the strategy rather than papered over:
calamine reads a whitespace-only *inline* string (``<is><t xml:space="preserve"> </t>``,
which is how openpyxl writes every string) as empty.
"""

from pathlib import Path

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from strategies import floats, ints, texts, workbooks

from excelizarr.io_xlsx import dump_xlsx, load_xlsx
from excelizarr.model import WorkbookModel

pytest.importorskip("python_calamine")


def _same(a: object, b: object) -> bool:
    # calamine has no int/float distinction, so compare numbers by value; but a bool is
    # still not a number
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


calamine_values = st.one_of(
    st.none(), ints, floats, texts.filter(str.strip), st.booleans()
)


@given(workbooks(values=calamine_values, with_formulas=False, with_layout=False))
def test_calamine_matches_openpyxl(tmp_path_factory, model: WorkbookModel) -> None:
    """Plain values read through calamine equal those read through openpyxl."""
    path = Path(tmp_path_factory.mktemp("cal")) / "a.xlsx"
    dump_xlsx(model, path)
    a, b = load_xlsx(path), load_xlsx(path, engine="calamine")
    assert a.sheet_names == b.sheet_names
    for sa, sb in zip(a.sheets, b.sheets, strict=True):
        va, vb = sa.values(), sb.values()
        # calamine trims to the cells that hold values; openpyxl to the cells written
        shape = (max(va.shape[0], vb.shape[0]), max(va.shape[1], vb.shape[1]))
        pa, pb = (np.full(shape, None, dtype=object) for _ in range(2))
        pa[: va.shape[0], : va.shape[1]] = va
        pb[: vb.shape[0], : vb.shape[1]] = vb
        for (i, j), x in np.ndenumerate(pa):
            assert _same(x, pb[i, j]), (sa.name, i, j, x, pb[i, j])


def test_unknown_engine(sample_xlsx: Path) -> None:
    """An engine we don't have is a ``ValueError``."""
    with pytest.raises(ValueError, match="unknown engine"):
        load_xlsx(sample_xlsx, engine="xlrd")  # type: ignore[arg-type]
