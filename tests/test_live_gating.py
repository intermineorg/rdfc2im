"""The live checks in test_live_mine.py must be opt-in, not opt-out.

They used to run whenever *anything* answered on MINE_BASE. On 2026-09-21 a stale, hand-built mine
from an earlier session was still running in the sandbox, so plain `make test` - documented as an
offline suite that stays green on a machine that has only ever checked out the repo - failed six
tests against a mine that tools/full-build.sh had never built. "A mine is reachable" is not the
same as "the person running the tests wants them pointed at it".

Live checks now run only when RDFC2IM_LIVE=1 (which `make test-live` sets).
"""
import http.server
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_live_mine  # noqa: E402


class _Answers200(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):  # keep test output clean
        pass


def _with_fake_mine(fn):
    """Run fn(base_url) with a server that answers 200 to everything - a reachable mine."""
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Answers200)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    old_mine, old_solr = test_live_mine.MINE_BASE, test_live_mine.SOLR_BASE
    base = f"http://127.0.0.1:{srv.server_port}"
    test_live_mine.MINE_BASE = test_live_mine.SOLR_BASE = base
    try:
        return fn(base)
    finally:
        test_live_mine.MINE_BASE, test_live_mine.SOLR_BASE = old_mine, old_solr
        srv.shutdown()


def _env(value):
    old = os.environ.pop("RDFC2IM_LIVE", None)
    if value is not None:
        os.environ["RDFC2IM_LIVE"] = value
    return old


def _restore(old):
    os.environ.pop("RDFC2IM_LIVE", None)
    if old is not None:
        os.environ["RDFC2IM_LIVE"] = old


def test_reachable_mine_is_ignored_unless_live_is_requested():
    old = _env(None)
    try:
        assert _with_fake_mine(lambda _: test_live_mine.mine_up()) is False
        assert _with_fake_mine(lambda _: test_live_mine.solr_up()) is False
    finally:
        _restore(old)


def test_live_flag_enables_the_checks_against_a_reachable_mine():
    old = _env("1")
    try:
        assert _with_fake_mine(lambda _: test_live_mine.mine_up()) is True
        assert _with_fake_mine(lambda _: test_live_mine.solr_up()) is True
    finally:
        _restore(old)


def test_live_flag_still_skips_when_nothing_is_listening():
    old = _env("1")
    saved = test_live_mine.MINE_BASE, test_live_mine.SOLR_BASE
    test_live_mine.MINE_BASE = test_live_mine.SOLR_BASE = "http://127.0.0.1:9"  # discard port
    try:
        assert test_live_mine.mine_up() is False
        assert test_live_mine.solr_up() is False
    finally:
        test_live_mine.MINE_BASE, test_live_mine.SOLR_BASE = saved
        _restore(old)
