"""rdfc2im command line.

  rdfc2im allow        write out/humanmine.allow from project.xml (+ curation/extra_allow.txt)
  rdfc2im translate    per source: mapping_subjects.tsv, mapping_predicates.sssom.tsv (3-way merged), columns.tsv,
                       queries/*.sparql, sparql.yaml, additions.xml, report.txt
  rdfc2im fetch        POST queries/*.sparql -> raw/*.tsv   (needs network)
  rdfc2im tsv          raw/*.tsv -> tsv/*.tsv (clean + transforms + constants + filters)
  rdfc2im items        tsv/*.tsv -> items/<source>.xml (InterMine Items XML: attributes, references, collections)
  rdfc2im project      out/_mine/{project.xml, keys, additions, priorities, replaced_sources, links_report}
  rdfc2im check        validate mappings vs model, via links, items files, keys, duplicates
  rdfc2im linkml       curation/linkml/humanmine.yaml from humanmine_model.json + humanmine_model.xml
  rdfc2im docs         out/_docs/{STATUS.md, CURATION_GUIDE.md}
  rdfc2im all          allow + translate + tsv + items + project + check + docs  (fetch only with --fetch)

All paths come from rdfc2im.yaml in the working directory (or --workspace); flags override.
"""
from __future__ import annotations
import argparse
import os
import sys

try:
    import yaml
    import lxml  # noqa: F401
except ImportError as e:  # pragma: no cover
    sys.exit(f"rdfc2im needs the '{e.name}' package: pip install -r requirements.txt  (pyyaml lxml)")

from . import __version__
from .allow import write_allow
from .model import load_model
from .mapping import Knowledge
from .pipeline import run_source, fetch_source, tsv_source
from .project import gen_project, check_project
from .items import emit_items
from .linkml import gen_linkml
from .docs import write_docs

HERE = os.path.dirname(__file__)
DEFAULTS = dict(
    model_dirs=["in/intermine_bio_model_bio_sources", "in/humanmine-bio-sources"],
    live_model="in/humanmine_model.xml",
    project_xml="in/humanmine_project.xml",
    priorities="in/humanmine_priorities.properties",   # merged into out/_mine/genomic_priorities.properties
    config_root="in/rdf-config-config-only/config",
    out="out",
    sources=None,                 # None = every source listed in sources.yaml with scope good/structural
    type="humanmine-items",
    model_json="in/humanmine_model.json",
    src_data_dir="/micklem/data/rdfc2im",
    source_version="4.3.0",   # humanmine-bio-sources' gradle project version
    include_guess=True,
    limit=20,
    types="root",
    use_from=True,
    extra_allow="curation/extra_allow.txt",
    extensions="curation/extensions_additions.xml",
    knowledge=None,               # None = the package's data/knowledge.yaml
    sources_yaml=None,            # None = the package's data/sources.yaml
)


def load_ws(path: str) -> dict:
    ws = dict(DEFAULTS)
    if os.path.exists(path):
        with open(path) as fh:
            ws.update({k: v for k, v in (yaml.safe_load(fh) or {}).items() if v is not None})
    return ws


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rdfc2im", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", "-w", default="rdfc2im.yaml")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("allow", "translate", "fetch", "tsv", "items", "project", "check", "linkml", "docs", "all"):
        p = sub.add_parser(name)
        p.add_argument("--source", "-s", action="append", help="restrict to these rdf-config sources")
        p.add_argument("--limit", type=int, help="LIMIT for generated queries; on fetch, overrides the query's LIMIT (0 = none)")
        p.add_argument("--no-guess", action="store_true", help="exclude guess rows from queries/columns")
        p.add_argument("--types", choices=["root", "union", "all"], help="which nodes get an `a <type>` triple")
        p.add_argument("--no-from", action="store_true", help="omit FROM <graph> clauses")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--force", action="store_true", help="re-fetch existing raw TSVs")
        p.add_argument("--sleep", type=float, default=None, help="seconds between requests (default 1)")
        p.add_argument("--iterate", type=int, default=None, help="page size: LIMIT n OFFSET k until a short page (default 5000; 0 = one request per query)")
        p.add_argument("--timeout", type=int, default=600)
        p.add_argument("--fetch", action="store_true", help="(all) include the fetch step")
    a = ap.parse_args(argv)
    if not os.path.exists(a.workspace):
        sys.exit(f"rdfc2im: no {a.workspace} in {os.getcwd()} - run from the workspace directory "
                 f"(the one containing rdfc2im.yaml and the Makefile), or pass --workspace")
    ws = load_ws(a.workspace)
    if a.limit is not None:
        ws["limit"] = a.limit
    if a.no_guess:
        ws["include_guess"] = False
    if a.types:
        ws["types"] = a.types
    if a.no_from:
        ws["use_from"] = False
    return run(a, ws)


