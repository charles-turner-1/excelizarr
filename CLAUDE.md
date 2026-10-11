# excelizarr

## Environment: pixi, not uv

The project environment is **pixi** (configured in `pyproject.toml` under `[tool.pixi.*]`,
locked by `pixi.lock`), and it is what CI runs. Run everything through it:

| Task | Command |
| --- | --- |
| Tests | `pixi run test` |
| Tests, more hypothesis examples | `HYPOTHESIS_PROFILE=ci pixi run test` |
| Tests with coverage | `pixi run test-cov` |
| A single file / test | `pixi run pytest tests/test_roundtrip.py -q` |
| Tests without calamine/icechunk | `pixi run -e test-minimal test` |
| A scratch snippet | `pixi run python -c "..."` |
| Lint | `pixi run ruff` |
| Format | `pixi run black` |
| Types | `pixi run mypy` |
| Hooks | `pixi run pre-commit` |
| Docs | `pixi run -e docs docs` |
| Benchmark | `pixi run -e bench bench` |

`python-calamine` and `icechunk` are optional extras (`fast`, `icechunk`). excelizarr must
work without either, and their tests `importorskip` accordingly.

## Where things are

- `src/excelizarr/model.py` is the neutral in-memory form. Readers and writers on both
  sides (`io_xlsx.py`, `io_zarr.py`) only ever talk to it, never to each other.
- `src/excelizarr/_pandas.py` plugs into pandas' private `BaseExcelReader`. That is
  deliberate (it is how `read_excel` parity comes for free), and it is the one module to
  check when a pandas release breaks things.
- The on-disk format is documented in `docs/guide/format.md` and the `io_zarr` module
  docstring. Bump `FORMAT_VERSION` for any change an older reader would misread.
- `planning/` holds design notes, not user docs.
