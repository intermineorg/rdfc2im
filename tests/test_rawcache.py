"""cached_raw_data/: checksummed save/restore of fetched data (rdfc2im/rawcache.py).

The two failure modes it is built around are each pinned by a test below: a copy that silently
comes out wrong (the virtiofs mount zeroed files under plain `cp`), and restoring data that was
fetched for a different scope than the build now needs.
"""
import hashlib
import os
import shutil
import subprocess
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rdfc2im import rawcache  # noqa: E402
from rdfc2im.rawcache import CacheError  # noqa: E402


def _raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc as e:
        return str(e)
    raise AssertionError(f"expected {exc.__name__}")


def _make(d, files):
    for rel, data in files.items():
        p = os.path.join(d, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)


def _read_tree(d):
    return {rel: open(os.path.join(d, rel), "rb").read() for rel in rawcache._walk(d)}


FILES = {"main.tsv": b"?a\t?b\n1\t2\n" * 1000, "main_synonym.tsv": b"?a\n\xce\xb1\n", "empty.tsv": b""}


# ------------------------------------------------------------------ round trip

def test_save_then_restore_reproduces_the_files_exactly(tmp_path):
    src, cache, dest = tmp_path / "src", tmp_path / "cache", tmp_path / "dest"
    _make(src, FILES)
    info = rawcache.save("go", str(src), str(cache), {"scope": "none"})
    assert info["files"] == 3 and info["meta"] == {"scope": "none"}
    rawcache.restore("go", str(dest), str(cache), {"scope": "none"})
    assert _read_tree(str(dest)) == FILES


