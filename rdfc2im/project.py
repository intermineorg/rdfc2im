"""Emit the mine's loader configuration for the Items XML route, and check it.

Outputs (in out/_mine/):
  project.xml                    HumanMine's project.xml with one <source> per rdf-config source
                                 (type humanmine-items = stock intermine-items-xml-file + keys/additions)
  replaced_sources.xml           the original <source> entries an rdf-config load supersedes
  humanmine-items_keys.properties    integration keys for every class the mappings touch
  humanmine-items_additions.xml      union of per-source additions + curation/extensions
  genomic_priorities.properties  HumanMine's priorities with our sources merged in: a replacement
                                 takes the slot of the source it replaces, a source loaded
                                 `alongside` sits right after its stock source, and fields written
                                 by more than one source get an entry
  links_report.txt               per source: classes, links (via) and their reverse references
"""
from __future__ import annotations
import copy
import os
from typing import Dict, List, Optional

from lxml import etree

from .mapping import read_tsv
from .model import InterMineModel
from .items import table_root

ABSTRACT = {"InterMineObject", "Annotatable", "BioEntity"}


def source_dirs(out_root: str) -> List[str]:
    return sorted(d for d in os.listdir(out_root)
                  if not d.startswith("_") and os.path.exists(os.path.join(out_root, d, "columns.tsv")))


def load_columns(out_root: str, src: str) -> Dict[str, List[dict]]:
    rows = read_tsv(os.path.join(out_root, src, "columns.tsv"))
    tables: Dict[str, List[dict]] = {}
    for r in rows:
        tables.setdefault(r["table"], []).append(r)
    return tables


def _prop(name: str, value: Optional[str] = None, location: Optional[str] = None) -> etree._Element:
    el = etree.Element("property", name=name)
    if location is not None:
        el.set("location", location)
    else:
        el.set("value", value or "")
    return el


