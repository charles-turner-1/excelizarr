---
file_format: mystnb
kernelspec:
  name: python3
---

# The on-disk format

Excel cells are heterogeneous: one column can hold a number, a label and a `#DIV/0!`.
Zarr arrays hold one type each. So a sheet isn't stored as one array. It's stored as
**several aligned 2D arrays of the same shape**, one per storage *slot*, plus a `kind`
array that says which slot holds each cell's value.

```text
book.zarr/                       attrs["excelizarr"]: format, sheets [{name, key}],
│                                                     number_formats, defined_names
└── sheets/
    └── <key>/                   attrs["excelizarr"]: name, merged, col_widths, freeze_panes
        ├── kind      uint8      CellKind code per cell
        ├── number    float64    ints and floats              (fill: NaN)
        ├── string    str        strings, error literals      (fill: "")
        ├── bool      bool                                    (fill: False)
        ├── temporal  int64      microseconds                 (fill: INT64_MIN)
        ├── formula   str        "=B2*2", "" for none         (fill: "")
        └── numfmt    uint16     index into number_formats    (fill: 0 = "General")
```

Every array has dimensions `("row", "col")` and is anchored at `A1`, so cell `B3` is
`[2, 1]` in every one of them.

The root's `zarr.json` also holds zarr's *consolidated metadata*: a copy of every
node's metadata above, so opening the root opens the whole tree. On an object store
that turns "open the workbook, find the sheet, open each array" from a request per node
into one request. Each node keeps its own `zarr.json` as well, so a zarr reader that
doesn't use consolidated metadata (it isn't in the zarr v3 spec yet) still reads the
store. Stores in an Icechunk repository don't carry it: Icechunk keeps its own metadata,
and opening a node there is already cheap.

Every node's metadata is still written exactly once. The writer builds the whole tree's
metadata in memory, consolidates it there, and writes every `zarr.json` (the root's
included) before any chunk, which is what lets an append-only `ZipStore` hold a
workbook.

## Cell kinds

```{code-cell} python
import excelizarr as xz

{k.name: int(k) for k in xz.CellKind}
```

| kind | slot | notes |
|---|---|---|
| `EMPTY` | none | `None`, `NaN` and `""` are all empty, which is how Excel and pandas see them |
| `INT`, `FLOAT` | `number` | Excel stores every number as a double; `INT` only remembers it was whole |
| `STRING` | `string` | |
| `ERROR` | `string` | the literal: `#N/A`, `#DIV/0!`, ... |
| `BOOL` | `bool` | |
| `DATETIME`, `DATE` | `temporal` | µs since 1970-01-01. Microseconds, not nanoseconds, because Excel's calendar runs to 9999 and `datetime64[ns]` stops in 2262 |
| `TIME` | `temporal` | µs since midnight |
| `DURATION` | `temporal` | µs |

A formula cell has its formula text in `formula`, and its `kind` describes the formula's
**cached result**, which is what pandas would show you. A formula with no cached value
is `EMPTY` with a formula.

## Why this layout is cheap

Zarr never writes a chunk that is entirely fill value. A slot the sheet doesn't use
(`bool` in a sheet with no booleans, say) costs one small `zarr.json` and no chunks. The
same goes for the empty half of a sparse sheet. Here's one value in the far corner of a
100,000 × 1,000 sheet:

```{code-cell} python
import zarr
from excelizarr.io_zarr import write_model
from excelizarr.model import SheetGrid, WorkbookModel

grid = SheetGrid.empty("Sparse", (100_000, 1_000))
grid.set_cell(99_999, 999, "here")
store = zarr.storage.MemoryStore()
write_model(WorkbookModel([grid]), store)
sorted(k for k in store._store_dict if "/c/" in k)
```

That's a hundred million cells, and two chunks: one in `kind` and one in `string`.

Reads work the same way in reverse. A range read fetches `kind` for the chunks under
the range, and then only the slots those cells actually use.

## Sheet names and keys

A sheet's group is named by its *key*: the sheet name, percent-escaped just enough to be
a legal zarr node name. The real name and the tab order live in the root's `sheets`
list.

```{code-cell} python
from excelizarr.io_zarr import sheet_key

[sheet_key(n) for n in ["Inputs", "Q1 '24", "100%", "__private"]]
```

## Chunking

Chunks default to 1024 rows × 256 columns, clipped to the sheet. Spreadsheets are long
and narrow, and most reads want whole columns. For big sheets on object storage, pass
`shards=` to pack many chunks into one object:

```python
xz.xlsx_to_zarr("big.xlsx", "s3://bucket/big.zarr", chunks=(4096, 64), shards=(65536, 256))
```

## Versioning

The root's `attrs["excelizarr"]["format"]` is the format version, currently `2`.
Readers refuse a store from a newer format rather than misreading it.

| format | change |
|---|---|
| 1 | the layout above |
| 2 | consolidated metadata in the root |

Format-2 readers read format-1 stores as they always were. Format-1 readers refuse
format-2 stores: they don't know about the root's copy of the metadata, so growing a
sheet in place would leave it stale.

## What isn't stored (yet)

Fonts, fills, borders and conditional formatting. Charts, images, comments, data
validation and pivot tables. Sheet-scoped defined names. Array formulas keep their text
but lose their array-ness. Styles would take the same mechanism `numfmt` uses: a table
in the root attrs, and a `style` array of indices into it.
