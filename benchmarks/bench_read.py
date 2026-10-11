"""Time getting a spreadsheet's data back, every way we know how.

Builds one ``rows × cols`` sheet of mixed numbers, strings and datetimes, writes it as
``.xlsx``, as HDF (what started all this) and as an excelizarr store, then times:

- ``pd.read_excel`` with openpyxl, and with calamine
- ``pd.read_hdf``
- ``xz.read_excel`` from the zarr store: the same frame pandas gives, by construction
- a ~100-cell range read from the zarr store, which is the cloud-native party trick

Each timing is the best of ``--repeat`` runs. Run with ``pixi run -e bench bench``.
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import excelizarr as xz


def make_frame(rows: int, cols: int, seed: int = 0) -> pd.DataFrame:
    """Build a frame that looks like model inputs: mostly numbers, some labels, some dates."""
    rng = np.random.default_rng(seed)
    data: dict[str, Any] = {}
    for j in range(cols):
        match j % 4:
            case 0:
                data[f"label_{j}"] = rng.choice(
                    ["alpha", "beta", "gamma", "delta"], rows
                )
            case 1:
                data[f"count_{j}"] = rng.integers(0, 10_000, rows)
            case 2:
                data[f"when_{j}"] = pd.Timestamp("2020-01-01") + pd.to_timedelta(
                    rng.integers(0, 3_000, rows), unit="D"
                )
            case _:
                data[f"value_{j}"] = rng.normal(size=rows).round(6)
    return pd.DataFrame(data)


def best_of(fn: Callable[[], object], repeat: int) -> float:
    """Return the fastest of ``repeat`` timed calls, in seconds."""
    times = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return min(times)


def size_of(path: Path) -> int:
    """Total bytes on disk under ``path`` (a file or a directory store)."""
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def main() -> None:
    """Run the benchmark and print a markdown table."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rows", type=int, default=50_000)
    ap.add_argument("--cols", type=int, default=20)
    ap.add_argument("--repeat", type=int, default=3)
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="excelizarr-bench-"))
    try:
        df = make_frame(args.rows, args.cols)
        xlsx, h5, store = tmp / "book.xlsx", tmp / "book.h5", tmp / "book.zarr"

        t0 = time.perf_counter()
        df.to_excel(xlsx, index=False, sheet_name="Inputs")
        print(
            f"wrote {args.rows:,} x {args.cols} xlsx in {time.perf_counter() - t0:.1f}s"
        )
        df.to_hdf(h5, key="Inputs", mode="w")

        convert = {}
        for engine in ("openpyxl", "calamine"):
            t0 = time.perf_counter()
            xz.xlsx_to_zarr(xlsx, store, engine=engine, mode="w")  # type: ignore[arg-type]
            convert[engine] = time.perf_counter() - t0
        xz.xlsx_to_zarr(xlsx, store, mode="w")  # leave the full-fidelity store in place

        wb = xz.open_workbook(store)
        want = pd.read_excel(xlsx, sheet_name="Inputs")
        pd.testing.assert_frame_equal(xz.read_excel(store, sheet_name="Inputs"), want)

        r = args.rows // 2
        rng_ref = f"Inputs!B{r}:E{r + 24}"  # 4 cols x 25 rows = 100 cells
        timings = {
            "pd.read_excel (openpyxl)": best_of(
                lambda: pd.read_excel(xlsx, sheet_name="Inputs"), args.repeat
            ),
            "pd.read_excel (calamine)": best_of(
                lambda: pd.read_excel(xlsx, sheet_name="Inputs", engine="calamine"),
                args.repeat,
            ),
            "pd.read_hdf": best_of(lambda: pd.read_hdf(h5, "Inputs"), args.repeat),
            "xz.read_excel (zarr)": best_of(
                lambda: xz.read_excel(store, sheet_name="Inputs"), args.repeat
            ),
            f"zarr range read ({rng_ref}, 100 cells)": best_of(
                lambda: xz.open_workbook(store)[rng_ref], args.repeat
            ),
        }
        base = timings["pd.read_excel (openpyxl)"]

        print()
        print("| read | seconds | vs openpyxl |")
        print("|---|---:|---:|")
        for name, t in timings.items():
            print(f"| {name} | {t:.4f} | {base / t:,.1f}x |")
        print()
        print("| format | size on disk |")
        print("|---|---:|")
        for name, p in [
            ("xlsx", xlsx),
            ("hdf (fixed)", h5),
            ("excelizarr zarr", store),
        ]:
            print(f"| {name} | {size_of(p) / 1e6:.2f} MB |")
        print()
        print(
            f"conversion xlsx -> zarr: {convert['openpyxl']:.1f}s (openpyxl), "
            f"{convert['calamine']:.1f}s (calamine); sheet is {wb['Inputs'].dimensions}"
        )
    finally:
        shutil.rmtree(tmp)


if __name__ == "__main__":
    main()
