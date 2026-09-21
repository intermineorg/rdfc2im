#!/usr/bin/env python3
"""copy_tree.py SRC DST [--exclude NAME ...]: make DST an identical, verified copy of SRC.

Used by tools/make-inputs.sh in place of `cp -r` and `tar`, both of which failed on the virtiofs
workspace mount (see rdfc2im/rawcache.py copy_tree). Needs only the standard library - it runs
before the project's venv is guaranteed to exist.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rdfc2im.rawcache import CacheError, copy_tree  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument("--exclude", action="append", default=[], help="skip entries with this name (repeatable)")
    a = ap.parse_args()
    try:
        n = copy_tree(a.src, a.dst, tuple(a.exclude))
    except CacheError as e:
        print(f"copy_tree: {e}", file=sys.stderr)
        return 1
    print(f"  copied {n} files, verified: {a.src} -> {a.dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