def gen_project(out_root: str, project_out: str, model: InterMineModel, type_name: str,
                src_data_dir: str, humanmine_project: Optional[str], sources_cfg: dict,
                extensions_xml: Optional[str], source_version: Optional[str] = None,
                priorities: Optional[str] = None, log=print) -> dict:
    os.makedirs(project_out, exist_ok=True)
    report: List[str] = []
    new_sources: List[etree._Element] = []
    replaced_names: List[str] = []
    all_classes: List[str] = []
    writers: Dict[str, List[str]] = {}
    # fields each `alongside` source writes, attributes and references (collections just union)
    alongside: Dict[str, List[str]] = {}          # our source name -> stock sources it supplements
    alongside_fields: Dict[str, List[str]] = {}   # Class.field -> [our source names]
    for src in source_dirs(out_root):
        tables = load_columns(out_root, src)
        if not tables:
            continue
        scfg = sources_cfg.get(src, {})
        name = f"humanmine-{src}"
        el = etree.Element("source", name=name, type=type_name)
        # Sources that live in humanmine-bio-sources must name that project's version: the dbmodel
        # plugin's addSourceDependencies resolves org.intermine:<type>:<version>, falling back to
        # bioVersion (5.0.+) when the attribute is absent - which would never find our 4.3.0 jar.
        # Every humanmine-bio-sources entry in HumanMine's own project.xml carries version="4.3.0".
        if source_version:
            el.set("version", source_version)
        el.append(_prop("src.data.file", location=os.path.join(src_data_dir, src, f"{src}.xml")))
        new_sources.append(el)
        report.append(f"[{name}]")
        classes: List[str] = []
        links: List[str] = []
        for table, cols in tables.items():
            root_cls = table_root(cols)
            for c in cols:
                if c["im_class"] not in classes:
                    classes.append(c["im_class"])
                writers.setdefault(f"{c['im_class']}.{c['im_field']}", []).append(name)
                via = c.get("via") or ""
                if via:
                    vcls, vf = via.split(".", 1)
                    fd = model.field(vcls, vf)
                    rev = f" (reverse {fd.type}.{fd.reverse})" if fd is not None and fd.reverse else ""
                    s = f"{via} -> {c['im_class']}{rev}"
                    if s not in links:
                        links.append(s)
                elif c["im_class"] != root_cls:
                    s = f"{root_cls} ~> {c['im_class']} (implicit: first reference/collection from {root_cls})"
                    if s not in links:
                        links.append(s)
        for table, cols in tables.items():
            root_cls = table_root(cols)
            for f in _priority_fields(model, root_cls, cols):
                # Every source's reference fields (Gene.chromosome via a Chromosome-mapped
                # column, not just its own direct attributes) need to compete for priority the
                # same way attributes do - found only by an actual load: this loop used to run
                # only for `alongside` sources, so two *replacing* sources (humanmine-ncbigene
                # and humanmine-ensembl, neither alongside anything) both writing Gene.chromosome
                # had no priority entry at all, and the build refused with "Conflicting values
                # for field Gene.chromosome ... needs configuring" even though plain attribute
                # conflicts on the very same two sources (Gene.symbol, Gene.description) were
                # already handled correctly via `writers` below.
                writers.setdefault(f, [])
                if name not in writers[f]:
                    writers[f].append(name)
        if scfg.get("alongside"):
            alongside[name] = list(scfg["alongside"])
        for c in classes:
            if c not in all_classes:
                all_classes.append(c)
            if c in ABSTRACT:
                report.append(f"  WARNING {c} is abstract/interface-only - use the concrete class")
        report.append("  classes: " + ", ".join(classes))
        report.append("  links:   " + ("; ".join(links) or "(none)"))
        items = os.path.join(out_root, src, "items", f"{src}.xml")
        report.append("  items:   " + (items if os.path.exists(items) else "(not emitted yet - fetch, tsv, items)"))
        for r in scfg.get("replaces") or []:
            if r not in replaced_names:
                replaced_names.append(r)

    # ---- project.xml
    if humanmine_project and os.path.exists(humanmine_project):
        tree = etree.parse(humanmine_project)
        root = tree.getroot()
        sources_el = root.find("sources")
        replaced_el = etree.Element("sources")
        insert_at = None
        for s in list(sources_el):
            if s.tag == "source" and s.get("name") in replaced_names:
                if insert_at is None:
                    insert_at = list(sources_el).index(s)
                sources_el.remove(s)
                replaced_el.append(s)
        if insert_at is None:
            insert_at = len(sources_el)
        block = [el for el in new_sources if el.get("name") not in alongside]
        for i, el in enumerate(block):
            sources_el.insert(insert_at + i, el)
        # A supplement loads right after the stock source it supplements, never before: the stock
        # source's keys decide whether *its* objects merge, and reactome's keys file has no
        # Pathway key - loaded second, stock reactome would duplicate every pathway of ours.
        for el in new_sources:
            stocks = alongside.get(el.get("name"))
            if not stocks:
                continue
            kids = list(sources_el)
            idx = [i for i, s in enumerate(kids) if s.tag == "source" and s.get("name") in stocks]
            if not idx:
                sources_el.append(el)
                continue
            at = max(idx) + 1
            while at < len(kids) and kids[at].get("name") in alongside:
                at += 1                     # keep earlier supplements of the same source in order
            sources_el.insert(at, el)
        etree.indent(root, space="  ")
        tree.write(os.path.join(project_out, "project.xml"), pretty_print=True, xml_declaration=True, encoding="UTF-8")
        etree.indent(replaced_el, space="  ")
        with open(os.path.join(project_out, "replaced_sources.xml"), "wb") as fh:
            fh.write(b"<!-- original HumanMine sources superseded by an rdf-config load (rdfc2im project) -->\n")
            fh.write(etree.tostring(replaced_el, pretty_print=True))
    else:
        root = etree.Element("project", type="bio")
        se = etree.SubElement(root, "sources")
        for el in new_sources:
            se.append(el)
        etree.indent(root, space="  ")
        etree.ElementTree(root).write(os.path.join(project_out, "project.xml"), pretty_print=True,
                                      xml_declaration=True, encoding="UTF-8")

    # ---- keys
    lines = [f"# {type_name}_keys.properties - generated by rdfc2im project; inheritance-aware",
             "# (a key declared on OntologyTerm applies to GOTerm etc. - InterMine resolves keys up the hierarchy)", ""]
    emitted = set()
    for cls in sorted(set(all_classes) | {"DataSet", "DataSource"}):
        owner, keys = model.keys_for(cls)
        if not keys:
            ka = model.key_attribute(cls)
            if ka:
                lines.append(f"# DRAFT - no keys file declares a key for {cls}; review")
                lines.append(f"{cls}.key_{ka.lower()}={ka}")
            else:
                lines.append(f"# WARNING no key and no identifier attribute for {cls} - its items never merge")
            lines.append("")
            continue
        if owner != cls:
            lines.append(f"# {cls} inherits its keys from {owner}")
        if owner in emitted:
            lines.append("")
            continue
        emitted.add(owner)
        for kname, flds in keys.items():
            lines.append(f"{owner}.{kname}={', '.join(flds)}")
        lines.append("")
    with open(os.path.join(project_out, f"{type_name}_keys.properties"), "w") as fh:
        fh.write("\n".join(lines) + "\n")

    # ---- additions
    classes_el = etree.Element("classes")
    merged: Dict[str, etree._Element] = {}
    add_files = [os.path.join(out_root, s, "additions.xml") for s in source_dirs(out_root)]
    if extensions_xml and os.path.exists(extensions_xml):
        add_files.append(extensions_xml)
    for p in add_files:
        if not os.path.exists(p):
            continue
        for c in etree.parse(p).getroot().iter("class"):
            cname = c.get("name")
            if cname not in merged:
                merged[cname] = etree.SubElement(classes_el, "class", name=cname, **{"is-interface": c.get("is-interface", "true")})
                if c.get("extends"):
                    merged[cname].set("extends", c.get("extends"))
            have = {(f.tag, f.get("name")) for f in merged[cname]}
            for f in c:
                if isinstance(f.tag, str) and (f.tag, f.get("name")) not in have:
                    merged[cname].append(copy.deepcopy(f))
    # Anything the mappings use that the mine may not have must be carried here, or the load fails
    # with "class ... does not exist" - see _carry_missing_definitions for the two ways that
    # happens (absent from the live model; contributed only by a source we replace).
    carried = _carry_missing_definitions(model, writers, merged, classes_el)
    etree.indent(classes_el, space="  ")
    with open(os.path.join(project_out, f"{type_name}_additions.xml"), "wb") as fh:
        fh.write(b'<?xml version="1.0"?>\n<!-- generated by rdfc2im project: union of out/*/additions.xml + curation/extensions_additions.xml,\n     plus anything the mappings use that the live model lacks (see _carry_absent_from_live) -->\n')
        fh.write(etree.tostring(classes_el, pretty_print=True))

    # ---- priorities
    replaced_by: Dict[str, List[str]] = {}
    for src in source_dirs(out_root):
        for r in sources_cfg.get(src, {}).get("replaces") or []:
            replaced_by.setdefault(r, []).append(f"humanmine-{src}")
    text = merge_priorities(priorities if priorities and os.path.exists(priorities) else None,
                            writers, replaced_by, alongside, alongside_fields)
    with open(os.path.join(project_out, "genomic_priorities.properties"), "w") as fh:
        fh.write(text)
    with open(os.path.join(project_out, "links_report.txt"), "w") as fh:
        fh.write("\n".join(report) + "\n")
    if carried:
        log("project: carried into additions (absent from the live model): " + ", ".join(carried))
    log(f"project: {len(new_sources)} items sources, {len(replaced_names)} originals replaced, "
        f"{len(all_classes)} classes keyed -> {project_out}")
    return {"sources": len(new_sources), "replaced": replaced_names, "classes": all_classes, "report": report}


