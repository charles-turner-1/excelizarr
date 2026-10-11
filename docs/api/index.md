# API reference

Generated from the source: the signatures are the real ones, and the prose is the
docstring.

## Converting and reading

```{eval-rst}
.. currentmodule:: excelizarr

.. autosummary::
   :toctree: generated/
   :nosignatures:

   xlsx_to_zarr
   open_workbook
   read_excel
   zarr_to_xlsx
```

## The lazy workbook

```{eval-rst}
.. currentmodule:: excelizarr

.. autosummary::
   :toctree: generated/
   :nosignatures:

   ZarrWorkbook
   ZarrSheet
   CellKind
```

## Errors

```{eval-rst}
.. currentmodule:: excelizarr

.. autosummary::
   :toctree: generated/
   :nosignatures:

   ExcelizarrError
   InvalidReferenceError
   FormatVersionError
```

## Lower level

The in-memory model, and the readers and writers on either side of it. Useful if you
want to build a workbook without going through `.xlsx` at all.

```{eval-rst}
.. currentmodule:: excelizarr

.. autosummary::
   :toctree: generated/
   :nosignatures:

   model.SheetGrid
   model.WorkbookModel
   model.NumberFormats
   io_xlsx.load_xlsx
   io_xlsx.dump_xlsx
   io_zarr.write_model
   io_zarr.read_model
```
