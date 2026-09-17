"""Per-source pipeline steps built on mapping.translate()."""
from __future__ import annotations
import glob
import os
import re
from typing import Dict, List, Optional

from lxml import etree

from .mapping import translate, Knowledge, read_tsv, write_tsv, LOADABLE
from .sparql import (QueryBuilder, table_names, query_rows, const_rows, write_sparql_yaml,
                     yaml_block, block_name)
from .tsv import clean_table
from .fetch import fetch_query
from .scope import apply_taxon_scope, apply_gene_scope, apply_publication_scope, DEFAULT_TAXA

COL_COLUMNS = ["table", "position", "column", "variable", "im_class", "im_field", "transform",
               "filter", "value", "kind", "via", "status", "required", "root"]


def run_source(model, config_dir: str, out_dir: str, knowledge: Knowledge, source_cfg: dict,
               include_guess: bool = True, limit: int = 0, types: str = "root",
               use_from: bool = True, log=print, taxa: Optional[List[str]] = None,
               gene_ids: Optional[List[str]] = None, gene_field: str = "primaryIdentifier",
               pmids: Optional[List[str]] = None) -> dict:
    """`taxa`/`gene_ids`/`pmids` restrict THIS run's generated queries only -
    mapping_predicates.sssom.tsv is written by translate() above before any of them is applied,
    so the committed mapping always reflects the default (human, no restriction) regardless of
    what a given run asks for. See scope.py. `taxa=None`/`["9606"]` and `gene_ids`/`pmids=None`/
    `[]` are both the identity case: every existing caller that does not pass these gets exactly
    today's behaviour. `pmids` is for PubMed specifically - see apply_publication_scope - and is
    independent of gene_ids/gene_field since PubMed has no Gene field of its own to restrict."""
    res = translate(model, config_dir, out_dir, knowledge, source_cfg)
    cfg, rows, node_by_key = res["cfg"], res["rows"], res["node_by_key"]
    skip_reason = None
    if taxa and list(taxa) != DEFAULT_TAXA:
        rows, skip_reason = apply_taxon_scope(rows, list(taxa))
    if not skip_reason and gene_ids:
        rows, skip_reason = apply_gene_scope(rows, gene_field, list(gene_ids))
    if not skip_reason and pmids:
        rows, skip_reason = apply_publication_scope(rows, list(pmids))
    if skip_reason:
        log(f"{cfg.name}: skipped this run - {skip_reason}")
        for old in glob.glob(os.path.join(out_dir, "queries", "*.sparql")):
            os.remove(old)
        return {"counts": _counts(rows), "tables": 0, "new_fields": 0, "messages": res["messages"],
                "subjects": res["subjects"], "rows": rows, "cfg": cfg, "skipped": skip_reason}
    qb = QueryBuilder(cfg, node_by_key, types=types, distinct=source_cfg.get("distinct", True) is not False,
                      use_from=use_from)
    qdir = os.path.join(out_dir, "queries")
    os.makedirs(qdir, exist_ok=True)
    for old in glob.glob(os.path.join(qdir, "*.sparql")):
        os.remove(old)
    col_rows: List[dict] = []
    blocks: Dict[str, dict] = {}
    tables = table_names(rows) if include_guess else table_names([r for r in rows if r.get("status") != "guess"])
    n_tables = 0
    for t in tables:
        qrows = query_rows(rows, t, include_guess)
        if not qrows:
            continue
        sparql, select, params = qb.build(t, qrows, limit)
        if not select:
            continue
        n_tables += 1
        with open(os.path.join(qdir, f"{t}.sparql"), "w", encoding="utf-8") as fh:
            fh.write(sparql)
        # the class this table's rows are about: the root subject the query was built around
        # (see items.table_root for why this is recorded rather than inferred)
        table_root = next((node_by_key[k].root.im_class for k in
                           ((r["subject"], r["predicate"], r["column"]) for r in qrows) if k in node_by_key), "")
        # columns.tsv: order = SELECT order, then constants
        pos = 0
        used = set()
        for r, var in zip(qb.kept, select):
            col_rows.append(dict(table=t, position=pos, column=r["column"], variable=var, im_class=r["im_class"],
                                 im_field=r["im_field"], transform=r.get("transform", ""), filter=r.get("filter", ""),
                                 value="", kind=r.get("kind", ""), via=r.get("via", ""), status=r["status"],
                                 required=r.get("required", "no"), root=table_root))
            pos += 1
        for c in const_rows(rows, t, include_guess):
            # one constant per field per link: the same field may hang off two different links
            k = (c["im_class"], c["im_field"], c.get("via", ""))
            if k in used:
                continue
            used.add(k)
            col_rows.append(dict(table=t, position=pos, column=c["column"], variable="", im_class=c["im_class"],
                                 im_field=c["im_field"], transform="", filter="", value=c.get("value", ""),
                                 kind="const", via=c.get("via", ""), status=c["status"], required="no",
                                 root=table_root))
            pos += 1
        root_var = cfg.subjects[0].name if not res["builder"].roots else res["builder"].roots[0].subject.name
        variables = [root_var] + [r["column"] for r in qrows]
        blocks[block_name(t)] = yaml_block(t, variables, {r["column"]: r["value"] for r in qrows if r.get("value")},
                                           limit, cfg)
    write_tsv(os.path.join(out_dir, "columns.tsv"), col_rows, COL_COLUMNS)
    write_sparql_yaml(cfg, out_dir, blocks)
    n_new = write_additions(model, rows, os.path.join(out_dir, "additions.xml"), include_guess)
    counts = _counts(rows)
    msgs = res["messages"] + qb.messages
    report = [f"source: {cfg.name}", f"endpoint: {cfg.endpoint}",
              f"subjects: {len(res['subjects'])} ({_subject_counts(res['subjects'])})",
              f"rows: {len(rows)}  " + "  ".join(f"{k}={v}" for k, v in counts.items()),
              f"tables with a query: {n_tables} ({', '.join(t for t in tables)})",
              f"new fields proposed: {n_new}",
              f"human edits preserved on this run: predicates={res['preserved']} subjects={res['subject_edits']}",
              f"stale rows kept: {res['stale']}"] + [f"note: {m}" for m in msgs]
    with open(os.path.join(out_dir, "report.txt"), "w") as fh:
        fh.write("\n".join(report) + "\n")
    log(f"{cfg.name}: {counts}  tables={n_tables} new-fields={n_new} preserved-edits={res['preserved']}")
    return {"counts": counts, "tables": n_tables, "new_fields": n_new, "messages": msgs,
            "subjects": res["subjects"], "rows": rows, "cfg": cfg}