def _parse_priority(line: str):
    if line.lstrip().startswith("#") or "=" not in line:
        return None, None
    key, _, val = line.partition("=")
    return key.strip(), [v.strip() for v in val.split(",") if v.strip()]


def merge_priorities(base: Optional[str], writers: Dict[str, List[str]],
                     replaced_by: Dict[str, List[str]], alongside: Dict[str, List[str]],
                     alongside_fields: Dict[str, List[str]]) -> str:
    """HumanMine's genomic_priorities.properties with our sources merged in.

    Two sources writing different non-null values into one field stop the build unless the field
    has a priority that lists both (SourcePriorityComparator: "Conflicting values for field";
    sources left to `*` share one rank, so they conflict too).  So every field with more than one
    writer needs every writer named:

    - a source we replace hands its slot to the replacement (ncbi-gene -> humanmine-ncbigene);
    - a source loaded `alongside` a stock one goes right after it, so HumanMine's value stands;
      if the stock source is not named yet, both go in just before `*`;
    - any other writer of ours goes just before `*` (or last, if there is no `*`).

    A field HumanMine does not configure gets an entry when more than one of our sources writes it
    or an `alongside` source does; the latter ends in `*`, since the stock source's other
    co-writers are unknown here.  Without a base file only those entries are written.
    """
    ours_of = {f: list(dict.fromkeys(srcs)) for f, srcs in writers.items()}
    for f, srcs in alongside_fields.items():
        ours_of[f] = list(dict.fromkeys(ours_of.get(f, []) + srcs))

    def merge(order: List[str], fld: str) -> List[str]:
        out: List[str] = []
        for n in order:
            out.extend(replaced_by.get(n, [n]))
        out = list(dict.fromkeys(out))
        for n in ours_of.get(fld, []):
            if n in out:
                continue
            stocks = [st for st in alongside.get(n, [])]
            at = out.index("*") if "*" in out else len(out)
            placed = [out.index(st) for st in stocks if st in out]
            if placed:
                at = max(placed) + 1
                while at < len(out) and out[at] in alongside:
                    at += 1
            else:
                for st in stocks:
                    out.insert(at, st); at += 1
            out.insert(at, n)
        return out

    lines = ["# genomic_priorities.properties - generated by rdfc2im project from "
             + (os.path.basename(base) if base else "(no base file: rdfc2im entries only)"),
             "# Our sources are merged into HumanMine's entries; see project.merge_priorities.", ""]
    done = set()
    if base:
        for ln in open(base, encoding="utf-8").read().splitlines():
            key, order = _parse_priority(ln)
            if key is None:
                lines.append(ln)
                continue
            done.add(key)
            new = merge(order, key)
            lines.append(ln if new == order else f"{key} = {', '.join(new)}")
    added = []
    for fld in sorted(ours_of):
        if fld in done:
            continue
        srcs = ours_of[fld]
        supplements = [n for n in srcs if n in alongside]
        if len(srcs) < 2 and not supplements:
            continue
        added.append(f"{fld} = {', '.join(merge(['*'] if supplements else [], fld))}")
    if added:
        lines += ["", "# ---- fields HumanMine does not configure, written by rdfc2im sources"] + added
    return "\n".join(lines) + "\n"


