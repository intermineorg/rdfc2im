"""SSSOM encoding of the predicate mapping (out/<src>/mapping_predicates.sssom.tsv).

Internally rows are plain dicts with rdfc2im's own keys (see mapping.CW_COLUMNS); this module
converts to and from the SSSOM TSV layout: a commented YAML metadata block, then the standard
SSSOM columns, then rdfc2im's operational columns declared as extension slots (ext_*).

  subject_id            expanded rdf-config predicate IRI (rdfc2im:self / rdfc2im:const for the synthetic rows)
  subject_label         the rdf-config object variable (= the TSV column name)
  predicate_id          skos:exactMatch (sure/human) | skos:closeMatch (guess) | sssom:NoMapping (todo/drop/link)
  object_id             intermine:<Class>.<field>  (EDITABLE)  or sssom:NoTermFound
  object_label          same, unprefixed - derived, not edited
  mapping_justification semapv:ManualMappingCuration (human) | semapv:LexicalMatching (name) |
                        semapv:MappingChaining (term URI identity) | semapv:UnspecifiedMatching (knowledge/java)
  confidence            1.0 sure/human, 0.5 guess, empty otherwise
  comment               free text (EDITABLE) - rdfc2im's `note`
  ext_subject, ext_predicate, ext_table*, ext_status*, ext_basis, ext_transform*, ext_filter*,
  ext_value*, ext_required*, ext_via, ext_kind, ext_multi, ext_example        (* = EDITABLE)
"""
from __future__ import annotations
import csv
import io
import os
from typing import Dict, List, Tuple

import yaml

SSSOM_COLUMNS = ["subject_id", "subject_label", "predicate_id", "object_id", "object_label",
                 "mapping_justification", "confidence", "comment",
                 "ext_subject", "ext_predicate", "ext_table", "ext_status", "ext_basis", "ext_transform",
                 "ext_filter", "ext_value", "ext_required", "ext_via", "ext_kind", "ext_multi", "ext_example"]

EXT = {"subject": "ext_subject", "predicate": "ext_predicate", "table": "ext_table", "status": "ext_status",
       "basis": "ext_basis", "transform": "ext_transform", "filter": "ext_filter", "value": "ext_value",
       "required": "ext_required", "via": "ext_via", "kind": "ext_kind", "multi": "ext_multi",
       "example": "ext_example"}

INTERMINE_PREFIX = "https://w3id.org/intermine/"
RDFC2IM_PREFIX = "https://w3id.org/rdfc2im/"


def metadata(cfg, extra_curies: Dict[str, str] = None) -> dict:
    curies = {"intermine": INTERMINE_PREFIX, "rdfc2im": RDFC2IM_PREFIX,
              "skos": "http://www.w3.org/2004/02/skos/core#",
              "semapv": "https://w3id.org/semapv/vocab/", "sssom": "https://w3id.org/sssom/"}
    curies.update(cfg.prefixes or {})
    curies.update(extra_curies or {})
    ext = [{"slot_name": v, "property": f"rdfc2im:{k}", "type_hint": "xsd:string"} for k, v in EXT.items()]
    return {"mapping_set_id": f"{RDFC2IM_PREFIX}mappings/{cfg.name}",
            "mapping_set_title": f"rdf-config {cfg.name} -> HumanMine (rdfc2im)",
            "mapping_set_description": "Predicate-level mapping from the rdf-config model onto the InterMine "
                                       "model. Editable columns: object_id, comment, ext_table, ext_status, "
                                       "ext_transform, ext_filter, ext_value, ext_required. ext_status is the "
                                       "operational switch (sure/human/guess load; todo/drop/link do not).",
            "license": "https://creativecommons.org/publicdomain/zero/1.0/",
            "curie_map": curies,
            "extension_definitions": ext}


def _justification(r: dict) -> str:
    st, basis = r.get("status", ""), r.get("basis", "")
    if st == "human":
        return "semapv:ManualMappingCuration"
    if basis.startswith("name"):
        return "semapv:LexicalMatching"
    if basis == "term" or basis.startswith("term+"):
        return "semapv:MappingChaining"
    return "semapv:UnspecifiedMatching"


def _predicate(r: dict) -> str:
    st = r.get("status", "")
    if st in ("sure", "human") and r.get("im_class") and r.get("im_field"):
        return "skos:exactMatch"
    if st == "guess" and r.get("im_class") and r.get("im_field"):
        return "skos:closeMatch"
    return "sssom:NoMapping"


def to_sssom(r: dict, cfg) -> dict:
    pred = r.get("predicate", "")
    if pred == "-self-":
        sid = "rdfc2im:self"
    elif pred == "-const-":
        sid = "rdfc2im:const"
    else:
        sid = cfg.expand(pred) if cfg else pred
    im = f"{r.get('im_class','')}.{r.get('im_field','')}" if r.get("im_class") and r.get("im_field") else ""
    out = {"subject_id": sid, "subject_label": r.get("column", ""), "predicate_id": _predicate(r),
           "object_id": f"intermine:{im}" if im else ("sssom:NoTermFound" if r.get("status") not in ("drop", "link") else ""),
           "object_label": im, "mapping_justification": _justification(r),
           "confidence": {"sure": "1.0", "human": "1.0", "guess": "0.5"}.get(r.get("status", ""), ""),
           "comment": r.get("note", "")}
    for k, v in EXT.items():
        out[v] = r.get(k, "")
    return out


def from_sssom(s: dict) -> dict:
    r = {k: s.get(v, "") for k, v in EXT.items()}
    r["column"] = s.get("subject_label", "")
    r["note"] = s.get("comment", "")
    oid = (s.get("object_id") or "").strip()
    if oid.startswith("intermine:"):
        oid = oid[len("intermine:"):]
    if oid.startswith("sssom:") or not oid:
        r["im_class"], r["im_field"] = "", ""
    elif "." in oid:
        r["im_class"], r["im_field"] = oid.split(".", 1)
    else:
        r["im_class"], r["im_field"] = oid, ""
    return r


def write_sssom(path: str, rows: List[dict], cfg) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    meta = yaml.safe_dump(metadata(cfg), sort_keys=False, allow_unicode=True, width=120)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        for ln in meta.rstrip("\n").split("\n"):
            fh.write("#" + ln + "\n")
        w = csv.DictWriter(fh, fieldnames=SSSOM_COLUMNS, delimiter="\t", extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: ("" if v is None else str(v)) for c, v in to_sssom(r, cfg).items()})


def read_sssom(path: str) -> Tuple[dict, List[dict]]:
    """Return (metadata, internal rows).  Missing file -> ({}, [])."""
    if not os.path.exists(path):
        return {}, []
    with open(path, encoding="utf-8", newline="") as fh:
        text = fh.read()
    meta_lines, body = [], []
    for ln in text.split("\n"):
        if ln.startswith("#") and not body:
            meta_lines.append(ln[1:])
        else:
            body.append(ln)
    meta = yaml.safe_load("\n".join(meta_lines)) if meta_lines else {}
    rows = [from_sssom(dict(r)) for r in csv.DictReader(io.StringIO("\n".join(body)), delimiter="\t")]
    return meta or {}, rows
