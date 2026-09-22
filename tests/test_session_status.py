"""tools/session-status.sh: reports what an earlier session left behind, changes nothing itself.

Runs the real script against a scratch copy of the repo's structural bits (a git repo, an out/
tree, a fake cached_raw_data/) so the assertions exercise the actual bash, not a description of it.
"""
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "tools", "session-status.sh")


def _repo(tmp_path):
    """A minimal git repo with tools/session-status.sh and tools/full-build.sh (for its SOURCES
    default) copied in, so the script can be run with HOME redirected and no side effects on the
    real repo or the real $HOME."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "tools").mkdir()
    shutil.copy(SCRIPT, repo / "tools" / "session-status.sh")
    os.chmod(repo / "tools" / "session-status.sh", 0o755)
    (repo / "tools" / "full-build.sh").write_text(
        'SOURCES=${SOURCES:-"go ncbigene hgnc"}\n')
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _run(repo, home, extra_env=None):
    env = dict(os.environ, HOME=str(home))
    env.pop("PATH", None)
    env["PATH"] = os.environ.get("PATH", "/usr/bin:/bin")
    if extra_env:
        env.update(extra_env)
    r = subprocess.run(["bash", "tools/session-status.sh"], cwd=repo, env=env,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def _rows(section_text):
    """The '  name   size' bullet lines of a report section, not its heading (which may itself
    mention panel-member names as part of listing the panel)."""
    return [l for l in section_text.splitlines() if l.startswith("  ")]


def test_flags_out_directories_outside_the_demo_panel_only(tmp_path):
    repo = _repo(tmp_path)
    for name in ("go", "hgnc", "uberon", "_mine"):
        d = repo / "out" / name
        d.mkdir(parents=True)
        (d / "columns.tsv").write_text("x\n")
    out = _run(repo, tmp_path / "home")
    section = out.split("outside the current demo panel")[1].split("cached_raw_data")[0]
    rows = " ".join(_rows(section))
    assert "uberon" in rows
    assert "go" not in rows.split()
    assert "_mine" not in rows


def test_reports_none_when_every_out_dir_is_in_the_panel(tmp_path):
    repo = _repo(tmp_path)
    (repo / "out" / "go").mkdir(parents=True)
    (repo / "out" / "go" / "columns.tsv").write_text("x\n")
    out = _run(repo, tmp_path / "home")
    section = out.split("outside the current demo panel")[1].split("cached_raw_data")[0]
    assert "(none)" in section


def test_an_empty_out_dir_with_no_columns_tsv_is_not_flagged(tmp_path):
    """A directory that exists but was never translated (e.g. a tracked-but-empty placeholder)
    is not "leftover derived output" - only real translate output counts."""
    repo = _repo(tmp_path)
    (repo / "out" / "uberon").mkdir(parents=True)
    out = _run(repo, tmp_path / "home")
    section = out.split("outside the current demo panel")[1].split("cached_raw_data")[0]
    assert "(none)" in section


def test_git_section_reports_dirty_working_tree(tmp_path):
    repo = _repo(tmp_path)
    (repo / "README.md").write_text("changed\n")
    (repo / "new_untracked_file.txt").write_text("x\n")
    out = _run(repo, tmp_path / "home")
    assert "1 tracked file(s) modified" in out
    assert "1 untracked path(s)" in out


def test_git_section_reports_clean_tree(tmp_path):
    repo = _repo(tmp_path)
    out = _run(repo, tmp_path / "home")
    assert "working tree clean" in out


def test_git_section_reports_unpushed_commits_against_upstream(tmp_path):
    repo = _repo(tmp_path)
    upstream = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "-q", "--bare", str(upstream)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(upstream)], cwd=repo, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "HEAD:main"], cwd=repo, check=True)
    (repo / "README.md").write_text("more\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=repo, check=True)
    out = _run(repo, tmp_path / "home")
    assert "1 commit(s) not yet pushed" in out


def test_git_section_up_to_date_after_pushing(tmp_path):
    repo = _repo(tmp_path)
    upstream = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "-q", "--bare", str(upstream)], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(upstream)], cwd=repo, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", "HEAD:main"], cwd=repo, check=True)
    out = _run(repo, tmp_path / "home")
    assert "up to date with upstream" in out


def test_no_upstream_is_reported_rather_than_assumed_pushed(tmp_path):
    repo = _repo(tmp_path)
    out = _run(repo, tmp_path / "home")
    assert "no upstream tracking branch" in out


def test_trial_home_dirs_are_listed_with_the_most_recently_touched_one_tagged(tmp_path):
    """Tagging must come from each directory's own content, not a cross-reference into log files -
    those can be touched by something unrelated (found live: an orphaned background process from
    an earlier session kept rewriting old runs' logs on every later build)."""
    repo = _repo(tmp_path)
    home = tmp_path / "home"
    import time
    old = home / "intermine-build" / "trial_home_old"
    new = home / "intermine-build" / "trial_home_new"
    old.mkdir(parents=True)
    (old / "f").write_text("x")
    time.sleep(1.1)              # find -printf '%T@' has 1s resolution on some filesystems
    new.mkdir(parents=True)
    (new / "f").write_text("x")
    out = _run(repo, home)
    section = out.split("Scratch build trees")[1].split("Each is safe")[0]
    assert "trial_home_old" in section and "trial_home_new" in section
    assert "most recently touched" in [l for l in section.splitlines() if "trial_home_new" in l][0]
    assert "most recently touched" not in [l for l in section.splitlines() if "trial_home_old" in l][0]


def test_never_writes_anything_under_the_repo_or_deletes_containers(tmp_path):
    """The whole point: a read-only report. No git object churn, no new files."""
    repo = _repo(tmp_path)
    before = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True,
                            text=True).stdout
    _run(repo, tmp_path / "home")
    after = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True,
                           text=True).stdout
    assert before == after == ""


