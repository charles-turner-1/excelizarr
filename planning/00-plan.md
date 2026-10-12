# excelizarr — a cloud-native representation of an .xlsx

## Context

A friend serialises `.xlsx` workbooks to HDF so a modelling pipeline isn't paying
`pd.read_excel` (zip + XML parse of every sheet) on each intermediate read. The same idea
in Zarr goes one further: the workbook becomes a chunked, cloud-readable store. Sheets
become groups, the cell grid becomes chunked 2D arrays, and `Sheet1!B2:D500` read straight
off S3 fetches only the chunks it touches. Put it in an Icechunk repo and you get git for
spreadsheets too. It's a joke, but it does work. `~/excelizarr` is empty and not a git
repo yet. The project copies the layout of `~/xrexpr`: pixi in `pyproject.toml`,
hypothesis tests, MyST/myst-nb Sphinx docs, versioneer, pre-commit (ruff/black/numpydoc/mypy),
GH Actions CI and RTD. xrexpr's Rust/maturin parts are not copied.

**Is it possible?** Yes. All deps are on conda-forge: zarr 3.4.1, icechunk 2.3.0,
fastexcel 0.21.0 and openpyxl. Excel cells are heterogeneous, so a plain DataFrame→Zarr
dump won't do. The trick is to store each sheet as a set of aligned, typed 2D arrays
plus a `kind` code array. That keeps the HDF-style "fast values" use case and also gives
a real round trip back to `.xlsx` (formulas and number formats included).

## On-disk format (v1)

```
book.zarr/                      group  attrs: excelizarr_format=1, sheet_order, defined_names,
│                                             number_formats=[...], source metadata
└── sheets/
    └── <sheet name>/           group  attrs: shape, merged_ranges, col_widths, freeze_panes,
        │                                     origin (top-left cell, e.g. "A1")
        ├── kind      uint8   (rows, cols)  0 empty,1 number,2 string,3 bool,4 datetime,
        │                                   5 date,6 time,7 duration,8 error
        ├── number    float64               fill NaN
        ├── string    vlen-utf8 / StringDType   fill ""   (strings + error text)
        ├── bool      bool
        ├── temporal  int64 ns since epoch (or ns for time/duration), fill INT64_MIN
        ├── formula   vlen-utf8             fill ""  ("=A1+1"; cached value lives in typed arrays)
        └── numfmt    uint16                index into root number_formats (0 = General)
```

- Chunks default to `(1024, 256)` clipped to the sheet shape. Shards default to
  `(65536, 256)`: on S3 that halved a 500,000-row whole-sheet read (6.1 s → 3.0 s) for
  about 50 ms on a range read. `shards=None` turns them off. Zarr skips chunks that are all fill value, so sparse sheets
  cost almost nothing on disk.
- A sheet name can be any string. Store it under an escaped group key and keep the real
  name in `sheet_order`.
- Accept any zarr `StoreLike`: local path, `memory://`, `s3://…` via fsspec, `ZipStore`
  (an aside in the docs: a zip of XML becomes a zip of chunks), or an Icechunk
  `session.store`.

## Package layout (`~/excelizarr`)

Copied from xrexpr: `pyproject.toml` (setuptools + versioneer instead of maturin; same
ruff/mypy-strict/numpydoc/pytest config; pixi workspace with `default`, `test-py312..314`
and `docs` environments), `.pre-commit-config.yaml`, `.github/workflows/ci.yml`,
`.readthedocs.yaml`, `.gitignore`, `.gitattributes`, `setup.py`, `versioneer.py` (copied
from `~/xrexpr/versioneer.py`), `CLAUDE.md` (pixi task table), `README.md`, `planning/`
(this plan goes there as `planning/00-plan.md`). I'll run `git init` so versioneer has a
VCS.

- Python floor is 3.12, because zarr 3.3 needs it, and 3.3 is what makes a sharded
  read coalesce its chunk requests (`Store.get_ranges`).
- Runtime deps: `zarr>=3.3`, `numpy`, `pandas`, `openpyxl`.
- Extras: `fast = [fastexcel]`, `icechunk = [icechunk]`, `dev`, `docs`. Pixi's test
  feature pulls in both fastexcel and icechunk, and those tests `importorskip`.

