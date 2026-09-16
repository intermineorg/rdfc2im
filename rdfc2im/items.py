"""Emit InterMine Items XML from a source's cleaned tables (out/<src>/tsv/*.tsv + columns.tsv).

Every row of every table is split into objects by (class, via): the root object (via empty)
plus one object per linked class.  Objects are keyed by the class's key attribute (or by all
their values when the class has none), so the same Gene met in ten tables is one <item>.
Links follow the mapping's `via` (Gene.synonyms -> Synonym); reverse references are set too
(Synonym.subject -> Gene).  Same-class links (OntologyTerm.parents) work because the target is
simply another item of the root class.

Output: out/<src>/items/<src>.xml, loadable by the stock `intermine-items-xml-file` source
(here registered as `humanmine-items` so keys/additions can travel with it).
"""
from __future__ import annotations
import csv
import os
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import quoteattr

from .mapping import read_tsv
from .model import InterMineModel


class Item:
    __slots__ = ("cls", "attrs", "refs", "colls")

    def __init__(self, cls: str):
        self.cls = cls
        self.attrs: Dict[str, str] = {}
        self.refs: Dict[str, tuple] = {}
        self.colls: Dict[str, List[tuple]] = {}


class ItemStore:
    def __init__(self, model: InterMineModel):
        self.model = model
        self.items: Dict[tuple, Item] = {}
        self.conflicts: List[str] = []

    def key_for(self, cls: str, values: Dict[str, str]) -> Optional[tuple]:
        ka = self.model.key_attribute(cls)
        if ka and values.get(ka):
            return (cls, ka, values[ka])
        vals = "|".join(f"{k}={v}" for k, v in sorted(values.items()) if v)
        return (cls, "*", vals) if vals else None

    def get(self, cls: str, values: Dict[str, str]) -> Optional[tuple]:
        k = self.key_for(cls, values)
        if k is None:
            return None
        it = self.items.get(k)
        if it is None:
            it = Item(cls)
            self.items[k] = it
        for f, v in values.items():
            if not v:
                continue
            if f in it.attrs and it.attrs[f] != v:
                if len(self.conflicts) < 50:
                    self.conflicts.append(f"{cls} {k[2]}: {f} = {it.attrs[f]!r} vs {v!r} (first kept)")
                continue
            it.attrs[f] = v
        return k

    def link(self, src_key: tuple, field: str, dst_key: tuple):
        it = self.items[src_key]
        fd = self.model.field(it.cls, field)
        if fd is None:
            return
        if fd.kind == "collection":
            lst = it.colls.setdefault(field, [])
            if dst_key not in lst:
                lst.append(dst_key)
        else:
            it.refs[field] = dst_key
        # reverse side
        if fd.reverse:
            dst = self.items[dst_key]
            rfd = self.model.field(dst.cls, fd.reverse)
            if rfd is not None:
                if rfd.kind == "collection":
                    lst = dst.colls.setdefault(fd.reverse, [])
                    if src_key not in lst:
                        lst.append(src_key)
                else:
                    dst.refs[fd.reverse] = src_key

    def write(self, path: str) -> int:
        ids = {k: f"0_{i + 1}" for i, k in enumerate(self.items)}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0" encoding="UTF-8"?>\n<items>\n')
            for k, it in self.items.items():
                fh.write(f'  <item id="{ids[k]}" class="{it.cls}" implements="">\n')
                for f, v in it.attrs.items():
                    fh.write(f'    <attribute name="{f}" value={quoteattr(v)}/>\n')
                for f, dk in it.refs.items():
                    if dk in ids:
                        fh.write(f'    <reference name="{f}" ref_id="{ids[dk]}"/>\n')
                for f, lst in it.colls.items():
                    refs = [dk for dk in lst if dk in ids]
                    if refs:
                        fh.write(f'    <collection name="{f}">' + "".join(f'<reference ref_id="{ids[dk]}"/>' for dk in refs) + "</collection>\n")
                fh.write("  </item>\n")
            fh.write("</items>\n")
        return len(ids)


def table_root(cols: List[dict]) -> str:
    """The class a table's rows are about: the root its query was designed around.

    Root choice is a design decision - a table may be built around Protein or around Organism -
    and `rdfc2im translate` records the one it used in columns.tsv's `root` column.  Linking must
    follow that choice.  It used to be inferred from column order instead (first required column,
    else first column), which picks whatever happens to come first: every UniProt table came out
    rooted at Organism, because the taxon constraint precedes the accession, and GWAS Catalog at
    Gene; links were then sought from a class the rows are not about, and Protein, Synonym,
    Publication, GWASResult and GWAS items were created unlinked.  The inference remains only as a
    fallback for columns.tsv files written before the column existed.
    """
    for c in cols:
        if c.get("root"):
            return c["root"]
    return next((c["im_class"] for c in cols if c.get("required") == "yes"), cols[0]["im_class"])


