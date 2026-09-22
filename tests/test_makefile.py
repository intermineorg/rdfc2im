"""The Makefile's test targets: one runner, chosen up front, never a failure-triggered retry.

`make test-live` used to be `pytest ... || python3 tests/run.py`. `||` fires on *any* nonzero
exit, so when pytest ran and reported real failures the Makefile re-ran every test under the
bare-bones runner, which cannot honour pytest's skip (it treats the Skipped exception as a crash)
and so buried the real result under an unrelated traceback. The fallback is for "pytest is not
installed", which is decided by importing it, not by whether the tests passed.
"""
import os
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dry_run(target, py="python3"):
    if not shutil.which("make"):
        return None
    return subprocess.run(["make", "-n", target, f"PY={py}"], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout


def _has_pytest(py="python3"):
    """Mirrors the Makefile's own HAVE_PYTEST check: decided by importing it under the same
    `py`, not by whether pytest happens to be installed for whatever interpreter is running
    this test itself."""
    return subprocess.run([py, "-c", "import pytest"], capture_output=True).returncode == 0


def test_test_targets_do_not_retry_under_another_runner_on_failure():
    for target in ("test", "test-live"):
        out = _dry_run(target)
        if out is None:
            return
        assert "||" not in out, f"make {target} re-runs the suite when pytest merely fails:\n{out}"


def test_only_test_live_opts_in_to_live_checks():
    plain, live = _dry_run("test"), _dry_run("test-live")
    if plain is None:
        return
    assert "RDFC2IM_LIVE=1" not in plain
    assert "RDFC2IM_LIVE=1" in live
    # test_live_mine.py is only named when the `python3` HAVE_PYTEST checked has pytest -
    # otherwise both targets fall back to the plain `tests/run.py` runner (see module docstring).
    if _has_pytest("python3"):
        assert "test_live_mine.py" in live
    else:
        assert "tests/run.py" in live
