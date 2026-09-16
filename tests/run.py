"""Minimal test runner (used when pytest is not installed): `python3 tests/run.py`."""
import importlib, inspect, os, pathlib, sys, tempfile, traceback
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
here = os.path.dirname(os.path.abspath(__file__))
failed = passed = 0
for f in sorted(os.listdir(here)):
    if not (f.startswith("test_") and f.endswith(".py")):
        continue
    mod = importlib.import_module(f[:-3]) if here in sys.path else None
    if mod is None:
        sys.path.insert(0, here); mod = importlib.import_module(f[:-3])
    for name, fn in inspect.getmembers(mod, inspect.isfunction):
        if not name.startswith("test_"):
            continue
        kwargs = {}
        if "tmp_path" in inspect.signature(fn).parameters:
            kwargs["tmp_path"] = pathlib.Path(tempfile.mkdtemp())
        try:
            fn(**kwargs); passed += 1; print(f"PASS {f}::{name}")
        except Exception:
            failed += 1; print(f"FAIL {f}::{name}"); traceback.print_exc()
print(f"{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
