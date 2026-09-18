#!/usr/bin/env python3
"""Generate, for each rdf-config source, an intermine_model.yaml written in the same
list/predicate/cardinality syntax as rdf-config's own model.yaml, but with each rdf-config
local variable name relabelled to the InterMine `Class.field` it maps onto (per that source's
mapping_predicates.sssom.tsv / mapping_subjects.tsv), where a real (sure/guess/human) mapping
exists. Rows that are todo/drop/link, or that rdfc2im could not confidently match, keep their
original rdf-config label and get an inline comment explaining why, rather than being silently
dropped or guessed at.

Output: intermine_model_yaml/<source>/intermine_model.yaml, one directory per source that has
both a fetched in/config/<source>/model.yaml and a translated out/<source>/mapping_*.tsv pair.

This is read-only over rdfc2im's existing outputs (out/, in/) - it does not fetch, translate,
or otherwise change any pipeline state. Re-run any time after `make translate` to refresh.
"""
from __future__ import annotations
import csv
import os
import sys
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from rdfc2im.rdfconfig import parse_model, Subject, Predicate, ObjectVar  # noqa: E402

OUT_DIR = os.path.join(ROOT, "intermine_model_yaml")


def _read_tsv(path):
    """Yield dict rows from an rdfc2im-generated TSV, skipping '#'-prefixed comment/header lines."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        lines = [ln for ln in fh if not ln.startswith("#")]
    if not lines:
        return
    reader = csv.DictReader(lines, delimiter="\t")
    for row in reader:
        yield row


def load_subject_classes(source_dir):
    """subject name -> (im_class, role, status)"""
    out = {}
    for row in _read_tsv(os.path.join(source_dir, "mapping_subjects.tsv")):
        out[row["subject"]] = (row.get("im_class") or "", row.get("role") or "", row.get("status") or "")
    return out


def load_predicate_map(source_dir):
    """(ext_subject, ext_predicate, subject_label) -> row dict, for the live (non-.base) SSSOM file."""
    out = {}
    for row in _read_tsv(os.path.join(source_dir, "mapping_predicates.sssom.tsv")):
        key = (row.get("ext_subject") or "", row.get("ext_predicate") or "", row.get("subject_label") or "")
        out[key] = row
    return out


def _quote(example):
    """Render an example value the way rdf-config's own model.yaml would (quoted literal vs bare CURIE/IRI)."""
    if example is None:
        return ""
    s = str(example)
    if s.startswith("<") and s.endswith(">"):
        return s
    if re.match(r"^[A-Za-z][\w.-]*:\S+$", s) and " " not in s:
        return s  # CURIE-looking, unquoted like rdf-config's own style
    return f'"{s}"'


def _label_for(pmap, subject_name, curie, var_name):
    row = pmap.get((subject_name, curie, var_name))
    if not row:
        return None, "no mapping row found for this predicate/variable"
    status = (row.get("ext_status") or "").strip()
    obj_label = (row.get("object_label") or "").strip()
    via = (row.get("ext_via") or "").strip()
    if status in ("sure", "guess", "human") and obj_label:
        note = f"rdf-config: {var_name} ({status})"
        if via:
            note += f" -> via {via}"
        return obj_label, note
    reason = status or "unmapped"
    comment_lines = (row.get("comment") or "").strip().splitlines()
    basis = comment_lines[0][:80] if comment_lines else ""
    return None, f"UNMAPPED ({reason}) - {basis}" if basis else f"UNMAPPED ({reason})"


def _local_name(curie):
    """rdfc2im's ext_subject path segments are the CURIE's local name (part after the first ':')."""
    curie = curie.strip()
    if ":" in curie and not curie.startswith("<"):
        return curie.split(":", 1)[1]
    return re.split(r"[/#]", curie.rstrip("/>"))[-1]


