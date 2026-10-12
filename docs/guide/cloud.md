---
file_format: mystnb
kernelspec:
  name: python3
---

# Stores, local and otherwise

Everything that takes a store takes anything zarr does.

```python
xz.xlsx_to_zarr("model.xlsx", "model.zarr")                        # a directory
xz.xlsx_to_zarr("model.xlsx", "s3://bucket/model.zarr",            # object storage
                storage_options={"anon": False})
xz.xlsx_to_zarr("model.xlsx", zarr.storage.MemoryStore())          # memory
xz.xlsx_to_zarr("model.xlsx", icechunk_session.store)              # see the Icechunk page
```

URL stores need `fsspec` and the matching filesystem package (`s3fs`, `gcsfs`, ...).
excelizarr itself never imports them.

## The expensive part happens once

With an `.xlsx` in a bucket, every read downloads the whole file, unzips it, and parses
the XML for the sheet you want, even if you only wanted one cell. With a zarr store,
`open_workbook` reads one document, the root's `zarr.json`, which carries every node's
metadata. A range read then fetches just the chunks the range overlaps, from just the
slots it uses: `kind` first, to learn which slots that is, then all of those at once.
However many slots a range touches, that's two round trips to the store.

```{code-cell} python
import openpyxl
import tempfile
from pathlib import Path

import zarr
import excelizarr as xz

tmp = Path(tempfile.mkdtemp())
book = openpyxl.Workbook()
ws = book.active
ws.title = "Big"
for r in range(1, 20_001):
    ws.append([r, r * 0.5, f"row {r}"])
book.save(tmp / "big.xlsx")


class CountingStore(zarr.storage.WrapperStore):
    """Count the objects a read fetches."""

    gets = 0

    async def get(self, key, prototype, byte_range=None):
        type(self).gets += 1
        return await self._store.get(key, prototype, byte_range)


store = zarr.storage.LocalStore(tmp / "big.zarr")
xz.xlsx_to_zarr(tmp / "big.xlsx", store, chunks=(1024, 3))

counted = CountingStore(zarr.storage.LocalStore(tmp / "big.zarr", read_only=True))
wb = xz.open_workbook(counted)
cells = wb["Big!A15000:B15010"]
CountingStore.gets, cells.shape
```

That's the root's `zarr.json`, plus one chunk each from `kind` and `number`. Column
`C` holds the strings and is never touched, and nor are the other nineteen row-chunks
of the sheet.

## Whole sheets

Reading a whole sheet, as `read_excel` and `to_model` do, means reading every chunk of
every slot it uses. zarr keeps `async.concurrency` chunk requests in flight per array,
and its default of 10 suits local disk; from an object store, a sheet of a few hundred
chunks would then take a few dozen round trips one after another. excelizarr raises
that limit to at least `excelizarr.io_zarr.CONCURRENCY` (64) for its own reads and
writes. A higher limit set in zarr's config is left alone:

```python
zarr.config.set({"async.concurrency": 256})   # or ZARR_ASYNC_CONCURRENCY=256
```

## A zip of XML becomes a zip of chunks

An `.xlsx` is a zip of XML files. Because zarr can live in a zip as well, so can an
excelizarr workbook, as one portable file:

```{code-cell} python
with zarr.storage.ZipStore(tmp / "big.zarr.zip", mode="w") as zs:
    xz.xlsx_to_zarr(tmp / "big.xlsx", zs)

with zarr.storage.ZipStore(tmp / "big.zarr.zip", mode="r") as zs:
    print(xz.open_workbook(zs)["Big"]["C20000"])

{p.name: f"{p.stat().st_size / 1e3:.0f} kB" for p in [tmp / "big.xlsx", tmp / "big.zarr.zip"]}
```

Every node's metadata is written exactly once, which is what lets a `ZipStore` (which
can't overwrite an entry) hold a workbook. In-place cell writes need a store that *can*
overwrite, so open a directory or object store for those.
