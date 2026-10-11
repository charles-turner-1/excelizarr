# excelizarr

**A cloud-native representation of an `.xlsx` workbook. In Zarr. Yes, really.**

> [!WARNING]
> This is a bit of fun. It works and it's property-tested, but please don't put it
> anywhere important. Claude wrote most of the code at my direction.

A friend made me realise you could save an `.xlsx` spreadsheet as an HDF5 file. That
raised an obvious question: why not Zarr? Then a workbook is chunked, compressed and
cloud-readable, and with [Icechunk](https://icechunk.io) it's version-controlled too.

```python
import excelizarr as xz

wb = xz.xlsx_to_zarr("model.xlsx", "s3://bucket/model.zarr")

wb["Inputs!B2:D10"]                                               # fetches a few chunks
df = xz.read_excel("s3://bucket/model.zarr", sheet_name="Inputs")  # == pd.read_excel
xz.zarr_to_xlsx("s3://bucket/model.zarr", "roundtrip.xlsx")       # formulas intact
```

## How it works

Each sheet becomes a zarr group of aligned, chunked 2D arrays:

- `kind`: which type each cell holds
- one array per value type: `number`, `string`, `bool`, `temporal`
- `formula` and `numfmt`

Zarr never writes a chunk that's all fill value, so unused types and empty space cost
nothing. A range read fetches only the chunks it overlaps. See
[the format](docs/guide/format.md).

- **`read_excel` parity.** `xz.read_excel` runs pandas' own Excel parser over the stored
  cells, so every keyword behaves the same, and a hypothesis property checks the frames
  match.
- **Round trips.** Formulas, number formats, merged cells, column widths, frozen panes
  and defined names all survive xlsx → zarr → xlsx.
- **Any zarr store.** Local, `s3://`, in memory, a `ZipStore`, or an Icechunk session.
- **In-place edits.** `wb["Inputs"]["B2"] = 42` rewrites one chunk.

## Is it fast?

On a 50,000 × 20 sheet ([details](docs/guide/benchmarks.md)):

| read | seconds |
|---|---:|
| `pd.read_excel` (openpyxl) | 2.97 |
| `pd.read_excel` (calamine) | 0.63 |
| `pd.read_hdf` | **0.017** |
| `xz.read_excel` | 0.22 |
| 100-cell range read | **0.004** |

So HDF is still the right call for "the whole sheet as a DataFrame, fast". excelizarr
is 14× faster than parsing the `.xlsx`, smaller on disk than either, and much better
at the cloud-shaped jobs: range reads, partial access and version control.

## Development

```bash
pixi run test             # hypothesis-heavy test suite
pixi run -e docs docs     # executed MyST docs, warnings are errors
pixi run -e bench bench   # the benchmark above
```

## Licence

[The Not Fit For Purpose Licence](LICENSE), which is the Apache License 2.0 with a
preamble. This tool is not fit for purpose. I don't care what your purpose is, this
isn't it.
