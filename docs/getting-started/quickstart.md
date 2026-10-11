---
file_format: mystnb
kernelspec:
  name: python3
---

# Quickstart

```bash
pip install excelizarr            # or, with the fast reader and Icechunk:
pip install "excelizarr[fast,icechunk]"
```

First we need a spreadsheet. This one is built with openpyxl, but any `.xlsx` will do.

```{code-cell} python
import datetime as dt
import tempfile
from pathlib import Path

import openpyxl

tmp = Path(tempfile.mkdtemp())
book = openpyxl.Workbook()
ws = book.active
ws.title = "Inputs"
ws.append(["region", "population", "growth", "updated"])
ws.append(["north", 1_200_000, 0.012, dt.datetime(2026, 1, 1)])
ws.append(["south", 860_000, 0.021, dt.datetime(2026, 2, 1)])
ws.append(["east", 2_310_000, -0.004, dt.datetime(2026, 3, 1)])
ws["E1"] = "next year"
for r in (2, 3, 4):
    ws[f"E{r}"] = f"=B{r}*(1+C{r})"
    ws[f"C{r}"].number_format = "0.0%"
book.save(tmp / "model.xlsx")
```

## Convert it

```{code-cell} python
import excelizarr as xz

wb = xz.xlsx_to_zarr(tmp / "model.xlsx", tmp / "model.zarr")
wb
```

## Read cells and ranges

Index the workbook with Excel references, or a sheet with references or numpy-style
slices. A range comes back as an object array of plain Python values. A single cell
comes back as its value.

```{code-cell} python
wb["Inputs!A1:D3"]
```

```{code-cell} python
sheet = wb["Inputs"]
sheet["B2"], sheet["D4"], sheet[1:3, 0]
```

Formula text and number formats are kept alongside the values:

```{code-cell} python
sheet.formulas("E2:E4").ravel(), sheet.number_formats("C2")
```

## `read_excel`, from the store

```{code-cell} python
xz.read_excel(tmp / "model.zarr", sheet_name="Inputs", index_col=0)
```

It's the same frame pandas gives you from the original file:

```{code-cell} python
import pandas as pd

want = pd.read_excel(tmp / "model.xlsx", sheet_name="Inputs", index_col=0)
got = xz.read_excel(tmp / "model.zarr", sheet_name="Inputs", index_col=0)
pd.testing.assert_frame_equal(got, want)
```

The `next year` column is `NaN` in both. openpyxl wrote those formulas and has no
calculation engine, so the file holds no cached results. A workbook saved by Excel has
them, and both readers would show them.

## Edit in place, and go back to `.xlsx`

```{code-cell} python
live = xz.open_workbook(tmp / "model.zarr", mode="r+")
live["Inputs"]["B3"] = 900_000
live["Inputs"]["A5:D5"] = [["west", 450_000, 0.03, dt.datetime(2026, 4, 1)]]
live["Inputs"]["A2:B5"]
```

```{code-cell} python
xz.zarr_to_xlsx(tmp / "model.zarr", tmp / "roundtrip.xlsx")
ws = openpyxl.load_workbook(tmp / "roundtrip.xlsx")["Inputs"]
ws["E2"].value, ws["C2"].number_format, ws["B3"].value
```

## The fast reader

`engine="calamine"` (the `fast` extra) reads through
[python-calamine](https://github.com/dimastbk/python-calamine), the same Rust engine
behind `pd.read_excel(engine="calamine")`. It's several times faster than openpyxl, but
it keeps values only: no formulas, number formats or layout. It also has its own idea
of dates: a datetime at midnight comes back as a `date`.

```{code-cell} python
fast = xz.xlsx_to_zarr(tmp / "model.xlsx", tmp / "fast.zarr", engine="calamine")
fast["Inputs!A1:D2"]
```
