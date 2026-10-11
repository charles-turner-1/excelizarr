---
file_format: mystnb
kernelspec:
  name: python3
---

# Version-controlled spreadsheets

[Icechunk](https://icechunk.io) is a transactional storage engine for zarr. It gives
you commits, branches, tags and snapshot isolation. Its session's `store` is an
ordinary zarr store, so excelizarr works on it unchanged.

This is the moment the joke turns useful. A modelling pipeline's inputs workbook is
the thing everyone edits and nobody versions.

```{code-cell} python
import tempfile
from pathlib import Path

import icechunk
import openpyxl
import excelizarr as xz

tmp = Path(tempfile.mkdtemp())
book = openpyxl.Workbook()
ws = book.active
ws.title = "Assumptions"
for row in [("parameter", "value"), ("discount_rate", 0.035), ("growth", 0.02), ("horizon", 30)]:
    ws.append(row)
book.save(tmp / "assumptions.xlsx")

# in memory for the docs; use icechunk.s3_storage(...) (or gcs, azure, local) for real
repo = icechunk.Repository.create(icechunk.in_memory_storage())
session = repo.writable_session("main")
xz.xlsx_to_zarr(tmp / "assumptions.xlsx", session.store, mode="w")
baseline = session.commit("import assumptions.xlsx")
```

Someone changes the discount rate:

```{code-cell} python
session = repo.writable_session("main")
xz.open_workbook(session.store, mode="r+")["Assumptions"]["B2"] = 0.05
revised = session.commit("treasury says 5%")

[(s.message, s.id[:8]) for s in repo.ancestry(branch="main")]
```

Both versions are still there:

```{code-cell} python
then = xz.read_excel(repo.readonly_session(snapshot_id=baseline).store, index_col=0)
now = xz.read_excel(repo.readonly_session("main").store, index_col=0)
then.join(now, lsuffix="_then", rsuffix="_now")
```

## Scenarios as branches

```{code-cell} python
repo.create_branch("pessimistic", snapshot_id=revised)
session = repo.writable_session("pessimistic")
xz.open_workbook(session.store, mode="r+")["Assumptions"]["B3"] = -0.01
session.commit("what if it shrinks")

{
    branch: xz.read_excel(repo.readonly_session(branch).store, index_col=0)["value"].to_dict()
    for branch in ["main", "pessimistic"]
}
```

A cell write rewrites only the chunks it touches, and an Icechunk commit stores only the
chunks that changed. So a snapshot of a one-cell edit costs a few small objects, not a
copy of the workbook.
