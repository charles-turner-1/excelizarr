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
Chunks live in shards (see below), so each of those steps reads the shard's small
index and then the chunk it points to.

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
    """Count the requests a read makes."""

    gets = 0
    get_ranges = zarr.abc.store.Store.get_ranges  # ranged reads go through get() too

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

That's the root's `zarr.json`, then for each of `kind` and `number` the shard's index
and one chunk. Column `C` holds the strings and is never touched, and nor are the other
nineteen row-chunks of the sheet.

## Whole sheets

Reading a whole sheet, as `read_excel` and `to_model` do, means reading every chunk of
every slot it uses. Two things keep that to a handful of requests:

- **Shards.** By default each slot is packed into objects of 65,536 rows
  (`excelizarr.io_zarr.DEFAULT_SHARDS`), 64 chunks apiece, so a whole-sheet read is a
  request per shard rather than per chunk. Pass `shards=None` to write every chunk as
  its own object, or a different `(rows, cols)` to change the size.
- **Concurrency.** zarr keeps `async.concurrency` requests in flight per array, 10 by
  default. excelizarr raises that to at least `excelizarr.io_zarr.CONCURRENCY` (64)
  for its own reads and writes; a higher limit set in zarr's config is left alone.

On S3, also raise s3fs's connection pool. It's botocore's default of 10, whatever zarr
asks for, and requests past it wait their turn:

```python
xz.read_excel("s3://bucket/big.zarr", storage_options={
    "config_kwargs": {"max_pool_connections": 64},
})
```

What shards cost: a range read reads each slot's shard index before its chunk, and an
in-place cell write rewrites the whole shard (a few MB at most) rather than one chunk.

Measured from a laptop in Australia against S3 in `ap-southeast-2`, on a 500,000 × 20
sheet (40 MB as zarr, best of 3, pool at 64):

| | sharded (default) | `shards=None` |
| --- | ---: | ---: |
| `xz.read_excel`, whole sheet | 2.96 s | 6.07 s |
| open + 100-cell range read | 0.48 s | 0.43 s |

For scale, the same sheet read locally with `xz.read_excel` takes 2.3 s, and
downloading its HDF file and reading that takes 2.0 s. The 100-cell range read stays
about the same at 10,000 rows (0.49 s): it doesn't grow with the sheet.

## Half the requests, with obstore

A URL string goes through fsspec, and s3fs sends a `HEAD` before every `GET` to learn
the object's size. [obstore](https://developmentseed.org/obstore/) doesn't, so wrapping
it in zarr's `ObjectStore` halves the requests. On a 20-row workbook in S3, read from
Australia against `ap-southeast-2`, best of 7:

| | requests | range read | `read_excel` |
| --- | --- | ---: | ---: |
| `"s3://..."` (s3fs) | 4 `HEAD` + 4 `GET` | 0.43 s | 0.42 s |
| `ObjectStore` (obstore) | 4 `GET` | 0.23 s | 0.23 s |

Install the `obstore` extra, and pass the store wherever a URL would go:

```python
import boto3
import zarr
from obstore.auth.boto3 import Boto3CredentialProvider
from obstore.store import S3Store

s3 = S3Store.from_url(
    "s3://bucket/model.zarr",
    region="ap-southeast-2",
    credential_provider=Boto3CredentialProvider(boto3.Session(profile_name="me")),
)
store = zarr.storage.ObjectStore(s3)
xz.xlsx_to_zarr("model.xlsx", store)
xz.read_excel(store, sheet_name="Inputs")
```

obstore reads credentials from environment variables, web identity and instance
metadata, but not from AWS profiles or SSO. With a profile, hand it boto3's credentials
as above. Without one, it falls through to instance metadata, which off EC2 takes
several seconds to fail. That's also why excelizarr doesn't switch URL strings to
obstore by itself.

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
