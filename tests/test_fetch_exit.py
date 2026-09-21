"""`rdfc2im fetch` must not exit 0 when a query failed.

do_fetch() used to discard every fetch_source() status, so a query that hit a network error
("failed") or lost some of its pages ("partial") still ended in `return 0`. Nothing downstream
notices: `tsv` skips a table whose raw file is missing without complaint, and a partial file is
just a shorter file - so the build carries on to a mine with a table quietly absent or truncated.
tools/full-build.sh runs under `set -e`, so a nonzero exit is exactly the signal that stops it;
the checksummed raw-data cache also relies on it, so as never to record an incomplete fetch as
the data to reuse.
"""
import contextlib
import io
import os

from rdfc2im import cli


def _fetch(tmp_path, statuses):
    """Run `rdfc2im fetch --source go` in a scratch workspace whose fetch_source reports `statuses`.

    Returns (exit code, stderr). Patches by hand rather than with pytest's monkeypatch so this
    also runs under tests/run.py, which only knows tmp_path.
    """
    (tmp_path / "cfg" / "go").mkdir(parents=True)
    (tmp_path / "rdfc2im.yaml").write_text("config_root: cfg\nout: out\nsources: [go]\n")
    old_cwd, old_fetch = os.getcwd(), cli.fetch_source
    os.chdir(tmp_path)
    cli.fetch_source = lambda *a, **k: statuses
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            rc = cli.main(["fetch", "--source", "go"])
    finally:
        cli.fetch_source = old_fetch
        os.chdir(old_cwd)
    return rc, err.getvalue()


def test_fetch_exits_nonzero_when_a_query_failed(tmp_path):
    assert _fetch(tmp_path, {"main": "fetched", "main_synonym": "failed"})[0] != 0


def test_fetch_exits_nonzero_when_a_query_came_back_partial(tmp_path):
    assert _fetch(tmp_path, {"main": "partial"})[0] != 0


def test_fetch_exits_zero_when_everything_fetched_kept_or_skipped(tmp_path):
    ok = {"a": "fetched", "b": "kept", "c": "skip-empty", "d": "skip-endpoint", "e": "dry"}
    assert _fetch(tmp_path, ok) == (0, "")


def test_fetch_reports_every_bad_table_not_just_the_first(tmp_path):
    _, err = _fetch(tmp_path, {"a": "failed", "b": "fetched", "c": "partial"})
    assert "go/a" in err and "go/c" in err and "go/b" not in err
