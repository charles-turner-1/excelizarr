# excelizarr

**A cloud-native representation of an `.xlsx` workbook. In Zarr. Yes, really.**

:::{warning}
This is a bit of fun. It works, it's tested (property-tested, even), and you probably
still shouldn't put it anywhere important. Claude wrote most of the code at my
direction. Read the claims here as "tested on what the test suite generates", no more.
:::

## Why would anyone do this

A friend's modelling pipeline read the same spreadsheets over and over, and
`pd.read_excel` is slow. It has to unzip the file, parse the XML for every sheet, and
turn every cell into a Python object before it can start building a frame. So he
converted the workbooks to HDF once and read those instead. That's a good idea.

It also raises an obvious question: if a workbook is going to become an array file
anyway, why not make it a *Zarr* file? Then it's chunked, compressed and
cloud-readable. You can read `Inputs!B2:D500` straight off S3 by fetching just the
chunks that range covers. Put the store in [Icechunk](https://icechunk.io) and you've
got a spreadsheet with git-style version control.

So that's what this is.

```python
import excelizarr as xz

wb = xz.xlsx_to_zarr("model.xlsx", "s3://bucket/model.zarr")

wb["Inputs!B2:D10"]                          # a 9x3 object array, a few small GETs
df = xz.read_excel("s3://bucket/model.zarr", sheet_name="Inputs")  # == pd.read_excel
xz.zarr_to_xlsx("s3://bucket/model.zarr", "roundtrip.xlsx")       # formulas intact
```

## What you get

::::{grid} 1 1 2 2
:gutter: 2

:::{grid-item-card} The same frame as `pd.read_excel`
`xz.read_excel` runs pandas' own reader over the stored cells, so `header`, `usecols`,
`skiprows`, `index_col` and the rest behave identically. A hypothesis property checks
that the frames are equal, dtypes included.
:::

:::{grid-item-card} Range reads off object storage
A sheet is a set of chunked 2D arrays, so a range read touches only the chunks under
it. Empty space is never written, so a sparse sheet stays small.
:::

:::{grid-item-card} A round trip, not a dump
Formulas, number formats, merged cells, column widths, frozen panes and defined names
all survive xlsx → zarr → xlsx.
:::

:::{grid-item-card} Spreadsheets with branches
Any zarr store works, Icechunk included. Commit the workbook, edit a cell, and read
yesterday's inputs from the snapshot. Run what-if scenarios on branches.
:::

::::

```{toctree}
:maxdepth: 2
:hidden:

getting-started/quickstart
guide/format
guide/cloud
guide/icechunk
guide/benchmarks
api/index
```
