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
  rdfc2im cache        save|restore|verify|list checksummed copies of fetched data in cached_raw_data/

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
from .pipeline import run_source, fetch_source, fetch_source_by_keys, tsv_source
from .scope import DEFAULT_TAXA, resolve_ncbigene_symbols, resolve_ensembl_symbols
from .project import gen_project, check_project
from .items import emit_items
from .linkml import gen_linkml
from .docs import write_docs
from . import rawcache

HERE = os.path.dirname(__file__)
DEFAULTS = dict(
    model_dirs=["in/intermine_bio_model_bio_sources", "in/humanmine-bio-sources"],
    live_model="in/humanmine_model.xml",
    project_xml="in/humanmine_project.xml",
    priorities="in/humanmine_priorities.properties",   # merged into out/_mine/genomic_priorities.properties
    priorities_override="curation/priorities_override.properties",  # hand-curated order fixes

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
    scope=None,                    # {taxon: ["9606"]} - default and only if unset; see scope.py
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
    _add_cache_parser(sub)
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
        p.add_argument("--taxon", help="comma-separated NCBI taxon id(s) to build for (default: 9606, human). "
                       "A source with no other species in its own data (HGNC, ClinVar, GWAS Catalog) is "
                       "skipped rather than mislabelled when this is not the default.")
        p.add_argument("--genes", help="comma-separated gene symbols, or @path/to/file (one per line), "
                       "to restrict the build to. Applies only to sources that declare a "
                       "`gene_scope_field` (see rdfc2im/data/sources.yaml) - others are skipped with "
                       "a message, same as an unsupported --taxon.")
        p.add_argument("--pmids", help="comma-separated PubMed ids, or @path/to/file (one per line), "
                       "to restrict fetch to. Applies only to sources that declare a "
                       "`pmid_scope_field` (see rdfc2im/data/sources.yaml) - PubMed has no Gene "
                       "field of its own, so this is separate from --genes, and (unlike --genes) "
                       "restricts at fetch time via VALUES-batching, not a query-embedded FILTER, "
                       "since a panel's cited-publication list is far too large for that.")
    a = ap.parse_args(argv)
    if a.cmd == "cache":          # needs no workspace: it copies files between two directories
        return run_cache(a)
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


def _add_cache_parser(sub):
    p = sub.add_parser("cache", help="checksummed cache of fetched raw data (see rdfc2im/rawcache.py)")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--cache-dir", default=rawcache.DEFAULT_CACHE_DIR,
                        help=f"cache location (default {rawcache.DEFAULT_CACHE_DIR}/)")
    cs = p.add_subparsers(dest="cache_cmd", required=True)
    for name, arg, hlp in (("save", "src_dir", "directory to copy into the cache"),
                           ("restore", "dest_dir", "directory to make identical to the cached copy")):
        c = cs.add_parser(name, parents=[common])
        c.add_argument("group", help="cache entry name, e.g. a source name")
        c.add_argument(arg, help=hlp)
        c.add_argument("--meta", action="append", default=[], metavar="KEY=VALUE",
                       help="what the data was fetched for; on restore every KEY must match what "
                            "save recorded, or the restore is refused")
    c = cs.add_parser("verify", parents=[common], help="re-hash cached files against their MD5SUMS")
    c.add_argument("group", nargs="*", help="default: every entry")
    cs.add_parser("list", parents=[common])


def run_cache(a) -> int:
    try:
        meta = {}
        for kv in getattr(a, "meta", []):
            k, sep, v = kv.partition("=")
            if not sep or not k:
                raise rawcache.CacheError(f"--meta wants KEY=VALUE, got {kv!r}")
            meta[k] = v
        if a.cache_cmd == "save":
            i = rawcache.save(a.group, a.src_dir, a.cache_dir, meta)
            print(f"cache save {a.group}: {i['files']} files, {i['bytes']} bytes, verified -> {a.cache_dir}/{a.group}")
        elif a.cache_cmd == "restore":
            i = rawcache.restore(a.group, a.dest_dir, a.cache_dir, meta)
            print(f"cache restore {a.group}: {i['files']} files, {i['bytes']} bytes, verified "
                  f"(cached {i['saved_at']}) -> {a.dest_dir}")
        elif a.cache_cmd == "verify":
            names = a.group or rawcache.groups(a.cache_dir)
            if not names:
                raise rawcache.CacheError(f"nothing cached in {a.cache_dir}/")
            for g in names:
                i = rawcache.verify(g, a.cache_dir)
                print(f"ok  {g}: {i['files']} files, {i['bytes']} bytes, cached {i['saved_at']}")
        else:
            for g in rawcache.groups(a.cache_dir):
                i = rawcache.verify(g, a.cache_dir)
                print(f"{g}\t{i['files']} files\t{i['bytes']} bytes\t{i['saved_at']}\t{i['meta']}")
    except rawcache.CacheError as e:
        print(f"rdfc2im cache: {e}", file=sys.stderr)
        return 1
    return 0


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


def _resolve_taxa(a, ws) -> list:
    """CLI --taxon wins; else rdfc2im.yaml's scope.taxon; else the default (human only)."""
    if getattr(a, "taxon", None):
        return [t.strip() for t in a.taxon.split(",") if t.strip()]
    y = ws.get("scope") or {}
    return list(y.get("taxon") or DEFAULT_TAXA)