def render_subject(s: Subject, pmap, subject_classes, indent=0, path=None):
    path = path if path is not None else s.name
    pad = "  " * indent
    im_class, role, _status = subject_classes.get(path, ("", "", ""))
    tag = f"[{im_class}]" if im_class else ("[blank node]" if s.blank else "[?]")
    head = f"- {s.name}{tag}"
    if s.example:
        head += f" {s.example}"
    lines = [pad + head + ":"]
    if s.types:
        lines.append(pad + "  - a: " + (s.types[0] if len(s.types) == 1 else "[" + ", ".join(s.types) + "]"))
    for p in s.predicates:
        card = p.cardinality
        lines.append(pad + f"  - {p.curie}{card}:")
        child_path = f"{path}/{_local_name(p.curie)}"
        for ov in p.objects:
            if ov.inline is not None:
                lines.extend(render_subject(ov.inline, pmap, subject_classes, indent + 2, path=child_path))
                continue
            label, note = _label_for(pmap, path, p.curie, ov.name)
            shown = label if label else ov.name
            ex = _quote(ov.example) if ov.example is not None else ""
            row = pad + f"    - {shown}:" + (f" {ex}" if ex else "")
            row += f"  # {note}"
            lines.append(row)
    return lines


def generate_for_source(source):
    out_src_dir = os.path.join(ROOT, "out", source)
    model_path = os.path.join(ROOT, "in", "config", source, "model.yaml")
    if not os.path.exists(model_path):
        return None, f"no in/config/{source}/model.yaml (run tools/make-inputs.sh)"
    if not os.path.exists(os.path.join(out_src_dir, "mapping_predicates.sssom.tsv")):
        return None, f"no out/{source}/mapping_predicates.sssom.tsv (run `rdfc2im translate --source {source}`)"

    subjects = parse_model(model_path)
    if not subjects:
        return None, "model.yaml parsed to zero subjects"
    subject_classes = load_subject_classes(out_src_dir)
    pmap = load_predicate_map(out_src_dir)

    lines = [
        f"# Generated by tools/gen_intermine_model_yaml.py - DO NOT EDIT, re-run after `make translate`.",
        f"# Same list/predicate/cardinality syntax as rdf-config's own model.yaml for '{source}', with",
        "# each local variable name relabelled to the InterMine Class.field it maps onto, where",
        "# out/{0}/mapping_predicates.sssom.tsv records a real (sure/guess/human) mapping. Anything".format(source),
        "# todo/drop/link, or with no matching mapping row, keeps its original rdf-config label and",
        "# is marked UNMAPPED in an inline comment - nothing is silently dropped or guessed at.",
        "",
    ]
    for s in subjects:
        lines.extend(render_subject(s, pmap, subject_classes))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n", None


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", "-s", action="append",
                     help="restrict to these rdf-config sources (default: every translated source)")
    args = ap.parse_args()

    all_sources = sorted(
        d for d in os.listdir(os.path.join(ROOT, "out"))
        if os.path.isdir(os.path.join(ROOT, "out", d)) and d != "_mine"
    )
    if args.source:
        unknown = set(args.source) - set(all_sources)
        if unknown:
            sys.exit(f"gen_intermine_model_yaml: unknown source(s): {', '.join(sorted(unknown))}")
        sources = [s for s in all_sources if s in set(args.source)]
    else:
        sources = all_sources
    os.makedirs(OUT_DIR, exist_ok=True)
    ok, skipped = [], []
    for source in sources:
        content, err = generate_for_source(source)
        if err:
            skipped.append((source, err))
            continue
        src_out_dir = os.path.join(OUT_DIR, source)
        os.makedirs(src_out_dir, exist_ok=True)
        path = os.path.join(src_out_dir, "intermine_model.yaml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        ok.append(source)
    print(f"generated {len(ok)}: {', '.join(ok)}")
    if skipped:
        print(f"skipped {len(skipped)}:")
        for s, why in skipped:
            print(f"  {s}: {why}")


if __name__ == "__main__":
    main()