def _priority_fields(model: InterMineModel, root_cls: str, cols: List[dict]) -> List[str]:
    """Class.field for every attribute and reference a table writes.

    Collections are left out: the integration writer unions them from every source.  References
    are resolved by priority exactly like attributes, so an inferred link (no `via`) and the
    reverse of each link count too.
    """
    from .items import _via_from
    out: List[str] = []

    def add(key):
        if key not in out:
            out.append(key)
    for c in cols:
        if c["im_class"] and c["im_field"]:
            add(f"{c['im_class']}.{c['im_field']}")
        via = c.get("via") or (_via_from(model, root_cls, c["im_class"]) if c["im_class"] != root_cls else "")
        if not via:
            continue
        vcls, _, vfld = via.partition(".")
        fd = model.field(vcls, vfld)
        if fd is None:
            continue
        if fd.kind == "reference":
            add(via)
        rd = model.field(fd.type, fd.reverse) if fd.reverse else None
        if rd is not None and rd.kind == "reference":
            add(f"{fd.type}.{fd.reverse}")
    return out


def _is_core(path: str) -> bool:
    """True for the model every mine has regardless of which sources it runs."""
    return path.endswith("core.xml") or path.endswith("genomic_additions.xml")


def _carry_missing_definitions(model: InterMineModel, writers: Dict[str, List[str]],
                               merged: Dict[str, etree._Element],
                               classes_el: etree._Element) -> List[str]:
    """Copy into the additions every class/field our items need that the mine may not have.

    Two ways a definition can go missing, and both have bitten us:

    1. It is absent from the live model entirely - AnatomyTerm, which reaches rdfc2im only
       through curation/extra_allow.txt because HumanMine runs no uberon source.
    2. It is present in the live model but contributed by a source we *replace*.  Only
       core.xml and genomic_additions.xml are guaranteed; everything else arrives in some
       source's _additions.xml.  GOTerm is declared in go, go-annotation, interpro-go,
       uniprot and psi-complexes - and `rdfc2im project` drops `go` and `uniprot` from
       project.xml, taking their contribution with them.  A mine left with only our sources
       has no GOTerm at all, and the load dies in ItemToObjectTranslator with
       `class "GOTerm" does not exist`.

    Over-carrying is safe: declaring the same class in several additions files is the normal
    InterMine pattern (GOTerm is declared five times upstream), and the plugin merges by name.
    """
    live = getattr(model, "live", None)
    carried: List[str] = []

    def declare(cls: str) -> Optional[etree._Element]:
        cd = model.classes.get(cls)
        if cd is None:
            return None
        if cls not in merged:
            attrs = {"name": cls, "is-interface": "true"}
            ext = [e for e in cd.extends if e != "InterMineObject"]
            if ext:
                attrs["extends"] = " ".join(ext)
            merged[cls] = etree.SubElement(classes_el, "class", **attrs)
            carried.append(cls)
            for parent in ext:              # a non-core ancestor must travel too
                pd = model.classes.get(parent)
                if pd is not None and not any(_is_core(p) for p in pd.source_files):
                    declare(parent)
        return merged[cls]

    for key in sorted(writers):
        cls, _, fld = key.rpartition(".")
        if not cls or not fld:
            continue
        cd = model.classes.get(cls)
        fd = model.field(cls, fld)
        if cd is None or fd is None:
            continue                        # `check` reports unknown names separately
        class_is_core = any(_is_core(p) for p in cd.source_files)
        class_in_live = live is None or live.has_class(cls)
        field_is_core = _is_core(fd.source_file)
        field_in_live = class_in_live and live is not None and live.field(cls, fld) is not None
        if class_is_core and field_is_core and (live is None or field_in_live):
            continue                        # guaranteed to be there whatever we replace
        el = declare(cls)
        if el is None:
            continue
        if any(isinstance(f.tag, str) and f.get("name") == fld for f in el):
            continue
        if fd.kind == "attribute":
            etree.SubElement(el, "attribute", name=fld, type=fd.type)
        else:
            sub = etree.SubElement(el, fd.kind, name=fld, **{"referenced-type": fd.type})
            if fd.reverse:
                sub.set("reverse-reference", fd.reverse)
            rd = model.classes.get(fd.type)  # the range must exist too
            if rd is not None and not any(_is_core(p) for p in rd.source_files):
                declare(fd.type)
            if fd.reverse:
                # Gradle's per-bio-source ModelMergerTask (mergeModels) validates THIS project's
                # additions.xml in isolation against the bare stock genomic_model.xml - it never
                # sees sibling bio-sources' own additions files, even ones already declaring the
                # very same field (confirmed live: clinvar's own clinvar_additions.xml already
                # declares this exact Gene.alleles collection, reverse="gene" - and it still
                # doesn't help, because that file plays no part in humanmine-items' own isolated
                # mergeModels run). So "already present elsewhere in the live/merged model" does
                # NOT mean our own additions.xml can omit it - each source's additions file must
                # be self-consistent on its own for ITS OWN mergeModels to pass. That is exactly
                # what this function's own docstring already calls "over-carrying": "declaring
                # the same class in several additions files is the normal InterMine pattern
                # ... and the plugin merges by name" - this is that same pattern, for a field
                # rather than a whole class. Skip only when something in `writers` genuinely
                # writes this field itself (not just holds an incidental empty priority-fields
                # entry - `_priority_fields` can populate a key with `writers.setdefault(f, [])`
                # and no writer, so check for a real entry, not just key presence).
                rev_key = f"{fd.type}.{fd.reverse}"
                if not writers.get(rev_key):
                    rev_el = declare(fd.type)
                    if rev_el is not None and not any(
                            isinstance(f.tag, str) and f.get("name") == fd.reverse for f in rev_el):
                        # Use the real declared kind wherever the model already knows it (core,
                        # an allowed source's additions, or the live model - all equally valid
                        # precedent); fall back to the standard InterMine reference<->collection
                        # pairing only for a field with no precedent anywhere.
                        existing_rev = model.field(fd.type, fd.reverse)
                        rev_kind = (existing_rev.kind if existing_rev is not None
                                    else ("collection" if fd.kind == "reference" else "reference"))
                        etree.SubElement(rev_el, rev_kind, name=fd.reverse,
                                          **{"referenced-type": cls, "reverse-reference": fld})
        if cls not in carried:
            carried.append(f"{cls}.{fld}")
    return carried


