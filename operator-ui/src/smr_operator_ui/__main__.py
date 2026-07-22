"""Command-line entry point for the SMR operator UI.

This module supports both ``python -m smr_operator_ui`` and direct execution
from an IDE, which commonly runs this file by its absolute path.
"""

from __future__ import annotations

import sys
from pathlib import Path


if __package__ in (None, ""):
    # Direct file execution puts the package directory (rather than ``src``)
    # on sys.path, so make the src-layout package importable first.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smr_operator_ui.app import main

raise SystemExit(main())
