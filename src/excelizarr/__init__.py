"""A cloud-native representation of an ``.xlsx`` workbook, in Zarr.

A workbook becomes a zarr group, each sheet a group of aligned, chunked, typed 2D
arrays. Read a range straight off object storage, ``pd.read_excel`` a store without
unzipping any XML, and convert back to ``.xlsx`` with formulas and number formats
intact::

    import excelizarr as xz

    wb = xz.xlsx_to_zarr("model.xlsx", "s3://bucket/model.zarr")
    wb["Inputs!B2:D10"]
    df = xz.read_excel("s3://bucket/model.zarr", sheet_name="Inputs")
    xz.zarr_to_xlsx("s3://bucket/model.zarr", "roundtrip.xlsx")
"""

from . import _version
from .api import open_workbook, read_excel, xlsx_to_zarr, zarr_to_xlsx
from .exceptions import ExcelizarrError, FormatVersionError, InvalidReferenceError
from .kinds import CellKind
from .workbook import ZarrSheet, ZarrWorkbook

__all__ = [
    "CellKind",
    "ExcelizarrError",
    "FormatVersionError",
    "InvalidReferenceError",
    "ZarrSheet",
    "ZarrWorkbook",
    "open_workbook",
    "read_excel",
    "xlsx_to_zarr",
    "zarr_to_xlsx",
]

__version__ = _version.get_versions()["version"]
