"""What a cell *is*, and which typed array its value lives in.

Excel cells are heterogeneous: one column can hold a number, a string and an error. Zarr
arrays are not. So a sheet is stored as several aligned arrays of the same shape, one
per storage *slot*, with a ``kind`` array of :class:`CellKind` codes saying which slot
holds each cell's value. Every slot is filled with its fill value wherever the cell is
some other kind, and zarr never writes a chunk that is all fill value, so the slots a
sheet doesn't use cost nothing on disk.
"""

from __future__ import annotations

import datetime as dt
from enum import IntEnum
from typing import Any, Final

import numpy as np

__all__ = [
    "EPOCH",
    "FILL",
    "SLOT_DTYPES",
    "CellKind",
    "Slot",
    "encode",
]


class CellKind(IntEnum):
    """The type of a cell's value; the code stored in the ``kind`` array."""

    EMPTY = 0
    INT = 1
    FLOAT = 2
    STRING = 3
    BOOL = 4
    DATETIME = 5
    DATE = 6
    TIME = 7
    DURATION = 8
    ERROR = 9


class Slot:
    """The names of the typed arrays a sheet group holds."""

    KIND: Final = "kind"
    NUMBER: Final = "number"
    STRING: Final = "string"
    BOOL: Final = "bool"
    TEMPORAL: Final = "temporal"
    FORMULA: Final = "formula"
    NUMFMT: Final = "numfmt"


#: Which slot each kind's value lives in. ``EMPTY`` lives nowhere.
KIND_SLOT: Final[dict[CellKind, str]] = {
    CellKind.INT: Slot.NUMBER,
    CellKind.FLOAT: Slot.NUMBER,
    CellKind.STRING: Slot.STRING,
    CellKind.ERROR: Slot.STRING,
    CellKind.BOOL: Slot.BOOL,
    CellKind.DATETIME: Slot.TEMPORAL,
    CellKind.DATE: Slot.TEMPORAL,
    CellKind.TIME: Slot.TEMPORAL,
    CellKind.DURATION: Slot.TEMPORAL,
}

#: Temporal values are int64 *microseconds*: since the epoch for datetimes and dates,
#: since midnight for times, and plain for durations. Microseconds rather than
#: nanoseconds because Excel's calendar runs to 9999 and ``datetime64[ns]`` stops in 2262.
EPOCH: Final = dt.datetime(1970, 1, 1)
_US: Final = dt.timedelta(microseconds=1)

SLOT_DTYPES: Final[dict[str, Any]] = {
    Slot.KIND: np.dtype("uint8"),
    Slot.NUMBER: np.dtype("float64"),
    Slot.STRING: np.dtypes.StringDType(),
    Slot.BOOL: np.dtype("bool"),
    Slot.TEMPORAL: np.dtype("int64"),
    Slot.FORMULA: np.dtypes.StringDType(),
    Slot.NUMFMT: np.dtype("uint16"),
}

FILL: Final[dict[str, Any]] = {
    Slot.KIND: 0,
    Slot.NUMBER: np.nan,
    Slot.STRING: "",
    Slot.BOOL: False,
    Slot.TEMPORAL: np.iinfo(np.int64).min,
    Slot.FORMULA: "",
    Slot.NUMFMT: 0,
}

#: The error literals Excel can store in a cell (openpyxl's ``ERROR_CODES``).
ERROR_CODES: Final = frozenset(
    {"#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A"}
)


def encode(value: object) -> tuple[CellKind, object]:
    """Classify a Python cell value and convert it to its slot's representation.

    Parameters
    ----------
    value : object
        ``None``, a bool, int, float, str, ``datetime``/``date``/``time``/``timedelta``,
        or a numpy scalar of one of those.

    Returns
    -------
    tuple
        ``(kind, stored)``, where ``stored`` is what goes in the slot for ``kind``
        (``None`` for :attr:`CellKind.EMPTY`). ``None``, ``NaN`` and ``""`` are all
        empty.

    Raises
    ------
    TypeError
        If ``value`` is none of the above.
    """
    if isinstance(value, np.generic):
        value = value.item()
    if value is None:
        return CellKind.EMPTY, None
    if isinstance(value, bool):
        return CellKind.BOOL, value
    if isinstance(value, int):
        return CellKind.INT, float(value)
    if isinstance(value, float):
        if np.isnan(value):
            return CellKind.EMPTY, None
        return CellKind.FLOAT, value
    if isinstance(value, str):
        if not value:
            # openpyxl never writes an empty string, and pandas reads one as empty, so
            # "" *is* empty: keeping a separate kind for it would only make two
            # spellings of the same cell
            return CellKind.EMPTY, None
        if value in ERROR_CODES:
            return CellKind.ERROR, value
        return CellKind.STRING, value
    if isinstance(value, dt.datetime):
        if value.tzinfo is not None:
            value = value.astimezone(dt.UTC).replace(tzinfo=None)
        return CellKind.DATETIME, (value - EPOCH) // _US
    if isinstance(value, dt.date):
        return CellKind.DATE, (dt.datetime.combine(value, dt.time()) - EPOCH) // _US
    if isinstance(value, dt.time):
        since_midnight = dt.datetime.combine(dt.date.min, value) - dt.datetime.min
        return CellKind.TIME, since_midnight // _US
    if isinstance(value, dt.timedelta):
        return CellKind.DURATION, value // _US
    raise TypeError(f"can't store a {type(value).__name__} in a cell: {value!r}")
