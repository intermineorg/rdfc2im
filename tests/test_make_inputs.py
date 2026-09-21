"""tools/make-inputs.sh: assembling in/ from the cached upstream clones.

The first fresh run of tools/full-build.sh died here: the script copied each clone with a plain
`cp -r`, .git and all, and then deleted the copied .git. On this project's virtiofs workspace mount
that `cp` failed on the clone's read-only pack file ("failed to extend ... pack: Permission
denied", leaving a 0-byte pack) and the build stopped at phase 2 - for a directory that was about
to be thrown away. In/ never needs any .git, so the copy must never touch one. An unreadable file
inside .git reproduces the failure on any filesystem: `cp -r` cannot read it.
"""
import hashlib
import os
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES = {
    "rdf-config/config/go/model.yaml": b"go: model\n",
    "rdf-config/config/hgnc/model.yaml": b"hgnc: model\n",
    "intermine/bio/sources/reactome/reactome.properties": b"x=1\n",
    "intermine/bio/core/build.gradle": b"apply plugin: 'java'\n",
    "humanmine/project.xml": b"<project/>\n",
    "humanmine/dbmodel/resources/genomic_priorities.properties": b"a = b\n",
    "humanmine-bio-sources/humanmine-go/go.xml": b"<items/>\n" * 100,
    "humanmine-bio-sources/settings.gradle": "include 'x'\n".encode(),
}


def _fake_upstream(cache):
    for rel, data in FILES.items():
        p = os.path.join(cache, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(data)
    for repo in ("rdf-config", "intermine", "humanmine", "humanmine-bio-sources"):
        subprocess.run(["git", "init", "-q", os.path.join(cache, repo)], check=True)
    # what a real clone's .git has that a plain copy trips over: something it cannot read
    hostile = os.path.join(cache, "humanmine-bio-sources", ".git", "objects", "pack")
    os.makedirs(hostile, exist_ok=True)
    p = os.path.join(hostile, "pack-x.pack")
    with open(p, "wb") as fh:
        fh.write(b"PACK")
    os.chmod(p, 0)


def _run(tmp_path):
    cache, work = str(tmp_path / "upstream"), tmp_path / "work"
    work.mkdir()
    _fake_upstream(cache)
    env = dict(os.environ, CACHE=cache, MINE="http://127.0.0.1:9")   # no live model: a tolerated warning
    return subprocess.run(["sh", os.path.join(ROOT, "tools", "make-inputs.sh")], cwd=work, env=env,
                          capture_output=True, text=True), work


def _md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


def test_assembling_in_never_reads_the_clones_git_directories(tmp_path):
    if os.geteuid() == 0 or not shutil.which("git"):
        return   # root can read a mode-0 file, so the reproduction does not apply
    r, work = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not any(".git" in d for d, _, _ in os.walk(work / "in")), "in/ must hold no .git"


def test_in_holds_byte_identical_copies_of_the_upstream_trees(tmp_path):
    if os.geteuid() == 0 or not shutil.which("git"):
        return
    r, work = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    want = {
        "in/config/go/model.yaml": "rdf-config/config/go/model.yaml",
        "in/config/hgnc/model.yaml": "rdf-config/config/hgnc/model.yaml",
        "in/intermine/bio/sources/reactome/reactome.properties": "intermine/bio/sources/reactome/reactome.properties",
        "in/intermine/bio/core/build.gradle": "intermine/bio/core/build.gradle",
        "in/humanmine-bio-sources/humanmine-go/go.xml": "humanmine-bio-sources/humanmine-go/go.xml",
        "in/humanmine-bio-sources/settings.gradle": "humanmine-bio-sources/settings.gradle",
        "in/humanmine_project.xml": "humanmine/project.xml",
        "in/humanmine_priorities.properties": "humanmine/dbmodel/resources/genomic_priorities.properties",
    }
    for dst, src in want.items():
        assert _md5(work / dst) == hashlib.md5(FILES[src]).hexdigest(), dst


def test_a_rerun_replaces_the_previous_copy_rather_than_merging_into_it(tmp_path):
    if os.geteuid() == 0 or not shutil.which("git"):
        return
    r, work = _run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    stale = work / "in" / "config" / "removed_upstream" / "model.yaml"
    stale.parent.mkdir()
    stale.write_text("stale")
    r2 = subprocess.run(["sh", os.path.join(ROOT, "tools", "make-inputs.sh")], cwd=work, capture_output=True,
                        text=True, env=dict(os.environ, CACHE=str(tmp_path / "upstream"), MINE="http://127.0.0.1:9"))
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert not stale.exists()