def check_inputs(ws, need_model=True, need_config=True):
    missing = []
    if need_config and not os.path.isdir(ws["config_root"]):
        missing.append(f"config_root: {ws['config_root']}")
    if need_model:
        for d in ws["model_dirs"]:
            if not os.path.isdir(d):
                missing.append(f"model_dirs entry: {d}")
        if not os.path.exists(ws["project_xml"]):
            missing.append(f"project_xml: {ws['project_xml']}")
    if missing:
        sys.exit("rdfc2im: input paths in rdfc2im.yaml do not exist (working directory: %s)\n  " % os.getcwd()
                 + "\n  ".join(missing) + "\nSee README.md, 'in/ layout'.")


def run(a, ws) -> int:
    out = ws["out"]
    if a.cmd not in ("fetch", "tsv", "items"):
        check_inputs(ws, need_config=a.cmd in ("translate", "all"))
    os.makedirs(out, exist_ok=True)
    allow_path = os.path.join(out, "humanmine.allow")
    with open(ws["sources_yaml"] or os.path.join(HERE, "data", "sources.yaml")) as fh:
        sources_cfg = yaml.safe_load(fh) or {}
    sources = a.source or ws["sources"] or [s for s, c in sources_cfg.items() if c.get("scope") in ("good", "structural")]
    sources = [s for s in sources if os.path.isdir(os.path.join(ws["config_root"], s))
               or print(f"skip {s}: no config dir") ]

    def do_allow():
        toks = write_allow(ws["project_xml"], allow_path)
        extra = ws.get("extra_allow")
        if extra and os.path.exists(extra):
            with open(extra) as fh:
                more = [l.strip() for l in fh if l.strip() and not l.startswith("#")]
            with open(allow_path, "a") as fh:
                fh.write("# from curation/extra_allow.txt\n" + "".join(t + "\n" for t in more))
            toks += more
        print(f"allow: {len(toks)} tokens -> {allow_path}")

    def model():
        if not os.path.exists(allow_path):
            do_allow()
        return load_model(ws["model_dirs"], allow_path,
                          ws.get("live_model") if os.path.exists(ws.get("live_model") or "") else None,
                          extra_additions=[ws.get("extensions")])

    def do_translate(m):
        kn = Knowledge(ws.get("knowledge"))
        results = {}
        for s in sources:
            results[s] = run_source(m, os.path.join(ws["config_root"], s), os.path.join(out, s), kn,
                                    sources_cfg.get(s, {}), include_guess=ws["include_guess"], limit=ws["limit"],
                                    types=ws["types"], use_from=ws["use_from"])
        return results

    def do_fetch():
        for s in sources:
            print(f"fetch {s}")
            fetch_source(os.path.join(out, s), dry_run=a.dry_run or os.environ.get("DRY_RUN") == "1",
                         force=a.force or os.environ.get("FORCE") == "1",
                         sleep=float(os.environ.get("SLEEP", 1.0)) if a.sleep is None else a.sleep, timeout=a.timeout,
                         limit=a.limit,   # None = the LIMIT written by translate; 0 = none
                         page=int(os.environ.get("ITERATE", 5000)) if a.iterate is None else a.iterate)

    def do_tsv():
        for s in sources:
            tsv_source(os.path.join(out, s))

    def do_items(m):
        n = 0
        for s in sources:
            if os.path.isdir(os.path.join(out, s, "tsv")):
                emit_items(m, os.path.join(out, s), s, sources_cfg.get(s, {})); n += 1
        if not n:
            print("items: no cleaned tables yet (run fetch + tsv first)")

    def do_project(m):
        return gen_project(out, os.path.join(out, "_mine"), m, ws["type"], ws["src_data_dir"],
                           ws.get("project_xml"), sources_cfg, ws.get("extensions"),
                           ws.get("source_version"), ws.get("priorities"))

    def do_check(m):
        return check_project(out, os.path.join(out, "_mine"), m, ws["type"], ws.get("project_xml"), sources_cfg)

    def do_docs(m):
        write_docs(out, ws, sources_cfg, m)

    cmd = a.cmd
    if cmd == "allow":
        do_allow(); return 0
    if cmd == "fetch":
        do_fetch(); return 0
    if cmd == "tsv":
        do_tsv(); return 0
    if cmd == "linkml":
        gen_linkml(ws.get("model_json"), ws.get("live_model"), os.path.join("curation", "linkml", "humanmine.yaml")); return 0
    if cmd == "all":
        do_allow()
    m = model()
    if cmd == "items":
        do_items(m); return 0
    if cmd == "translate":
        do_translate(m); return 0
    if cmd == "project":
        do_project(m); return 0
    if cmd == "check":
        return do_check(m)
    if cmd == "docs":
        do_docs(m); return 0
    if cmd == "all":
        do_translate(m)
        if a.fetch:
            do_fetch()
        do_tsv()
        do_items(m)
        do_project(m)
        rc = do_check(m)
        do_docs(m)
        return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