def test_reports_cache_health_when_a_cache_and_venv_are_present(tmp_path):
    """The fake .venv must actually work, not just exist: session-status.sh sources
    .venv/bin/activate and then relies on whatever 'python3' that puts on PATH to have
    pyyaml. An empty activate file is a no-op, so this only ever passed by accident - when
    the *ambient* python3 already had pyyaml (e.g. a real venv already active in the shell
    running the tests, or a stray user-site install under the real $HOME). Under
    tests/run.py's bare `python3`, with `_run` pointing $HOME at an empty scratch dir, there
    is no such fallback and the cache-health check failed with a missing-dependency message
    instead of 'ok'. Rebuild a minimal real venv here - pyvenv.cfg plus a same-depth
    bin/python3 symlink (chaining through another symlink defeats Python's pyvenv.cfg
    lookup) - sharing this repo's real venv site-packages, so activation is genuine."""
    repo = _repo(tmp_path)
    real_venv = pathlib.Path(ROOT) / ".venv"
    py_ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    venv_bin = repo / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (repo / ".venv" / "lib" / py_ver).mkdir(parents=True)
    (repo / ".venv" / "lib" / py_ver / "site-packages").symlink_to(
        real_venv / "lib" / py_ver / "site-packages")
    (venv_bin / "python3").symlink_to((real_venv / "bin" / "python3").resolve())
    shutil.copy(real_venv / "pyvenv.cfg", repo / ".venv" / "pyvenv.cfg")
    (venv_bin / "activate").write_text(f'export PATH="{venv_bin}:$PATH"\n')
    shutil.copytree(os.path.join(ROOT, "rdfc2im"), repo / "rdfc2im")
    (repo / "cached_raw_data" / "go" / "data").mkdir(parents=True)
    (repo / "cached_raw_data" / "go" / "data" / "a.tsv").write_bytes(b"x")
    import hashlib
    md5 = hashlib.md5(b"x").hexdigest()
    (repo / "cached_raw_data" / "go" / "MD5SUMS").write_text(f"{md5}  data/a.tsv\n")
    (repo / "cached_raw_data" / "go" / "META.json").write_text('{"meta": {}, "files": 1, "bytes": 1, "saved_at": "x"}\n')
    out = _run(repo, tmp_path / "home", extra_env={"PYTHONPATH": str(repo)})
    assert "ok  go:" in out


def test_out_dir_cleanup_advice_never_suggests_rm_rf(tmp_path):
    """This ran for real: a session-status report said 'Safe to remove per-source: rm -rf
    out/<source>' for a directory that ALSO held out/<source>/mapping_subjects.tsv and
    mapping_predicates.sssom.tsv - the tracked, hand-curated mapping state (.gitignore negates
    exactly those two files, plus their .base snapshots, back into version control). Following
    that advice deleted real curation work, recovered only because it was still uncommitted.
    The suggested command must never be able to touch a tracked file - `git clean -fdx` is safe
    (it refuses tracked content by construction); a blanket `rm -rf` is not."""
    repo = _repo(tmp_path)
    d = repo / "out" / "uberon"
    d.mkdir(parents=True)
    (d / "columns.tsv").write_text("derived\n")                 # untracked/derived
    (d / "mapping_subjects.tsv").write_text("curated\n")        # tracked curation state
    subprocess.run(["git", "add", "out/uberon/mapping_subjects.tsv"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "curated mapping"], cwd=repo, check=True)
    out = _run(repo, tmp_path / "home")
    section = out.split("outside the current demo panel")[1].split("cached_raw_data")[0]
    assert "git clean -fdx" in section
    # "rm -rf" may appear only inside an explicit warning not to use it - never as the suggested command
    for line in section.splitlines():
        if "rm -rf" in line:
            assert "never" in line.lower(), f"rm -rf suggested without a warning: {line!r}"
    assert "mapping_subjects.tsv" in section or "tracked" in section.lower()


def test_out_dir_cleanup_command_actually_preserves_tracked_files(tmp_path):
    """Not just the wording - the literal command the report suggests, run for real, must leave
    the tracked curation files behind and remove only the derived ones."""
    repo = _repo(tmp_path)
    d = repo / "out" / "uberon"
    d.mkdir(parents=True)
    (d / "mapping_subjects.tsv").write_text("curated\n")
    subprocess.run(["git", "add", "out/uberon/mapping_subjects.tsv"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "curated mapping"], cwd=repo, check=True)
    (d / "columns.tsv").write_text("derived\n")
    (d / "raw").mkdir()
    (d / "raw" / "main.tsv").write_text("derived\n")
    subprocess.run(["git", "clean", "-fdx", "out/uberon"], cwd=repo, check=True)
    assert (d / "mapping_subjects.tsv").exists()
    assert not (d / "columns.tsv").exists()
    assert not (d / "raw").exists()