def _counts(rows: List[dict]) -> Dict[str, int]:
    """Counts over *active* rows (not under a dropped/skipped branch); `pruned` = rows under one."""
    c: Dict[str, int] = {}
    pruned = 0
    for r in rows:
        if r.get("table") == "drop":
            pruned += 1
            continue
        c[r.get("status", "?")] = c.get(r.get("status", "?"), 0) + 1
    out = {k: c.get(k, 0) for k in ("sure", "guess", "human", "todo", "drop", "link")}
    out["pruned"] = pruned
    return out


def _subject_counts(subjects: List[dict]) -> str:
    c: Dict[str, int] = {}
    for s in subjects:
        c[s.get("status", "?")] = c.get(s.get("status", "?"), 0) + 1
    return ", ".join(f"{k}={v}" for k, v in sorted(c.items()))


def write_additions(model, rows: List[dict], path: str, include_guess: bool) -> int:
    ok = {"sure", "human"} | ({"guess"} if include_guess else set())
    classes: Dict[str, etree._Element] = {}
    root = etree.Element("classes")
    n = 0
    for r in rows:
        if r.get("status") not in ok or not r.get("im_class") or not r.get("im_field"):
            continue
        cls, fld = r["im_class"], r["im_field"]
        if model.field(cls, fld) is not None:
            continue
        if cls not in classes:
            attrs = {"name": cls, "is-interface": "true"}
            if not model.has_class(cls):
                attrs["extends"] = "TODO-set-parent"
            classes[cls] = etree.SubElement(root, "class", **attrs)
            if not model.has_class(cls):
                classes[cls].append(etree.Comment(f" class {cls} is not in the model; set extends and remove this comment "))
        if any(a.get("name") == fld for a in classes[cls] if isinstance(a.tag, str)):
            continue
        etree.SubElement(classes[cls], "attribute", name=fld, type=_java_type(r.get("example", "")))
        n += 1
    etree.indent(root, space="  ")
    with open(path, "wb") as fh:
        fh.write(b'<?xml version="1.0"?>\n<!-- proposed new fields for this source (rdfc2im); rows whose im_field is not in the model -->\n')
        fh.write(etree.tostring(root, pretty_print=True))
    return n


def _java_type(example: str) -> str:
    e = (example or "").strip()
    if re.fullmatch(r"-?\d+", e):
        return "java.lang.Integer"
    if re.fullmatch(r"-?\d*\.\d+([eE][-+]?\d+)?", e):
        return "java.lang.Double"
    if e.lower() in ("true", "false"):
        return "java.lang.Boolean"
    return "java.lang.String"


# ---------------------------------------------------------------- fetch + tsv
def fetch_source(out_dir: str, dry_run=False, force=False, sleep=1.0, timeout=600, limit=None, page=0, log=print) -> Dict[str, str]:
    res = {}
    for q in sorted(glob.glob(os.path.join(out_dir, "queries", "*.sparql"))):
        t = os.path.splitext(os.path.basename(q))[0]
        res[t] = fetch_query(q, os.path.join(out_dir, "raw", f"{t}.tsv"), timeout=timeout,
                             dry_run=dry_run, force=force, sleep=sleep, limit=limit, page=page, log=log)
    return res


def tsv_source(out_dir: str, log=print) -> Dict[str, dict]:
    cols = read_tsv(os.path.join(out_dir, "columns.tsv"))
    tables: Dict[str, List[dict]] = {}
    for c in cols:
        tables.setdefault(c["table"], []).append(c)
    res = {}
    for t, cl in tables.items():
        raw = os.path.join(out_dir, "raw", f"{t}.tsv")
        if not os.path.exists(raw):
            continue
        cl = sorted(cl, key=lambda c: int(c["position"]))
        st = clean_table(raw, os.path.join(out_dir, "tsv", f"{t}.tsv"), cl)
        res[t] = st
        log(f"  {os.path.basename(out_dir)}/{t}: {st}")
    return res
