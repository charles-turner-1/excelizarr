# Benchmarks

These come from `benchmarks/bench_read.py` (`pixi run -e bench bench`). The test sheet
is 50,000 rows × 20 columns of labels, integers, dates and floats: a million cells,
written by `DataFrame.to_excel`. Each figure is the best of three runs on an Apple
A18 Pro laptop, from a local disk. The script isn't run as part of the docs build, so
re-run it on your own machine before believing any of this.

| read | seconds | vs openpyxl |
|---|---:|---:|
| `pd.read_excel` (openpyxl) | 2.972 | 1.0× |
| `pd.read_excel` (calamine) | 0.628 | 4.7× |
| `pd.read_hdf` | 0.017 | 173× |
| `xz.read_excel` (zarr) | 0.218 | 13.6× |
| zarr range read, 100 cells (`Inputs!B25000:E25024`) | 0.004 | 680× |

| format | size on disk |
|---|---:|
| xlsx | 6.25 MB |
| HDF (`fixed`) | 13.61 MB |
| excelizarr | 4.03 MB |

Converting the `.xlsx` to zarr takes 7.6 s with openpyxl (the full-fidelity reader) and
1.4 s with calamine. You pay that once.

## What this says

**For whole-sheet reads, HDF wins, by about 13×.** A `fixed`-format HDF file is the
DataFrame's typed columns written straight to disk, and reading it back does almost
nothing. excelizarr stores *cells* rather than columns. It decodes them to Python
objects and hands them to pandas' own Excel parser, because that's how `read_excel`
parity comes for free. That parser, plus decompression, is where the 0.2 s goes. If all
you want is "this sheet, as a DataFrame, fast", your mate's HDF trick is the right one.

**It still beats parsing the `.xlsx`**: about 14× faster than openpyxl and 3× faster
than calamine, with an identical frame at the end.

**Range reads are where the format pays off.** A hundred cells out of a million takes
4 ms. It only touches the chunks under the range, and that's on local disk. Against
object storage, the `.xlsx` and HDF files have to be downloaded whole (HDF isn't
designed for partial reads over HTTP), while the zarr store fetches a few small objects.

**It's the smallest of the three.** Zarr compresses each slot separately, and slots a
sheet doesn't use cost nothing. The HDF file is twice the size of the `.xlsx`, mostly
because its label columns are stored as pickled object arrays.

## What isn't measured

- Object-storage latency. Those numbers depend on the bucket, the region and the
  concurrency settings much more than on the format; one set of S3 measurements, with
  and without sharding, is in [Stores, local and otherwise](cloud.md).
- Multi-sheet workbooks. `read_excel` on one sheet of a many-sheet `.xlsx` still pays
  to open the zip, while a zarr store doesn't touch the other sheets at all.
