"""cached_raw_data/: a checksummed local cache of what a build fetched from the network.

The slow, remote, occasionally flaky part of tools/full-build.sh is the SPARQL extraction
(~15 minutes against RDF Portal) plus a few plain downloads. Everything after it is local and
deterministic, so the fetched bytes are worth keeping: a later build can restore them instead of
asking the network again (`tools/full-build.sh --use-cached-data`).

Layout - one directory per *group* (a source name, or a named download):

    cached_raw_data/<group>/data/<relative paths...>   the cached files
    cached_raw_data/<group>/MD5SUMS                    `md5sum -c` format, paths relative to <group>/
    cached_raw_data/<group>/META.json                  what the data was fetched *for*, plus when

Two things here exist because of failures this project has actually had, not in the abstract:

* **Copies are verified, not trusted.** Plain `cp` has silently turned a 4097-byte file into 4097
  bytes of NUL on this project's virtiofs workspace mount (see the Makefile's `docs` target). Every
  copy in or out of the cache therefore hashes what it read, writes to a temp name, re-reads the
  written file and compares, and only then renames into place. A mismatch retries, then fails
  loudly; it never leaves a wrong file under the real name.
* **A cache must know what it is a cache of.** Data fetched for a 113-gene panel is not valid for a
  different panel, and restoring it silently would build the wrong mine with no error anywhere -
  the same "step that reports success while doing nothing useful" this project keeps hitting.
  Callers record META (scope, gene-panel checksum, ...) at save time and pass what they *need* at
  restore time; any recorded key that disagrees refuses the restore.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import time

DEFAULT_CACHE_DIR = "cached_raw_data"
GROUP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CHUNK = 1 << 20
COPY_ATTEMPTS = 3


class CacheError(Exception):
    """Anything that means the cache cannot be trusted or does not apply. Never swallowed."""


def md5_file(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _stream(src: str, dst: str) -> str:
    """Copy src to dst in chunks, fsync, and return the md5 of what was READ from src.

    Kept as its own function so a test can swap in a copier that corrupts its output the way the
    virtiofs mount did - the verification in _copy_verified is the thing under test, not this.
    """
    h = hashlib.md5()
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        for chunk in iter(lambda: fi.read(CHUNK), b""):
            h.update(chunk)
            fo.write(chunk)
        fo.flush()
        os.fsync(fo.fileno())
        try:  # best effort: make the read-back below come from the file, not the page cache
            os.posix_fadvise(fo.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        except (AttributeError, OSError):
            pass
    return h.hexdigest()


def _copy_verified(src: str, dst: str, expect_md5: str | None = None) -> str:
    """Copy src -> dst so that dst is provably identical, or raise. Returns the md5.

    expect_md5, when given, is what src is *supposed* to hash to (the manifest's value): a source
    that already disagrees is corrupt, and retrying the copy cannot fix that, so it is not retried.
    """
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    tmp = dst + ".part"
    last = ""
    for _ in range(COPY_ATTEMPTS):
        read_md5 = _stream(src, tmp)
        if expect_md5 is not None and read_md5 != expect_md5:
            os.unlink(tmp)
            raise CacheError(f"{src} is corrupt: md5 {read_md5}, expected {expect_md5}")
        written_md5 = md5_file(tmp)
        if written_md5 == read_md5 and os.path.getsize(tmp) == os.path.getsize(src):
            os.replace(tmp, dst)
            return read_md5
        last = f"read {read_md5}, wrote {written_md5}"
    if os.path.exists(tmp):
        os.unlink(tmp)
    raise CacheError(f"copy {src} -> {dst} kept coming out different after {COPY_ATTEMPTS} attempts ({last})")


def copy_tree(src: str, dst: str, exclude: tuple = ()) -> int:
    """Make dst an identical copy of src (minus any entry named in `exclude`), file by verified file.

    For mounts where `cp` and `tar` both misbehave (see tools/make-inputs.sh): only chunked
    read/write is used, every file is re-read and compared (_copy_verified), and nothing depends
    on inode numbers. Symlinks are recreated, empty directories kept, permission bits preserved
    (a clone's gradlew must stay executable). dst is replaced, not merged into, so a file upstream
    deleted does not survive a refresh. Returns the number of regular files copied.
    """
    if not os.path.isdir(src):
        raise CacheError(f"copy_tree: {src} is not a directory")
    if os.path.lexists(dst):
        shutil.rmtree(dst) if os.path.isdir(dst) and not os.path.islink(dst) else os.unlink(dst)
    os.makedirs(dst)
    n = 0
    for base, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in exclude)
        rel = os.path.relpath(base, src)
        out = dst if rel == "." else os.path.join(dst, rel)
        for d in list(dirs):
            sp = os.path.join(base, d)
            if os.path.islink(sp):          # os.walk lists a symlink to a directory among dirs
                os.symlink(os.readlink(sp), os.path.join(out, d))
                dirs.remove(d)
            else:
                os.makedirs(os.path.join(out, d), exist_ok=True)
        for f in sorted(files):
            if f in exclude:
                continue
            sp, dp = os.path.join(base, f), os.path.join(out, f)
            if os.path.islink(sp):
                os.symlink(os.readlink(sp), dp)
                continue
            _copy_verified(sp, dp)
            os.chmod(dp, stat.S_IMODE(os.stat(sp).st_mode))
            n += 1
    return n


def _check_group(group: str) -> None:
    if not GROUP_RE.match(group or ""):
        raise CacheError(f"bad cache group name {group!r} (letters, digits, . _ - only)")


def _walk(root: str) -> list[str]:
    out = []
    for d, _, files in os.walk(root):
        for f in files:
            if f.endswith(".part"):
                continue
            out.append(os.path.relpath(os.path.join(d, f), root))
    return sorted(out)


def _read_manifest(gdir: str) -> dict[str, str]:
    path = os.path.join(gdir, "MD5SUMS")
    if not os.path.exists(path):
        raise CacheError(f"{gdir} has no MD5SUMS - not a complete cache entry")
    man = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if not line:
                continue
            md5, _, rel = line.partition("  ")
            if len(md5) != 32 or not rel.startswith("data/"):
                raise CacheError(f"{path}: unparseable line {line!r}")
            man[rel[len("data/"):]] = md5
    return man


def _read_meta(gdir: str) -> dict:
    path = os.path.join(gdir, "META.json")
    if not os.path.exists(path):
        raise CacheError(f"{gdir} has no META.json - not a complete cache entry")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save(group: str, src_dir: str, cache_dir: str = DEFAULT_CACHE_DIR, meta: dict | None = None) -> dict:
    """Cache every file under src_dir as `group`, replacing any earlier entry atomically."""
    _check_group(group)
    if not os.path.isdir(src_dir):
        raise CacheError(f"nothing to cache for {group}: {src_dir} is not a directory")
    rels = _walk(src_dir)
    if not rels:
        raise CacheError(f"nothing to cache for {group}: {src_dir} has no files - "
                         f"refusing to record an empty fetch as a cache")
    os.makedirs(cache_dir, exist_ok=True)
    gdir = os.path.join(cache_dir, group)
    new, old = os.path.join(cache_dir, f".{group}.new"), os.path.join(cache_dir, f".{group}.old")
    for d in (new, old):
        shutil.rmtree(d, ignore_errors=True)
    total = 0
    sums = {}
    try:
        for rel in rels:
            sums[rel] = _copy_verified(os.path.join(src_dir, rel), os.path.join(new, "data", rel))
            total += os.path.getsize(os.path.join(new, "data", rel))
        with open(os.path.join(new, "MD5SUMS"), "w", encoding="utf-8") as fh:
            for rel in rels:
                fh.write(f"{sums[rel]}  data/{rel}\n")
        info = {"meta": dict(meta or {}), "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "files": len(rels), "bytes": total}
        with open(os.path.join(new, "META.json"), "w", encoding="utf-8") as fh:
            json.dump(info, fh, indent=2, sort_keys=True)
            fh.write("\n")
        if os.path.exists(gdir):
            os.rename(gdir, old)
        os.rename(new, gdir)
    except BaseException:
        shutil.rmtree(new, ignore_errors=True)
        if os.path.exists(old) and not os.path.exists(gdir):
            os.rename(old, gdir)          # put the previous good entry back
        raise
    shutil.rmtree(old, ignore_errors=True)
    return info


def verify(group: str, cache_dir: str = DEFAULT_CACHE_DIR) -> dict:
    """Re-hash every cached file against the manifest. Raises CacheError on any difference."""
    _check_group(group)
    gdir = os.path.join(cache_dir, group)
    if not os.path.isdir(gdir):
        raise CacheError(f"no cached data for {group} in {cache_dir}/")
    man, info = _read_manifest(gdir), _read_meta(gdir)
    on_disk = set(_walk(os.path.join(gdir, "data"))) if os.path.isdir(os.path.join(gdir, "data")) else set()
    if on_disk != set(man):
        raise CacheError(f"{group}: files differ from MD5SUMS - missing {sorted(set(man) - on_disk)}, "
                         f"unlisted {sorted(on_disk - set(man))}")
    for rel, want in sorted(man.items()):
        got = md5_file(os.path.join(gdir, "data", rel))
        if got != want:
            raise CacheError(f"{group}: {rel} is corrupt in the cache (md5 {got}, expected {want})")
    return info


def restore(group: str, dest_dir: str, cache_dir: str = DEFAULT_CACHE_DIR, meta: dict | None = None) -> dict:
    """Make dest_dir hold exactly the cached files for `group`, byte for byte.

    `meta` is what the caller needs the cached data to have been fetched for; every key must match
    what was recorded at save time. dest_dir's other files are removed: a stale file left beside
    restored ones would be picked up by the next stage exactly as if it had been fetched.
    """
    info = verify(group, cache_dir)                 # includes: exists, complete, uncorrupted
    have = info.get("meta", {})
    for k, want in (meta or {}).items():
        if have.get(k) != want:
            raise CacheError(f"{group}: cached data was fetched for {k}={have.get(k)!r}, "
                             f"but this build needs {k}={want!r} - refusing to restore it")
    gdir = os.path.join(cache_dir, group)
    man = _read_manifest(gdir)
    os.makedirs(dest_dir, exist_ok=True)
    for rel, want in sorted(man.items()):
        _copy_verified(os.path.join(gdir, "data", rel), os.path.join(dest_dir, rel), expect_md5=want)
    for rel in _walk(dest_dir):
        if rel not in man:
            os.unlink(os.path.join(dest_dir, rel))
    for d, _, _ in sorted(os.walk(dest_dir, topdown=False)):
        if d != dest_dir and not os.listdir(d):
            os.rmdir(d)
    return info


def groups(cache_dir: str = DEFAULT_CACHE_DIR) -> list[str]:
    if not os.path.isdir(cache_dir):
        return []
    return sorted(g for g in os.listdir(cache_dir)
                  if not g.startswith(".") and os.path.isdir(os.path.join(cache_dir, g)))
