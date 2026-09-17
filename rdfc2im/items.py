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
import unicodedata
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import quoteattr

from .mapping import read_tsv
from .model import InterMineModel

# InterMine's have.large.file.xml.tgt loader (FullXmlConverterTask -> the postgres COPY BINARY
# writer) cannot handle non-ASCII bytes: confirmed on a minimal repro that a single "alpha"
# (Greek letter, U+03B1) in one attribute value is enough to fail the whole retrieve with
# "PSQLException: invalid byte sequence for encoding UTF8: 0x00" - the writer appears to
# mis-handle multi-byte UTF-8 rather than reject it cleanly. have.file.xml.tgt (XmlDataLoaderTask)
# does not have this bug, but at real data volumes it opens far more concurrent connections than
# Postgres's default allows and is markedly slower - not a viable swap for every source. Rather
# than block every source with any non-ASCII text (HGNC's alt_label synonyms legitimately carry
# Greek letters, e.g. "ERalpha"), transliterate attribute values to their closest ASCII on the
# way into the items file. This only affects what InterMine loads, not rdfc2im's own tables,
# filters or dedup, which still see the real Unicode text.
_GREEK_TO_ASCII = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta",
    "ε": "epsilon", "ζ": "zeta", "η": "eta", "θ": "theta",
    "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu",
    "ν": "nu", "ξ": "xi", "ο": "omicron", "π": "pi",
    "ρ": "rho", "σ": "sigma", "ς": "sigma", "τ": "tau",
    "υ": "upsilon", "φ": "phi", "χ": "chi", "ψ": "psi",
    "ω": "omega", "ɣ": "gamma",
}
_PUNCT_TO_ASCII = {
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"', "−": "-",
}


def to_ascii(s: str) -> str:
    """Closest-ASCII transliteration for InterMine's items-loading path; see the module note.
    Known Greek letters spell out by name; known "smart" punctuation maps to its ASCII form;
    anything else goes through NFKD (handles ligatures and accented Latin - e.g. "fi" from the
    fi-ligature, "e" from "e-acute") and drops what still isn't ASCII, so no attribute value can
    ever carry a byte this loader chokes on. A character with no ASCII form at all becomes "?"
    rather than vanishing silently."""
    if s.isascii():
        return s
    out = []
    for ch in s:
        if ch in _GREEK_TO_ASCII:
            out.append(_GREEK_TO_ASCII[ch])
        elif ch in _PUNCT_TO_ASCII:
            out.append(_PUNCT_TO_ASCII[ch])
        elif ch.isascii():
            out.append(ch)
        else:
            decomposed = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
            out.append(decomposed if decomposed.isascii() and decomposed else "?")
    return "".join(out)


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
                    fh.write(f'    <attribute name="{f}" value={quoteattr(to_ascii(v))}/>\n')
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


def group_key(model: InterMineModel, root_cls: str, cls: str, via: str) -> Tuple[str, str]:
    """The object a column writes to, as (class, via) - exactly how emit_items groups columns.

    Columns sharing a key and a field write one attribute of one object, so only one value can
    survive.  translate uses this same function to keep that from happening, and check to report
    it, so the three can never disagree about what collides.
    """
    via = via or ""
    if cls != root_cls and not via:
        via = _via_from(model, root_cls, cls)
    # same-class link through an ancestor-typed reference: the target is the root class
    if via and cls != root_cls and model.is_a(root_cls, cls):
        cls = root_cls
    return cls, via


def _via_from(model: InterMineModel, from_cls: str, to_cls: str) -> str:
    for fname, fd in model.all_fields(from_cls).items():
        if fd.kind != "attribute" and (fd.type == to_cls or model.is_a(to_cls, fd.type)):
            return f"{from_cls}.{fname}"
    return ""


def resolve_key_ambiguity(store: ItemStore, cls: str, field: str,
                           prefer_field: Optional[str] = None, prefer_value: Optional[str] = None) -> int:
    """A field this source did not use as ITS OWN merge key (see ItemStore.key_for) can still be
    one InterMine uses in a cross-source integration key - Gene.secondaryIdentifier is not
    ncbigene's key (primaryIdentifier is), but `key_secondaryidentifier_org` merges on it against
    other sources.  If the *upstream data itself* gives two distinct objects in this source's own
    output the same value (real: 255 Ensembl gene ids in NCBI Gene's own cross-reference data are
    each shared by two or more different NCBI Entrez records, an overlapping-transcript/antisense
    annotation ambiguity, not an rdfc2im mapping error - see STATUS.md D14), loading it past that
    key either fails or merges the wrong two objects.

    Grouped by (field value, organism ref) since the key it stands in for is organism-scoped too.
    If prefer_field/prefer_value picks out exactly one holder of a duplicated value, the field is
    kept only there; otherwise the ambiguity has no principled answer and it is dropped from every
    holder - the same "can't resolve cleanly, so don't corrupt" rule `clean_table` already applies
    to a filter-failing required column. Returns the number of items the field was blanked on."""
    groups: Dict[tuple, List[Item]] = {}
    for it in store.items.values():
        if it.cls != cls:
            continue
        v = it.attrs.get(field)
        if v:
            groups.setdefault((v, it.refs.get("organism")), []).append(it)
    blanked = 0
    for (_, items) in ((k, v) for k, v in groups.items() if len(v) > 1):
        keep = None
        if prefer_field and prefer_value:
            matches = [it for it in items if it.attrs.get(prefer_field) == prefer_value]
            if len(matches) == 1:
                keep = matches[0]
        for it in items:
            if it is keep:
                continue
            del it.attrs[field]
            blanked += 1
    return blanked


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
            key = group_key(model, root_cls, c["im_class"], c.get("via") or "")
            if c["im_class"] != root_cls and not key[1]:
                stats["notes"].append(f"{table}: no link from {root_cls} to {c['im_class']}; "
                                      f"{c['im_class']} items are created but unlinked")
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
    for cls, kcfg in (source_cfg.get("key_ambiguity") or {}).items():
        n = resolve_key_ambiguity(store, cls, kcfg["field"], kcfg.get("prefer_field"), kcfg.get("prefer_value"))
        if n:
            stats["notes"].append(f"{cls}.{kcfg['field']}: blanked on {n} items - ambiguous in this "
                                   f"source's own data, not an rdfc2im mapping error (STATUS.md D14)")
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