def test_manifest_is_md5sum_compatible(tmp_path):
    src, cache = tmp_path / "src", tmp_path / "cache"
    _make(src, FILES)
    rawcache.save("go", str(src), str(cache))
    text = (cache / "go" / "MD5SUMS").read_text()
    assert f"{hashlib.md5(FILES['main.tsv']).hexdigest()}  data/main.tsv\n" in text
    if shutil.which("md5sum"):
        r = subprocess.run(["md5sum", "-c", "MD5SUMS"], cwd=cache / "go", capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr


def test_restore_removes_stale_files_from_the_destination(tmp_path):
    src, cache, dest = tmp_path / "src", tmp_path / "cache", tmp_path / "dest"
    _make(src, FILES)
    rawcache.save("go", str(src), str(cache))
    _make(dest, {"leftover_from_old_run.tsv": b"stale", "sub/also.tsv": b"stale"})
    rawcache.restore("go", str(dest), str(cache))
    assert _read_tree(str(dest)) == FILES
    assert not os.path.exists(dest / "sub")


def test_resave_replaces_the_previous_entry(tmp_path):
    src, cache, dest = tmp_path / "src", tmp_path / "cache", tmp_path / "dest"
    _make(src, {"a.tsv": b"old"})
    rawcache.save("go", str(src), str(cache))
    shutil.rmtree(src)
    _make(src, {"b.tsv": b"new"})
    rawcache.save("go", str(src), str(cache))
    rawcache.restore("go", str(dest), str(cache))
    assert _read_tree(str(dest)) == {"b.tsv": b"new"}


# ------------------------------------------------------------------ scope must match

def test_restore_refuses_data_fetched_for_a_different_scope(tmp_path):
    src, cache, dest = tmp_path / "src", tmp_path / "cache", tmp_path / "dest"
    _make(src, FILES)
    rawcache.save("ncbigene", str(src), str(cache), {"genes_md5": "aaa", "taxon": "9606"})
    msg = _raises(CacheError, rawcache.restore, "ncbigene", str(dest), str(cache), {"genes_md5": "bbb"})
    assert "genes_md5" in msg and "aaa" in msg and "bbb" in msg
    assert not os.path.exists(dest), "a refused restore must not touch the destination"


def test_restore_ignores_recorded_keys_the_caller_did_not_ask_about(tmp_path):
    src, cache, dest = tmp_path / "src", tmp_path / "cache", tmp_path / "dest"
    _make(src, FILES)
    rawcache.save("ncbigene", str(src), str(cache), {"genes_md5": "aaa", "taxon": "9606"})
    rawcache.restore("ncbigene", str(dest), str(cache), {"taxon": "9606"})


def test_restore_refuses_a_key_the_cache_never_recorded(tmp_path):
    src, cache = tmp_path / "src", tmp_path / "cache"
    _make(src, FILES)
    rawcache.save("go", str(src), str(cache), {})
    _raises(CacheError, rawcache.restore, "go", str(tmp_path / "d"), str(cache), {"genes_md5": "aaa"})


# ------------------------------------------------------------------ a cache is not trusted blindly

def test_restore_refuses_a_corrupted_cache_file_and_names_it(tmp_path):
    src, cache, dest = tmp_path / "src", tmp_path / "cache", tmp_path / "dest"
    _make(src, FILES)
    rawcache.save("go", str(src), str(cache))
    victim = cache / "go" / "data" / "main.tsv"
    victim.write_bytes(b"\x00" * len(FILES["main.tsv"]))      # same size, the virtiofs failure shape
    msg = _raises(CacheError, rawcache.restore, "go", str(dest), str(cache))
    assert "main.tsv" in msg and "corrupt" in msg
    assert not os.path.exists(dest)


def test_restore_refuses_a_cache_with_a_missing_or_extra_file(tmp_path):
    src, cache = tmp_path / "src", tmp_path / "cache"
    _make(src, FILES)
    rawcache.save("go", str(src), str(cache))
    os.unlink(cache / "go" / "data" / "main_synonym.tsv")
    _raises(CacheError, rawcache.restore, "go", str(tmp_path / "d"), str(cache))
    rawcache.save("go", str(src), str(cache))
    (cache / "go" / "data" / "surprise.tsv").write_bytes(b"x")
    _raises(CacheError, rawcache.restore, "go", str(tmp_path / "d"), str(cache))


def test_restore_with_no_cache_is_an_error_not_an_empty_success(tmp_path):
    msg = _raises(CacheError, rawcache.restore, "go", str(tmp_path / "d"), str(tmp_path / "cache"))
    assert "no cached data for go" in msg


def test_incomplete_entry_without_manifest_is_refused(tmp_path):
    cache = tmp_path / "cache"
    _make(cache / "go" / "data", {"main.tsv": b"x"})           # a save that died before MD5SUMS
    _raises(CacheError, rawcache.restore, "go", str(tmp_path / "d"), str(cache))


def test_saving_an_empty_directory_is_refused(tmp_path):
    (tmp_path / "src").mkdir()
    msg = _raises(CacheError, rawcache.save, "go", str(tmp_path / "src"), str(tmp_path / "cache"))
    assert "no files" in msg
    assert not os.path.exists(tmp_path / "cache" / "go")


def test_group_names_cannot_escape_the_cache_dir(tmp_path):
    for bad in ("../x", "a/b", "", ".hidden", "a b"):
        _raises(CacheError, rawcache.save, bad, str(tmp_path), str(tmp_path / "cache"))


# ------------------------------------------------------------------ the virtiofs failure

def _zeroing_stream(fail_times):
    """A copier that, like plain `cp` on the virtiofs mount, sometimes writes NULs of the right length."""
    real, calls = rawcache._stream, {"n": 0}

    def stream(src, dst):
        md5 = real(src, dst)
        calls["n"] += 1
        if calls["n"] <= fail_times:
            with open(dst, "wb") as fh:
                fh.write(b"\x00" * os.path.getsize(src))
        return md5
    return stream, calls


def test_a_copy_that_silently_zeroes_is_detected_and_retried(tmp_path):
    src, cache, dest = tmp_path / "src", tmp_path / "cache", tmp_path / "dest"
    _make(src, {"a.tsv": b"real data\n" * 100})
    real = rawcache._stream
    rawcache._stream, calls = _zeroing_stream(fail_times=1)
    try:
        rawcache.save("go", str(src), str(cache))
    finally:
        rawcache._stream = real
    assert calls["n"] == 2, "first attempt was bad, second good"
    rawcache.restore("go", str(dest), str(cache))
    assert _read_tree(str(dest)) == {"a.tsv": b"real data\n" * 100}


def test_a_copy_that_always_zeroes_fails_loudly_and_leaves_nothing_behind(tmp_path):
    src, cache = tmp_path / "src", tmp_path / "cache"
    _make(src, {"a.tsv": b"real data\n" * 100})
    real = rawcache._stream
    rawcache._stream, _ = _zeroing_stream(fail_times=99)
    try:
        msg = _raises(CacheError, rawcache.save, "go", str(src), str(cache))
    finally:
        rawcache._stream = real
    assert "different" in msg
    assert not os.path.exists(cache / "go"), "a save that never verified must not look like a cache"
    assert not [p for p in os.listdir(cache) if p.startswith(".")], "temp directory left behind"


def test_failed_resave_keeps_the_previous_good_cache(tmp_path):
    src, cache, dest = tmp_path / "src", tmp_path / "cache", tmp_path / "dest"
    _make(src, {"a.tsv": b"good\n"})
    rawcache.save("go", str(src), str(cache))
    real = rawcache._stream
    rawcache._stream, _ = _zeroing_stream(fail_times=99)
    try:
        _raises(CacheError, rawcache.save, "go", str(src), str(cache))
    finally:
        rawcache._stream = real
    rawcache.restore("go", str(dest), str(cache))
    assert _read_tree(str(dest)) == {"a.tsv": b"good\n"}


# ------------------------------------------------------------------ command line

def _cli(argv, cwd):
    old = os.getcwd()
    os.chdir(cwd)
    try:
        from rdfc2im import cli
        try:
            return cli.main(argv)
        except SystemExit as e:
            return e.code
    finally:
        os.chdir(old)


def test_cli_save_restore_and_failure_exit_codes(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    _make(src, FILES)
    cd = str(tmp_path / "c")
    assert _cli(["cache", "save", "go", str(src), "--cache-dir", cd, "--meta", "taxon=9606"], tmp_path) == 0
    assert _cli(["cache", "restore", "go", str(dest), "--cache-dir", cd, "--meta", "taxon=9606"], tmp_path) == 0
    assert _read_tree(str(dest)) == FILES
    assert _cli(["cache", "restore", "go", str(tmp_path / "d2"), "--cache-dir", cd, "--meta", "taxon=10090"], tmp_path) != 0
    assert _cli(["cache", "restore", "nope", str(tmp_path / "d3"), "--cache-dir", cd], tmp_path) != 0
    assert _cli(["cache", "verify", "go", "--cache-dir", cd], tmp_path) == 0
    assert _cli(["cache", "verify", "--cache-dir", cd], tmp_path) == 0
    (tmp_path / "c" / "go" / "data" / "main.tsv").write_bytes(b"corrupt")
    assert _cli(["cache", "verify", "--cache-dir", cd], tmp_path) != 0
