#!/usr/bin/env python3
"""Run actual unit/integration tests; a success token is emitted only for a nonempty clean run."""
import os
import sys
import unittest
from pathlib import Path
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
suite = unittest.defaultTestLoader.discover(str(Path(__file__).parent), pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=2).run(suite)
passed = result.wasSuccessful() and result.testsRun > 0 and not result.skipped
if passed:
    print(f"DMD_TESTS_PASS:{result.testsRun};skipped=0", flush=True)
raise SystemExit(0 if passed else 1)
