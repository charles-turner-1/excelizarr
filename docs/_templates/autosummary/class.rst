{{ fullname | escape | underline }}

.. currentmodule:: {{ module }}

.. autoclass:: {{ objname }}
   :members:
   :special-members: __getitem__, __setitem__

.. ``:members:`` rather than the stock summary table, so methods render with their
.. signatures and docstrings. ``__getitem__``/``__setitem__`` are named because on
.. ``ZarrSheet`` and ``ZarrWorkbook`` indexing *is* the API.
