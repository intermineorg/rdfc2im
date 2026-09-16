"""Clean W3C SPARQL-results TSV into plain, loadable TSV and apply column transforms.

W3C SPARQL TSV encodes every cell as an RDF term: <iri>, "literal", "v"^^<dt>, "v"@lang,
and escapes tab/newline inside literals.  The delimited loader wants bare values.
"""
from __future__ import annotations
import csv
import os
import re
from typing import Dict, Iterable, List, Optional

TYPED = re.compile(r'^"(.*)"\^\^<[^>]*>$', re.S)
LANG = re.compile(r'^"(.*)"@[A-Za-z0-9-]+$', re.S)
QUOTED = re.compile(r'^"(.*)"$', re.S)
TYPED_CURIE = re.compile(r'^"(.*)"\^\^[A-Za-z][\w-]*:[\w.-]+$', re.S)


def unescape_literal(s: str) -> str:
    # SPARQL TSV: \t \n \r \" \\ inside literals
    out, i = [], 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            n = s[i + 1]
            out.append({"t": " ", "n": " ", "r": " ", '"': '"', "\\": "\\"}.get(n, n))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def clean_term(v: str) -> str:
    v = v.strip()
    if not v:
        return ""
    if v.startswith("<") and v.endswith(">"):
        return v[1:-1]
    for rx in (TYPED, TYPED_CURIE, LANG, QUOTED):
        m = rx.match(v)
        if m:
            return unescape_literal(m.group(1)).replace("\t", " ").replace("\n", " ").replace("\r", " ").strip()
    if v.startswith("_:"):
        return v
    return v


def _header_name(h: str) -> str:
    """Accept ?id, "id", id, ?"id" - Virtuoso quotes header names, others prefix ?."""
    h = h.strip().lstrip("?").strip()
    if len(h) >= 2 and h[0] == h[-1] and h[0] in "\"'":
        h = h[1:-1]
    return h.strip().lstrip("?")


def read_sparql_tsv(path: str) -> List[List[str]]:
    with open(path, encoding="utf-8", newline="") as fh:
        text = fh.read().replace("\r\n", "\n").replace("\r", "\n")
    rows = []
    for ln in text.split("\n"):
        if ln == "":
            continue
        rows.append(ln.split("\t"))
    if rows:
        rows[0] = [_header_name(h) for h in rows[0]]
        rows[1:] = [[clean_term(c) for c in r] for r in rows[1:]]
    return rows


# ------------------------------------------------------------- transforms
def apply_transform(value: str, spec: str) -> List[str]:
    """Return a list (a `split:` transform may explode one value into several)."""
    vals = [value]
    for step in _split_steps(spec):
        if not step:
            continue
        name, _, arg = step.partition(":")
        nv = []
        for v in vals:
            if name == "iri_localname":
                nv.append(re.split(r"[/#]", v.rstrip("/"))[-1] if v else v)
            elif name == "prefix":
                nv.append(arg + v if v and not v.startswith(arg) else v)
            elif name == "strip_prefix":
                nv.append(v[len(arg):] if v.startswith(arg) else v)
            elif name == "replace":
                a, _, b = arg.partition(":")
                nv.append(v.replace(a.replace("\\:", ":"), b.replace("\\:", ":")))
            elif name == "regex":
                m = re.search(arg, v)
                nv.append((m.group(1) if m.groups() else m.group(0)) if m else "")
            elif name == "split":
                nv.extend(x.strip() for x in v.split(arg or ",") if x.strip())
            elif name == "lower":
                nv.append(v.lower())
            elif name == "upper":
                nv.append(v.upper())
            elif name == "int":
                m = re.search(r"-?\d+", v)
                nv.append(m.group(0) if m else "")
            else:
                raise ValueError(f"unknown transform {name}")
        vals = nv
    return vals


def _split_steps(spec: str) -> List[str]:
    """Split on commas that are not escaped with a backslash."""
    parts, cur, i = [], "", 0
    while i < len(spec):
        c = spec[i]
        if c == "\\" and i + 1 < len(spec) and spec[i + 1] == ",":
            cur += ","; i += 2; continue
        if c == ",":
            parts.append(cur); cur = ""
        else:
            cur += c
        i += 1
    parts.append(cur)
    return [p.strip() for p in parts]


def passes_filter(value: str, spec: str) -> bool:
    if not spec:
        return True
    name, _, arg = spec.partition(":")
    if name == "regex":
        return re.search(arg, value) is not None
    if name == "nonempty":
        return value != ""
    if name == "equals":
        return value == arg
    raise ValueError(f"unknown filter {spec}")


def clean_table(raw_path: str, out_path: str, columns: List[dict]) -> dict:
    """columns: [{column, variable, im_class, im_field, transform, filter, value, kind, required}] in output order.

    Rows from the raw TSV are matched by `variable` (the SPARQL variable); `kind=const`
    columns are appended with `value`.  Returns stats.
    """
    raw = read_sparql_tsv(raw_path)
    if not raw:
        return {"rows_in": 0, "rows_out": 0}
    header, data = raw[0], raw[1:]
    idx = {h: i for i, h in enumerate(header)}
    missing = [c["variable"] for c in columns if c.get("kind") != "const" and c["variable"] not in idx]
    stats = {"rows_in": len(data), "rows_out": 0, "filtered": 0, "empty": 0, "missing_vars": missing}
    if missing:
        print(f"  WARNING {os.path.basename(raw_path)}: variables not in the raw header {missing}; header is {header}")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t", lineterminator="\n", quoting=csv.QUOTE_NONE, escapechar="\\")
        w.writerow([f"{c['im_class']}.{c['im_field']}" for c in columns])
        seen = set()
        for r in data:
            out_rows = [[]]
            keep = True
            for c in columns:
                if c.get("kind") == "const":
                    vals = [c.get("value", "")]
                else:
                    i = idx.get(c["variable"])
                    v = r[i] if i is not None and i < len(r) else ""
                    if not passes_filter(v, c.get("filter", "")):
                        if c.get("required") == "yes":
                            keep = False       # required column fails its filter: drop the row
                            break
                        v = ""                 # otherwise just blank the value
                    vals = apply_transform(v, c.get("transform", "")) if c.get("transform") else [v]
                    if not vals:
                        vals = [""]
                out_rows = [row + [x] for row in out_rows for x in vals]
            if not keep:
                stats["filtered"] += 1
                continue
            for row in out_rows:
                key = tuple(row)
                if not any(v for c, v in zip(columns, row) if c.get("kind") != "const"):
                    stats["empty"] += 1
                    continue          # no data at all in this row
                if key in seen:
                    continue
                seen.add(key)
                w.writerow([x.replace("\t", " ") for x in row])
                stats["rows_out"] += 1
    return stats