def _via_from(model: InterMineModel, from_cls: str, to_cls: str) -> str:
    for fname, fd in model.all_fields(from_cls).items():
        if fd.kind != "attribute" and (fd.type == to_cls or model.is_a(to_cls, fd.type)):
            return f"{from_cls}.{fname}"
    return ""


def emit_items(model: InterMineModel, out_dir: str, src: str, source_cfg: dict, log=print) -> dict:
    cols = read_tsv(os.path.join(out_dir, "columns.tsv"))
    tables: Dict[str, List[dict]] = {}
    for c in cols:
        tables.setdefault(c["table"], []).append(c)
    store = ItemStore(model)
    stats = {"tables": 0, "rows": 0, "items": 0, "notes": []}
    classes_seen: List[str] = []
    for table, cl in tables.items():
        tsv = os.path.join(out_dir, "tsv", f"{table}.tsv")
        if not os.path.exists(tsv):
            continue
        cl = sorted(cl, key=lambda c: int(c["position"]))
        root_cls = table_root(cl)
        # object groups in column order: (class, via) -> [column dicts]
        groups: List[Tuple[Tuple[str, str], List[dict]]] = []
        for c in cl:
            cls, via = c["im_class"], c.get("via") or ""
            if cls != root_cls and not via:
                via = _via_from(model, root_cls, cls)
                if not via and f"{table}:{cls}" not in stats["notes"]:
                    stats["notes"].append(f"{table}: no link from {root_cls} to {cls}; {cls} items are created but unlinked")
            # same-class link through an ancestor-typed reference: the target is the root class
            if via and cls != root_cls and model.is_a(root_cls, cls):
                cls = root_cls
            key = (cls, via)
            for g in groups:
                if g[0] == key:
                    g[1].append(c)
                    break
            else:
                groups.append((key, [c]))
        with open(tsv, encoding="utf-8", newline="") as fh:
            rd = csv.reader(fh, delimiter="\t")
            header = next(rd, None)
            if header is None:
                continue
            stats["tables"] += 1
            for row in rd:
                stats["rows"] += 1
                if len(row) < len(header):
                    row = row + [""] * (len(header) - len(row))
                keys: Dict[Tuple[str, str], tuple] = {}
                for (cls, via), gcols in groups:
                    values = {c["im_field"]: row[int(c["position"])] for c in gcols if c["im_field"]}
                    real = [c for c in gcols if c.get("kind") != "const"]
                    if real and not any(row[int(c["position"])] for c in real):
                        continue          # its data columns are all empty: no object in this row
                    k = store.get(cls, values)
                    if k is not None:
                        keys[(cls, via)] = k
                        if cls not in classes_seen:
                            classes_seen.append(cls)
                for (cls, via), k in keys.items():
                    if not via:
                        continue
                    vcls, vf = via.split(".", 1)
                    # the linking object: the group with class vcls (root first)
                    src_key = keys.get((vcls, "")) or next((kk for (c2, v2), kk in keys.items() if c2 == vcls and (c2, v2) != (cls, via)), None)
                    if src_key is None or src_key == k:
                        continue
                    store.link(src_key, vf, k)
    # DataSet / DataSource
    ds_title = source_cfg.get("data_set_title") or f"{src} (rdf-config)"
    ds_name = source_cfg.get("data_source_name") or src
    if store.items:
        dsrc = store.get("DataSource", {"name": ds_name})
        dset = store.get("DataSet", {"name": ds_title})
        if dsrc and dset:
            store.link(dset, "dataSource", dsrc)
        for k, it in list(store.items.items()):
            if it.cls in ("DataSet", "DataSource"):
                continue
            if "dataSets" in model.all_fields(it.cls) and dset:
                it.colls.setdefault("dataSets", []).append(dset)
            elif "dataSet" in model.all_fields(it.cls) and dset:
                it.refs["dataSet"] = dset
    path = os.path.join(out_dir, "items", f"{src}.xml")
    if store.items:
        stats["items"] = store.write(path)
    stats["classes"] = classes_seen
    stats["conflicts"] = store.conflicts
    log(f"  {src}: {stats['tables']} tables, {stats['rows']} rows -> {stats['items']} items ({', '.join(classes_seen)})"
        + (f"; {len(store.conflicts)} attribute conflicts" if store.conflicts else ""))
    # These used to be collected and never shown, which is how every UniProt table stayed
    # mis-rooted: the warnings existed, nobody could see them.
    for note in dict.fromkeys(stats["notes"]):
        log(f"    WARNING {note}")
    return stats