# ------------------------------------------------------------------- check
def check_project(out_root: str, project_out: str, model: InterMineModel, type_name: str,
                  humanmine_project: Optional[str], sources_cfg: dict, log=print) -> int:
    hard, soft = [], []
    px = os.path.join(project_out, "project.xml")
    if not os.path.exists(px):
        log("check: no project.xml - run `rdfc2im project` first")
        return 2
    gen_keys = _generated_keys(os.path.join(project_out, f"{type_name}_keys.properties"))
    active = {s.get("name"): s for s in etree.parse(px).getroot().iter("source")}
    for name, s in active.items():
        if s.get("type") != type_name:
            continue
        src = name[len("humanmine-"):]
        for c in read_tsv(os.path.join(out_root, src, "columns.tsv")):
            cls, fld = c["im_class"], c["im_field"]
            if not model.has_class(cls) and not _in_additions(project_out, type_name, cls, None):
                hard.append(f"{name}: class {cls} not in model nor additions")
                continue
            fd = model.field(cls, fld) if model.has_class(cls) else None
            if fd is None and not _in_additions(project_out, type_name, cls, fld):
                hard.append(f"{name}: {cls}.{fld} not in model nor in {type_name}_additions.xml")
            elif fd is not None and fd.kind != "attribute":
                hard.append(f"{name}: {cls}.{fld} is a {fd.kind} - only attributes are columns (links come from `via`)")
            via = c.get("via") or ""
            if via:
                vcls, vf = via.split(".", 1)
                vfd = model.field(vcls, vf)
                if vfd is None:
                    hard.append(f"{name}: via {via} is not a field of {vcls}")
                elif vfd.kind == "attribute":
                    hard.append(f"{name}: via {via} is an attribute, not a reference/collection")
                elif not (model.is_a(cls, vfd.type) or model.is_a(vfd.type, cls)):
                    hard.append(f"{name}: via {via} has range {vfd.type} but the column carries {cls}")
                elif vfd.type != cls and model.is_a(cls, vfd.type):
                    # The range is a strict ancestor of what the column carries, so the type check
                    # cannot tell an intended link (GOTerm into OntologyTerm.parents) from a wrong
                    # one (GOTerm into Protein.keywords, which is how spec D4 went unnoticed).
                    soft.append(f"{name}: via {via} widens - {cls} into a {vfd.type} "
                                f"{vfd.kind}; confirm that is the intended link")
            owner, keys = model.keys_for(cls)
            if not keys and cls in gen_keys and c.get("required") == "yes":
                soft.append(f"{name}: root class {cls} uses a DRAFT key ({gen_keys[cls]}) - review")
            elif not keys and cls not in gen_keys:
                soft.append(f"{name}: class {cls} has no integration key - its items are stored without merging")
        hard.extend(f"{name}: {msg}" for msg in column_collisions(model, out_root, src))
        hard.extend(f"{name}: {msg}" for msg in cross_products(out_root, src))
        items = os.path.join(out_root, src, "items", f"{src}.xml")
        if os.path.exists(items):
            ids, n, bad = set(), 0, 0
            root = etree.parse(items).getroot()
            for it in root.iter("item"):
                ids.add(it.get("id")); n += 1
                cls = it.get("class")
                if not model.has_class(cls) and not _in_additions(project_out, type_name, cls, None):
                    hard.append(f"{name}: items file uses unknown class {cls}")
            for r in root.iter("reference"):
                if r.get("ref_id") not in ids:
                    bad += 1
            if bad:
                hard.append(f"{name}: {bad} dangling ref_ids in {items}")
            soft.append(f"{name}: {n} items in {items}")
        else:
            soft.append(f"{name}: no items file yet (run fetch, tsv, items)")
        names = list(active)
        replaces = sources_cfg.get(src, {}).get("replaces") or []
        for stock in sources_cfg.get(src, {}).get("alongside") or []:
            if stock in replaces:
                hard.append(f"{name}: both replaces and loads alongside '{stock}' - pick one")
            elif stock not in active:
                soft.append(f"{name}: loads alongside '{stock}', which is not in project.xml - "
                            f"nothing to supplement; it loads on its own")
            elif names.index(stock) > names.index(name):
                hard.append(f"{name}: loads before '{stock}', the stock source it supplements - "
                            f"'{stock}' merges by its own keys and would duplicate objects")
        accepted = sources_cfg.get(src, {}).get("accepted_gaps") or {}
        used = set()
        for orig in replaces:
            if orig in active:
                soft.append(f"{name}: original source '{orig}' is still active - duplicate load")
            for cls, missing, whole in replaced_coverage(model, out_root, src, orig):
                # A gap is fatal unless sources.yaml accepts it, with its evidence: the check only
                # sees what the replaced source *declares*, so a false alarm needs a basis too.
                open_, hit = [], []
                for f in missing:
                    key = cls if cls in accepted else f"{cls}.{f}"
                    if key not in accepted:
                        open_.append(f)
                    elif key not in hit:
                        hit.append(key)
                used.update(hit)
                for key in hit:
                    a = accepted[key] or {}
                    soft.append(f"{name}: replaces '{orig}' without {key} - accepted "
                                f"({a.get('basis', 'no basis')}): {a.get('reason', '')}".rstrip(": "))
                if not open_:
                    continue
                if whole and len(open_) == len(missing):
                    hard.append(f"{name}: replaces '{orig}', which declares {cls} ({', '.join(open_)}); "
                                f"this source writes none of it - loading it may delete that data.  "
                                f"Load alongside instead, map it, or accept it in sources.yaml")
                else:
                    hard.append(f"{name}: replaces '{orig}', which declares {cls}.{{{', '.join(open_)}}}; "
                                f"this source never writes those - map them, or accept them in "
                                f"sources.yaml with the reason they are not a loss")
        for key in sorted(set(accepted) - used):
            soft.append(f"{name}: accepted_gaps entry {key} matches no gap - remove it")
    hard, soft = list(dict.fromkeys(hard)), list(dict.fromkeys(soft))
    for h in hard:
        log("HARD  " + h)
    for s in soft:
        log("note  " + s)
    log(f"check: {len(hard)} hard problems, {len(soft)} notes")
    return 1 if hard else 0