`src/excelizarr/`:
| module | role |
|---|---|
| `_refs.py` | A1 refs: `col_to_index`/`index_to_col`, `parse_range("Sheet 1!B2:D10")` → (sheet, r0, r1, c0, c1), whole-column `A:C` ranges |
| `model.py` | `SheetGrid`/`WorkbookModel` dataclasses: the in-memory typed arrays + metadata. Every reader and writer goes through this one neutral form |
| `kinds.py` | `CellKind` IntEnum, fill values, python-value ↔ (kind, slot) encode/decode |
| `io_xlsx.py` | `load_xlsx(path, engine="openpyxl"\|"calamine")`. openpyxl does two passes: `data_only=False` for formulas/formats/merges, then `data_only=True` for cached values. calamine (fastexcel) is values-only and fast. `dump_xlsx(model, path)` writes back via openpyxl |
| `io_zarr.py` | `write_model(model, store, chunks=, shards=, mode=)` and `read_sheet_region(group, r0:r1, c0:c1)`, which builds a `SheetGrid` from only the needed chunks |
| `workbook.py` | lazy `ZarrWorkbook` (`open_workbook(store)`, `.sheet_names`, `wb["Inputs"]`, `wb["Inputs!B2:D10"]`) and `ZarrSheet` (`.shape`, `sheet["B2:D10"]` → object ndarray of Python values, `sheet[1:10, 0:3]`, `.to_pandas(header=0, index_col=None)`, `.formulas(range)`, in-place `sheet["B2"] = 42`, which rewrites one chunk) |
| `api.py` | top-level verbs `xlsx_to_zarr(src, store, **kw)`, `zarr_to_xlsx(store, dst)`, `read_excel(store, sheet_name=0, header=0, usecols=, skiprows=, nrows=, index_col=)`, a drop-in subset of `pd.read_excel` and the pipeline use case |
| `exceptions.py` | `ExcelizarrError`, `InvalidReferenceError`, `FormatVersionError` |

`to_pandas`/`read_excel` copy pandas' openpyxl behaviour: empty → NaN, header row
handling, per-column dtype inference via `pd.DataFrame(...).infer_objects()` and datetime
columns as `datetime64[ns]`. Formula cells with no cached value come back as NaN, which is
what pandas does too.

## Tests (`tests/`, hypothesis-heavy like xrexpr)

- `strategies.py`: valid sheet names (≤31 chars, no `[]:*?/\`, no leading or trailing
  `'`, unique case-insensitively); mixed-type cell grids (finite floats, ints, unicode
  strings without illegal XML control chars, bools, datetimes 1900–9999 at Excel's ms
  resolution, dates, None); simple formulas (`=A1+1`, `=SUM(A1:A3)`); column-homogeneous
  tables for the pandas comparison.
- `test_refs.py`: a property test that `index_to_col` and `col_to_index` are inverse
  bijections, plus range parsing goldens and error cases.
- `test_roundtrip.py` (properties):
  - xlsx → zarr → xlsx keeps every cell's value, type, formula text and number format
    (compared through openpyxl).
  - model → zarr → model is the identity.
  - results don't depend on chunk shape.
- `test_read_excel.py`: `xz.read_excel(zarr)` equals `pd.read_excel(xlsx)`, checked with
  `assert_frame_equal` for header 0/None, `usecols`, `skiprows` and `nrows`.
- `test_regions.py`: any drawn range read equals the matching slice of the full grid.
  Sparse sheets write few chunk objects, checked by counting store keys.
- `test_workbook.py`: lazy API, in-place cell writes, multi-sheet ordering, merged ranges
  and defined names kept.
- `test_calamine.py` (`importorskip("fastexcel")`): values agree with the openpyxl engine.
- `test_icechunk.py` (`importorskip("icechunk")`): write to an in-memory repo, commit,
  edit a cell, commit, then read the old snapshot and get the old value back.

## Docs (`docs/`, MyST + myst-nb executed, `-W`)

Same `conf.py` as xrexpr, without mermaid unless a diagram needs it.
- `index.md`: the joke, the pitch, a 10-line example.
- `getting-started/`: install, convert, read a range, `read_excel`.
- `guide/format.md`: the format spec above.
- `guide/cloud.md`: memory/fsspec stores, `ZipStore`.
- `guide/icechunk.md`: version-controlled spreadsheets.
- `guide/benchmarks.md`: results from `benchmarks/bench_read.py`. The script times
  `pd.read_excel` vs `pd.read_hdf` vs `xz.read_excel` vs a 100-cell range read on a
  ~50k×20 sheet. It runs outside the docs build; the page only shows its numbers.
- `api/`: autosummary.

## Execution order

