"""rawcache.copy_tree / tools/copy_tree.py: copying a directory tree on a mount where cp and tar fail.

tools/make-inputs.sh's second fresh-run failure. After `cp -r` broke on a clone's .git, its
replacement `tar --exclude=.git -cf - . | tar -xf -` also failed on the same virtiofs workspace
mount: "tar: .: Directory renamed before its status could be extracted" (tar detects a rename by
comparing inode numbers, which that mount does not keep stable), and the pipeline's exit status
killed the build. Both tools lean on filesystem behaviour that mount does not honour; a plain
read/write loop with a re-read comparison (the same verified copier the raw-data cache uses) does
not. That mount cannot be reproduced offline, so these tests pin what a correct copy must do -
including every property a clone's tree has that a naive copy loses - and
test_make_inputs.py pins that the script goes through this rather than back to cp or tar.
"""
import os
import stat
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rdfc2im import rawcache  # noqa: E402
from rdfc2im.rawcache import CacheError  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tree(d, skip=()):
    """Everything under d as {relpath: ("file", bytes, mode) | ("link", target) | ("dir",)}."""
    out = {}
    for base, dirs, files in os.walk(d):
        dirs[:] = [x for x in dirs if x not in skip]
        for n in [x for x in dirs + files if x not in skip]:
            p = os.path.join(base, n)
            rel = os.path.relpath(p, d)
            if os.path.islink(p):
                out[rel] = ("link", os.readlink(p))
            elif os.path.isdir(p):
                out[rel] = ("dir",)
            else:
                out[rel] = ("file", open(p, "rb").read(), stat.S_IMODE(os.stat(p).st_mode))
    return out


def _fixture(src):
    os.makedirs(src / "sub" / "deep")
    os.makedirs(src / "empty_dir")
    (src / "a.txt").write_bytes(b"alpha\n" * 500)
    (src / "sub" / "b.bin").write_bytes(bytes(range(256)) * 20)
    (src / "sub" / "deep" / "c.txt").write_bytes(b"")
    (src / "gradlew").write_bytes(b"#!/bin/sh\n")
    os.chmod(src / "gradlew", 0o755)
    os.symlink("a.txt", src / "link_to_a")
    os.makedirs(src / ".git" / "objects")
    (src / ".git" / "objects" / "hostile").write_bytes(b"PACK")
    os.chmod(src / ".git" / "objects" / "hostile", 0)
    (src / "sub" / ".git").write_bytes(b"gitdir: ../x\n")     # a submodule-style .git FILE


def test_copy_tree_reproduces_the_tree_minus_git(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _fixture(src)
    n = rawcache.copy_tree(str(src), str(dst), exclude=(".git",))
    assert _tree(str(dst)) == _tree(str(src), skip=(".git",))
    assert n == 4                                               # a.txt, b.bin, c.txt, gradlew
    assert stat.S_IMODE(os.stat(dst / "gradlew").st_mode) == 0o755, "gradlew must stay executable"
    assert os.readlink(dst / "link_to_a") == "a.txt"
    assert os.path.isdir(dst / "empty_dir")
    assert not os.path.exists(dst / ".git") and not os.path.exists(dst / "sub" / ".git")


def test_copy_tree_makes_dst_identical_removing_what_src_lacks(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    (src).mkdir()
    (src / "keep.txt").write_bytes(b"new")
    os.makedirs(dst / "old_dir")
    (dst / "old_dir" / "gone.txt").write_bytes(b"stale")
    (dst / "keep.txt").write_bytes(b"OLD")
    rawcache.copy_tree(str(src), str(dst))
    assert _tree(str(dst)) == {"keep.txt": ("file", b"new", stat.S_IMODE(os.stat(src / "keep.txt").st_mode))}


def test_copy_tree_detects_a_silently_zeroed_file(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "a.txt").write_bytes(b"real data\n" * 100)
    real = rawcache._stream

    def zeroing(s, d):
        md5 = real(s, d)
        with open(d, "wb") as fh:
            fh.write(b"\x00" * os.path.getsize(s))
        return md5
    rawcache._stream = zeroing
    try:
        try:
            rawcache.copy_tree(str(src), str(dst))
        except CacheError:
            pass
        else:
            raise AssertionError("a zeroed copy was accepted")
    finally:
        rawcache._stream = real
    assert not os.path.exists(dst / "a.txt")


def test_copy_tree_refuses_a_missing_source(tmp_path):
    try:
        rawcache.copy_tree(str(tmp_path / "nope"), str(tmp_path / "dst"))
    except CacheError:
        return
    raise AssertionError("expected CacheError")


def test_copy_tree_command_line_tool(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    _fixture(src)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "copy_tree.py"), str(src), str(dst),
                        "--exclude", ".git"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert os.path.exists(dst / "a.txt") and not os.path.exists(dst / ".git")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "copy_tree.py"), str(tmp_path / "nope"),
                        str(tmp_path / "d2")], capture_output=True, text=True)
    assert r.returncode != 0 and "copy_tree" in r.stderr
