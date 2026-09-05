#!/usr/bin/env python3
from __future__ import annotations
import os, sys
from pathlib import Path
target = Path(__file__).resolve().parents[1] / "src" / "context_zip.py"
os.execvp("python3", ["python3", str(target), "--stack", "spring", *sys.argv[1:]])