def replaced_coverage(model: InterMineModel, out_root: str, src: str, replaced: str):
    """What a replaced stock source declares that this source never writes.

    Returns [(class, missing_fields, whole_class_missing)].  `rdfc2im project` removes every
    source named in `replaces` from project.xml, so anything only it loaded is gone.  That is
    how expressionatlas came to replace atlas-express while loading nothing but DataSet
    metadata, and reactome to replace a source whose whole purpose is gene/pathway membership.

    This is a PROXY: it compares against the fields the replaced source *declares* in its
    _additions.xml, which is not proof it *populates* them.  `check` makes every gap a hard
    problem unless sources.yaml's `accepted_gaps` records why it is not a loss.  "What this
    source writes" counts explicit columns, the link items.py infers when a column has no `via`,
    and the reverse reference it writes for every link.
    """
    from .items import _via_from
    declared: Dict[str, set] = {}
    for path in getattr(model, "files", []):
        norm = path.replace(os.sep, "/")
        if f"/{replaced}/" in norm and norm.endswith(f"{replaced}_additions.xml") and "/test/" not in norm:
            for c in etree.parse(path).getroot().iter("class"):
                declared.setdefault(c.get("name"), set()).update(
                    f.get("name") for f in c if isinstance(f.tag, str))
    if not declared:
        return []

    written: Dict[str, set] = {}

    def add(cls, fld):
        written.setdefault(cls, set()).add(fld)

    def add_link(via):
        cls, _, fld = via.partition(".")
        add(cls, fld)
        fd = model.field(cls, fld)
        if fd is not None and fd.reverse:
            add(fd.type, fd.reverse)

    for table, cols in load_columns(out_root, src).items():
        root = table_root(cols)
        for c in cols:
            if c["im_class"] and c["im_field"]:
                add(c["im_class"], c["im_field"])
            via = c.get("via") or (_via_from(model, root, c["im_class"]) if c["im_class"] != root else "")
            if via:
                add_link(via)

    out = []
    for cls, fields in sorted(declared.items()):
        # A field declared on a class is written by writing it on any subclass: hpo declares
        # OntologyTerm.crossReferences, and both it and we fill it on HPOTerms.
        mine = set()
        for wcls, wfields in written.items():
            if model.has_class(wcls) and model.has_class(cls) and model.is_a(wcls, cls):
                mine |= wfields
        mine |= written.get(cls, set())
        missing = sorted(fields - mine)
        if missing:
            out.append((cls, missing, not mine))
    return out


