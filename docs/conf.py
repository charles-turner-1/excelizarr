"""Sphinx configuration for the excelizarr documentation site.

The site is MyST markdown throughout, and the pages carrying worked examples are
*executed* at build time (``myst-nb``), so no output in the docs can drift from what the
code actually does. Warnings are errors: the local loop and CI both run
``sphinx-build -W --keep-going`` via ``pixi run -e docs docs``.
"""

from importlib.metadata import version as _installed_version

# Project information
# -------------------

project = "excelizarr"
author = "Charles Turner"
copyright = "2026, Charles Turner"
release = _installed_version("excelizarr")
version = release.split("+")[0]


# Sources Sphinx may read
# -----------------------
# An allowlist of the site itself: a stray note dropped under ``docs/`` is invisible to
# the builder rather than fatal.

include_patterns = [
    "index.md",
    "getting-started/**",
    "guide/**",
    "api/**",
]


# Extensions
# ----------
# ``myst_nb`` bundles ``myst_parser``; listing both is an error. ``napoleon`` only
# *renders* the numpydoc sections; validating them is the pre-commit hook's job.

extensions = [
    "myst_nb",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx_design",
    "sphinx_copybutton",
]

myst_enable_extensions = ["colon_fence"]
myst_heading_anchors = 3


# Execution (myst-nb)
# -------------------
# ``force`` so every build re-runs the cells, ``raise_on_error`` so a cell that throws
# fails the build rather than leaving a stale page.

nb_execution_mode = "force"
nb_execution_raise_on_error = True
nb_execution_timeout = 120


# API rendering
# -------------

templates_path = ["_templates"]
autosummary_generate = True
autodoc_member_order = "bysource"
autodoc_typehints = "description"
napoleon_google_docstring = False
napoleon_numpy_docstring = True


# Cross-references
# ----------------

intersphinx_mapping = {
    "python": ("https://docs.python.org/3/", None),
    "numpy": ("https://numpy.org/doc/stable/", None),
    "pandas": ("https://pandas.pydata.org/docs/", None),
    "zarr": ("https://zarr.readthedocs.io/en/stable/", None),
}


# HTML output
# -----------

html_theme = "pydata_sphinx_theme"
html_title = "excelizarr"
html_theme_options = {
    "github_url": "https://github.com/charles-turner-1/excelizarr",
    "show_toc_level": 2,
    "navigation_with_keys": False,
}
