"""The exceptions excelizarr raises on purpose."""

__all__ = ["ExcelizarrError", "FormatVersionError", "InvalidReferenceError"]


class ExcelizarrError(Exception):
    """Base class for every error excelizarr raises deliberately."""


class InvalidReferenceError(ExcelizarrError, ValueError):
    """An A1-style cell or range reference that doesn't parse, or names no sheet."""


class FormatVersionError(ExcelizarrError):
    """A store that isn't an excelizarr workbook, or is one from a format we can't read."""