1. Scaffold: configs, `git init`, `pixi install`, an empty package that imports.
2. `_refs`, `kinds`, `model` plus their tests.
3. `io_xlsx` (openpyxl) and `io_zarr`, then the round-trip properties pass.
4. `workbook` and `api` (`read_excel`, range reads, cell writes), then the pandas
   comparison passes.
5. The calamine engine and the Icechunk tests.
6. Docs and the benchmark script; `pixi run -e docs docs` is green.
7. ruff, black, mypy and pre-commit all clean.

Known risks, decided up front:
- Zarr v3 string dtype differs between numpy versions. Use zarr's `str` / VariableLengthUTF8
  and check against the installed version.
- openpyxl drops cached values for formulas it wrote itself. Tests compare formula text,
  not cached values, for generated workbooks.
- Styles beyond number formats (fonts, fills, borders) are out of scope for v1. They're
  noted in `planning/` as a follow-up: a style table in attrs indexed by a `style` array,
  the same mechanism as `numfmt`.

## Verification

- `pixi run test`: all hypothesis properties pass, with `--hypothesis-show-statistics`.
- `pixi run ruff && pixi run mypy && pixi run pre-commit`.
- `pixi run -e docs docs` builds with warnings as errors, and the executed examples run.
- `pixi run python benchmarks/bench_read.py` prints the timing table. I'll report the
  numbers honestly, including if Zarr loses to HDF on small sheets.
- A manual smoke test: convert a real multi-sheet xlsx, `open_workbook`, read a range,
  `zarr_to_xlsx`, then open the result with openpyxl.

---

## As built (2026-10-11): where the code departed from the plan

- **python-calamine, not fastexcel.** fastexcel's API is column-typed Arrow: it infers
  one dtype per column and coerces mixed columns, which loses exactly the per-cell type
  information a cell grid stores. python-calamine is the same Rust engine with
  cell-level types (it's what `pd.read_excel(engine="calamine")` uses). It ships as the
  `fast` extra.
- **`read_excel` parity by construction.** Rather than re-implementing `header`,
  `usecols`, `skiprows` and so on, `_pandas.ZarrExcelReader` subclasses pandas' private
  `BaseExcelReader` and supplies only `get_sheet_data`. The cost is a dependency on
  pandas internals, confined to one module.
- **Grids are always anchored at A1.** No `origin` attr. Leading empty chunks are never
  written, and pandas' readers assume A1 anyway.
- **Temporal values are int64 microseconds, not datetime64[ns].** Excel's calendar runs
  to 9999, and ns overflows in 2262.
- **`""` is EMPTY.** openpyxl never writes empty strings, and pandas reads them as empty.
- **Metadata is written exactly once per node** (`attributes=` at creation), so
  append-only stores (`ZipStore`) work.
- **Consolidated metadata in the root (format 2), except under Icechunk.** Measured
  against S3, a 100-cell range read was 11 sequential GETs at ~75 ms each: the v2
  probes, a `zarr.json` per node on the way down (`kind`'s twice), then a chunk per
  slot. The writer builds every node's metadata in memory, consolidates it, and writes
  it all before any chunk, so the root is still written once. Growing a sheet in place
  re-consolidates. Readers open as zarr v3 only, and fetch a region's value slots
  concurrently once `kind` says which they are: a range read is now the root's
  `zarr.json`, then `kind`, then the value slots together.

## Found by hypothesis

- openpyxl writes floats with `%.16g`, so a double needing 17 significant digits
  (`1.7976931348623157e308`) is rounded, overflows to `inf` and is written next time as
  an empty `<v/>`. The strategy is narrowed to 15 significant digits, which is Excel's
  own precision.
- `float -> int64` overflowed silently for whole floats ≥ 2⁶³, while pandas gives a
  Python int. Fixed with `model.as_ints`.
- Shard shapes clipped to small sheets weren't multiples of the chunk shape.
- calamine reads whitespace-only inline strings as empty. That's a calamine quirk,
  narrowed out of the engine-agreement property.

## Follow-ups

- Styles (fonts, fills, borders): a style table in the root attrs plus a `style` index
  array, the same mechanism as `numfmt`.
- Sheet-scoped defined names; array-formula refs; data validation; comments.
- A column-typed fast path for whole-sheet `to_pandas` that skips pandas' TextParser.
  It would close some of the gap to HDF, at the price of the parity-by-construction
  guarantee.
- Benchmark against real object storage (S3 latency, sharded vs unsharded).
