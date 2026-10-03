#!/usr/bin/env python3
"""Run from either host without installing a global command."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ihav_competitor_search.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