def _parse_list_arg(value: str) -> list:
    """A comma list, or @path/to/file (whitespace-separated tokens, one or more per line,
    '#'-comments ok - a curated list is easier to read grouped a dozen to a line under a
    category comment than strictly one per line, so a line is split on any whitespace rather
    than treated as a single token). Shared by --genes and --pmids."""
    if not value:
        return []
    if value.startswith("@"):
        out = []
        with open(value[1:]) as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    out.extend(line.split())
        return out
    return [s.strip() for s in value.split(",") if s.strip()]


def _resolve_genes(a) -> list:
    """CLI --genes: see _parse_list_arg."""
    return _parse_list_arg(getattr(a, "genes", None))


def _resolve_pmids(a) -> list:
    """CLI --pmids: see _parse_list_arg. Independent of --genes: PubMed has no Gene field of its
    own to restrict a gene-panel build against, so a demo build instead scopes it to the PMIDs
    already referenced by the genes/variants/associations loaded from every other source - see
    scope.py's extract_publication_pmids for gathering that list, and do_fetch's use of
    pipeline.fetch_source_by_keys for how it restricts PubMed's own fetch."""
    return _parse_list_arg(getattr(a, "pmids", None))


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
        taxa = _resolve_taxa(a, ws)
        genes = _resolve_genes(a)
        results = {}
        for s in sources:
            scfg = sources_cfg.get(s, {})
            gene_ids, gene_field = [], scfg.get("gene_scope_field", "")
            if genes and gene_field:
                # Each source keys genes in its own scheme: ncbigene/hgnc/clinvar share NCBI's
                # numeric Entrez id (hgnc's own primaryIdentifier IS the entrez id, via its
                # see_also_NCBIGene cross-reference - see knowledge.yaml), ensembl/gwascatalog
                # share the Ensembl gene id (gwascatalog's snp_gene_ids column carries Ensembl
                # ids too - see sources.yaml), and uniprot's own field is literally the symbol,
                # so it needs no resolution at all. A source with a gene_scope_field but no
                # entry here still gets no gene_ids, so run_source's apply_gene_scope will not
                # find its field mapped by identifiers that mean anything - safer to widen this
                # dict as more resolvers are added than to guess a resolver by source name.
                if gene_field == "symbol":
                    gene_ids = list(genes)
                else:
                    resolver = {
                        "ncbigene": resolve_ncbigene_symbols,
                        "hgnc": resolve_ncbigene_symbols,
                        "clinvar": resolve_ncbigene_symbols,
                        "ensembl": resolve_ensembl_symbols,
                        "gwascatalog": resolve_ensembl_symbols,
                    }.get(s)
                    if resolver:
                        gene_ids = resolver(genes, taxon=taxa[0])
            results[s] = run_source(m, os.path.join(ws["config_root"], s), os.path.join(out, s), kn,
                                    scfg, include_guess=ws["include_guess"], limit=ws["limit"],
                                    types=ws["types"], use_from=ws["use_from"], taxa=taxa,
                                    gene_ids=gene_ids, gene_field=gene_field or "primaryIdentifier")
        return results

    def do_fetch() -> int:
        """Fetch every source; returns 1 if any table failed or came back incomplete.

        Every table is still attempted (one bad endpoint should not hide the others' state), but
        the exit code must say so: `tsv` skips a table with no raw file without complaint and a
        "partial" file is just a shorter one, so a caller that only checks the exit code - as
        tools/full-build.sh under `set -e` does - would otherwise build on missing data.
        """
        pmids = _resolve_pmids(a)
        bad = []
        for s in sources:
            print(f"fetch {s}")
            scfg = sources_cfg.get(s, {})
            pmid_field = scfg.get("pmid_scope_field")
            if pmids and pmid_field:
                res = fetch_source_by_keys(os.path.join(out, s), pmid_field, [f'"{p}"' for p in pmids],
                                           timeout=a.timeout,
                                           sleep=float(os.environ.get("SLEEP", 1.0)) if a.sleep is None else a.sleep)
            else:
                res = fetch_source(os.path.join(out, s), dry_run=a.dry_run or os.environ.get("DRY_RUN") == "1",
                                   force=a.force or os.environ.get("FORCE") == "1",
                                   sleep=float(os.environ.get("SLEEP", 1.0)) if a.sleep is None else a.sleep,
                                   timeout=a.timeout,
                                   limit=a.limit,   # None = the LIMIT written by translate; 0 = none
                                   page=int(os.environ.get("ITERATE", 5000)) if a.iterate is None else a.iterate)
            bad += [(s, t, st) for t, st in (res or {}).items() if st in ("failed", "partial")]
        for s, t, st in bad:
            print(f"rdfc2im fetch: {s}/{t} {st} - its raw data is missing or incomplete", file=sys.stderr)
        return 1 if bad else 0

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
                           ws.get("source_version"), ws.get("priorities"),
                           ws.get("priorities_override"),
                           only=a.source or ws.get("sources"))

    def do_check(m):
        return check_project(out, os.path.join(out, "_mine"), m, ws["type"], ws.get("project_xml"), sources_cfg)

    def do_docs(m):
        write_docs(out, ws, sources_cfg, m)

    cmd = a.cmd
    if cmd == "allow":
        do_allow(); return 0
    if cmd == "fetch":
        return do_fetch()
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
        if a.fetch and do_fetch():
            return 1
        do_tsv()
        do_items(m)
        do_project(m)
        rc = do_check(m)
        do_docs(m)
        return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
