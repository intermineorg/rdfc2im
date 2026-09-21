"""tools/full-build.sh --use-cached-data / --cache-dir, checked through --dry-run.

--dry-run is a real dry run of the exact command sequence (run/run_in/write_file are the only
things that touch the outside world, and all are no-op printers under it), so these tests read
the plan the script would execute. They skip on a machine without the tools check_prereqs needs
(docker compose, a JDK 8): that phase always runs first, even under --only.
"""
import hashlib
import os
import re
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PANEL = os.path.join(ROOT, "curation", "demo_gene_panel.txt")


def _plan(tmp_path, *args):
    """Returns the dry-run log, or None if this machine can't pass check_prereqs."""
    if not shutil.which("bash"):
        return None
    env = dict(os.environ, TRIAL_HOME=str(tmp_path / "trial_home"), RUN_ID="test",
               LOG_DIR=str(tmp_path / "logs"))
    r = subprocess.run(["bash", "tools/full-build.sh", "--dry-run", *args], cwd=ROOT, env=env,
                       capture_output=True, text=True)
    out = r.stdout + r.stderr
    if r.returncode != 0 and "[check_prereqs] FATAL" in out:
        return None
    assert r.returncode == 0, out
    return out


def _cmds(plan):
    """The `+ ...` command lines the script would run, without timestamp/phase decoration."""
    return [m.group(1) for line in plan.splitlines() if (m := re.search(r"\] \+ (.*)$", line))]


def _meta(cmd):
    return sorted(re.findall(r"--meta (\S+)", cmd))


def test_default_build_fetches_then_saves_to_the_cache(tmp_path):
    plan = _plan(tmp_path, "--only", "rdfc2im_pipeline", "--sources", "go hgnc")
    if plan is None:
        return
    cmds = _cmds(plan)
    for src in ("go", "hgnc"):
        fetch = next(i for i, c in enumerate(cmds) if f"rdfc2im fetch --source {src} " in c)
        save = next(i for i, c in enumerate(cmds) if f"rdfc2im cache save {src} out/{src}/raw" in c)
        tsv = next(i for i, c in enumerate(cmds) if f"rdfc2im tsv --source {src}" in c)
        assert fetch < save < tsv, "cache only what was fetched, and before it is transformed"
    assert not any("cache restore" in c for c in cmds)


def test_use_cached_data_restores_and_never_fetches(tmp_path):
    plan = _plan(tmp_path, "--only", "rdfc2im_pipeline", "--sources", "go hgnc", "--use-cached-data")
    if plan is None:
        return
    cmds = _cmds(plan)
    for src in ("go", "hgnc"):
        restore = next(i for i, c in enumerate(cmds) if f"rdfc2im cache restore {src} out/{src}/raw" in c)
        tsv = next(i for i, c in enumerate(cmds) if f"rdfc2im tsv --source {src}" in c)
        assert restore < tsv
    assert not any(" fetch " in c or "cache save" in c for c in cmds), cmds


def test_saved_and_required_scope_are_identical_for_every_source(tmp_path):
    """A restore that demanded different --meta than save recorded would refuse every time (or,
    worse, check nothing). Both modes must derive them from the same function."""
    srcs = "go ncbigene reactome"
    saved = _plan(tmp_path, "--only", "rdfc2im_pipeline", "--sources", srcs)
    restored = _plan(tmp_path, "--only", "rdfc2im_pipeline", "--sources", srcs, "--use-cached-data")
    if saved is None:
        return
    for src in srcs.split():
        s = next(c for c in _cmds(saved) if f"cache save {src} " in c)
        r = next(c for c in _cmds(restored) if f"cache restore {src} " in c)
        assert _meta(s) == _meta(r) and _meta(s), (s, r)


def test_panel_scoped_sources_are_keyed_to_the_gene_panel_checksum(tmp_path):
    plan = _plan(tmp_path, "--only", "rdfc2im_pipeline", "--sources", "ncbigene go", "--use-cached-data")
    if plan is None:
        return
    md5 = hashlib.md5(open(PANEL, "rb").read()).hexdigest()
    cmds = _cmds(plan)
    assert f"--meta genes_md5={md5}" in next(c for c in cmds if "cache restore ncbigene" in c)
    assert "genes_md5" not in next(c for c in cmds if "cache restore go " in c), "unscoped source"