def column_collisions(model: InterMineModel, out_root: str, src: str) -> List[str]:
    """Columns in one table that write the same field of the same object.

    items.py would keep one value and drop the rest without a word.  translate splits these into
    separate tables, so one reaching columns.tsv means a table was set by hand (or in knowledge)
    onto a collision - a hard problem, because the load would silently lose data.
    """
    from .items import group_key
    out = []
    for table, cols in load_columns(out_root, src).items():
        root = table_root(cols)
        seen: Dict[tuple, List[str]] = {}
        for c in cols:
            if c.get("kind") == "const" or not c.get("im_field"):
                continue
            key = group_key(model, root, c["im_class"], c.get("via") or "") + (c["im_field"],)
            seen.setdefault(key, []).append(c["column"])
        for (cls, via, fld), columns in seen.items():
            if len(columns) > 1:
                out.append(f"table {table}: columns {', '.join(columns)} all write {cls}.{fld}"
                           f"{' via ' + via if via else ''} on one object - only one value would survive")
    return out


def cross_products(out_root: str, src: str) -> List[str]:
    """One untyped predicate bound by several columns of one subject in one table.

    rdf-config lists example values of a single predicate as separate objects; read as separate
    columns they become independent patterns over the same predicate, every variable binds every
    value, and the table returns N^k rows (PubMed's fabio:hasSubjectTerm returned 625 rows for a
    paper with 5 headings).  Columns carrying a `filter` or `value` are discriminated and do not
    count; typed sub-subjects are link rows and never appear here.
    """
    from .mapping import LOADABLE
    from .sssom import read_sssom
    path = os.path.join(out_root, src, "mapping_predicates.sssom.tsv")
    if not os.path.exists(path):
        return []
    _, rows = read_sssom(path)
    groups: Dict[tuple, List[str]] = {}
    for r in rows:
        if r.get("status") not in LOADABLE or r.get("table") in ("", "drop", "*"):
            continue
        if r.get("kind") not in ("literal", "iri") or r.get("predicate") == "-self-":
            continue
        if r.get("filter") or r.get("value"):
            continue
        groups.setdefault((r["table"], r["subject"], r["predicate"]), []).append(r["column"])
    return [f"table {t}: {subj} {pred} is bound by {len(cols)} columns ({', '.join(cols)}) - a cross product "
            f"returning N^{len(cols)} rows; map the predicate once" for (t, subj, pred), cols in groups.items()
            if len(cols) > 1]


def _generated_keys(path: str) -> Dict[str, str]:
    out = {}
    if not os.path.exists(path):
        return out
    for ln in open(path):
        ln = ln.strip()
        if ln and not ln.startswith("#") and ".key" in ln and "=" in ln:
            out[ln.split(".key", 1)[0]] = ln
    return out


def _in_additions(project_out: str, type_name: str, cls: str, fld: Optional[str]) -> bool:
    p = os.path.join(project_out, f"{type_name}_additions.xml")
    if not os.path.exists(p):
        return False
    for c in etree.parse(p).getroot().iter("class"):
        if c.get("name") == cls:
            if fld is None:
                return True
            for f in c:
                if isinstance(f.tag, str) and f.get("name") == fld:
                    return True
    return False