def test_a_different_panel_gives_a_different_cache_key(tmp_path):
    other = tmp_path / "panel.txt"
    other.write_text("CYP2D6 CYP3A4\n")
    a = _plan(tmp_path, "--only", "rdfc2im_pipeline", "--sources", "ncbigene", "--use-cached-data")
    b = _plan(tmp_path, "--only", "rdfc2im_pipeline", "--sources", "ncbigene", "--use-cached-data",
              "--genes", str(other))
    if a is None:
        return
    ka = _meta(next(c for c in _cmds(a) if "cache restore ncbigene" in c))
    kb = _meta(next(c for c in _cmds(b) if "cache restore ncbigene" in c))
    assert ka != kb


def test_cache_dir_option_is_passed_through(tmp_path):
    plan = _plan(tmp_path, "--only", "rdfc2im_pipeline", "--sources", "go", "--use-cached-data",
                 "--cache-dir", "/somewhere/else")
    if plan is None:
        return
    assert "--cache-dir /somewhere/else" in next(c for c in _cmds(plan) if "cache restore go" in c)


def test_reactome_download_is_cached_and_restorable(tmp_path):
    fetched = _plan(tmp_path, "--only", "build_reactome_source")
    restored = _plan(tmp_path, "--only", "build_reactome_source", "--use-cached-data")
    if fetched is None:
        return
    f, r = _cmds(fetched), _cmds(restored)
    assert any("curl" in c and "UniProt2Reactome.txt" in c for c in f)
    assert any("cache save reactome-uniprot-map /micklem/data/reactome/current" in c for c in f)
    assert not any("curl" in c and "UniProt2Reactome" in c for c in r)
    assert any("cache restore reactome-uniprot-map /micklem/data/reactome/current" in c for c in r)


def test_logs_default_to_the_repository_so_a_host_can_watch_them(tmp_path):
    """A build in a sandbox logged under the sandbox's private $HOME, which a host terminal could not
    see ("no logfile at /home/agent/... for run fresh3"). The repository is the shared directory."""
    if not shutil.which("bash"):
        return
    env = {k: v for k, v in os.environ.items() if k != "LOG_DIR"}
    env.update(TRIAL_HOME=str(tmp_path / "trial_home"), RUN_ID="test-default-logdir")
    logs = os.path.join(ROOT, ".build-logs")
    mine = os.path.join(logs, "build-test-default-logdir")
    try:
        r = subprocess.run(["bash", "tools/full-build.sh", "--dry-run", "--only", "stage_src_data"], cwd=ROOT,
                           env=env, capture_output=True, text=True)
        if r.returncode != 0 and "[check_prereqs] FATAL" in r.stdout + r.stderr:
            return
        assert r.returncode == 0, r.stdout + r.stderr
        assert os.path.exists(mine + ".log") and os.path.exists(mine + ".status")
        assert not os.path.exists(tmp_path / "trial_home" / "logs")
        w = subprocess.run(["bash", "tools/watch-build.sh", "--run-id", "test-default-logdir", "--once"], cwd=ROOT,
                           env=env, capture_output=True, text=True)
        assert w.returncode == 0 and ".build-logs" in w.stdout, w.stdout + w.stderr
    finally:
        for ext in (".log", ".status", ".timing.tsv"):
            if os.path.exists(mine + ext):
                os.unlink(mine + ext)


def test_project_is_scoped_to_the_sources_being_built(tmp_path):
    """--sources scoped the fetch but not the mine: `project` was run with no source list, so any
    stale out/<dir>/columns.tsv from an earlier session leaked into project.xml (phase 11 then died
    loading humanmine-expressionatlas, which was never in the panel)."""
    plan = _plan(tmp_path, "--only", "rdfc2im_project", "--sources", "go hgnc")
    if plan is None:
        return
    cmd = next(c for c in _cmds(plan) if "rdfc2im project" in c)
    assert cmd.endswith("rdfc2im project --source go --source hgnc"), cmd
