"""Write out/_docs/STATUS.md and out/_docs/CURATION_GUIDE.md from the current out/ tree."""
from __future__ import annotations
import datetime as dt
import os
from typing import Dict, List

from .mapping import read_tsv, CW_COLUMNS, SUBJ_COLUMNS
from .sssom import read_sssom, to_sssom, SSSOM_COLUMNS
from .project import source_dirs, load_columns
from .rdfconfig import load_config

FINDINGS = """\
## Findings that shape the design (verified against the uploaded code)

1. **The delimited loader (v0.1 route, now dropped) links by *exact declared type* and allows one object
   per class per row.** `DelimitedLoaderTask.setJoinFields` sets a reference only when the referenced
   type's simple name is literally a class in the row (`Synonym.subject` = BioEntity never links to a
   Gene row), links *every* co-occurring pair whether intended or not, cannot express same-class links
   (`OntologyTerm.parents`, `Homologue.gene/homologue`), and de-duplicates objects by concatenated attribute
   values. That is why v0.2 emits **Items XML** instead: attributes, references and collections are explicit,
   links are exactly the mapping's `via`, and the stock `intermine-items-xml-file` loader needs no patch.
2. **`project.xml` runs `ncbi-gene`, not `human-gene`** (contrary to spec section 7.2). The rdf-config gene
   load therefore replaces `ncbi-gene` and follows `NcbiGeneConverter`: for taxon 9606 (no `9606.xref` in
   `ncbigene_config.properties`) `primaryIdentifier` = NCBI Gene id, `secondaryIdentifier` = Ensembl id.
   `HgncConverter` keys the same way (`line[18]`), so ncbigene + hgnc items merge on `Gene.primaryIdentifier`;
   the new `ensembl` source merges through `Gene.key_secondaryidentifier_org`.
3. The two `hgnc` sources differ only in `DATASET_TITLE` and a gradle dependency block: no collision.
4. Term-URI coverage: 102 distinct `term=` URIs in the allowed model. Matches are strong for UniProt (core:),
   bibo/dc (Publication), rdfs:label/skos:prefLabel, and weak for ncbigene/GO/ClinVar, whose vocabularies are
   source-specific - exactly the spec's finding 7.1. The Java-converter evidence in `knowledge.yaml` fills
   most of that gap with `sure` rows.
5. `AnatomyTerm` (Uberon) exists only in `intermine/bio/sources/uberon/resources/uberon_additions.xml`;
   it enters the model via `curation/extra_allow.txt` (`/uberon/`), matching the spec's "activation only".
6. rdf-config's own generator makes every predicate without `?`/`*` **required**; the Python generator
   here makes only `required=yes` rows required, so `optionalize_model` is no longer needed. The
   `humanmine*:` blocks in `sparql.yaml` still let you cross-check with `rdf-config --sparql`.
7. **The HumanMine `/service/model?format=json` endpoint repeats each class's `term` on every attribute**
   (988/988 attribute terms wrong). `rdfc2im linkml` therefore takes field terms from `humanmine_model.xml`.
   The deepakunni3 `intermine-linkml` schemas are a re-encoding of `term=` (core) and FlyMine (not HumanMine);
   no extra mapping knowledge, but a usable validation target once regenerated.
8. `humanmine_model.json` and `.xml` agree on 170/171 classes; the JSON lacks `InterMineObject` only.
"""


def write_docs(out: str, ws: dict, sources_cfg: dict, model) -> None:
    ddir = os.path.join(out, "_docs")
    os.makedirs(ddir, exist_ok=True)
    srcs = source_dirs(out)
    per: Dict[str, dict] = {}
    for s in srcs:
        rows = read_sssom(os.path.join(out, s, "mapping_predicates.sssom.tsv"))[1]
        subj = read_tsv(os.path.join(out, s, "mapping_subjects.tsv"))
        cols = load_columns(out, s)
        rep = {}
        rp = os.path.join(out, s, "report.txt")
        if os.path.exists(rp):
            for ln in open(rp):
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    rep.setdefault(k.strip(), v.strip())
        cfgdir = os.path.join(ws.get("config_root", ""), s)
        per[s] = dict(rows=rows, subjects=subj, cols=cols, report=rep, cfg=load_config(cfgdir) if os.path.isdir(cfgdir) else None,
                      tsv=sorted(os.listdir(os.path.join(out, s, "tsv"))) if os.path.isdir(os.path.join(out, s, "tsv")) else [],
                      items=os.path.exists(os.path.join(out, s, "items", f"{s}.xml")))
    _write_status(ddir, per, ws, sources_cfg, out)
    _write_guide(ddir, per, ws, sources_cfg, out)


def _pruned_subjects(d: dict) -> set:
    """Subjects all of whose rows sit in table=drop (i.e. under a pruned branch)."""
    by = {}
    for r in d["rows"]:
        by.setdefault(r["subject"], []).append(r.get("table") == "drop")
    return {s for s, v in by.items() if v and all(v)}


def _cnt(rows: List[dict], key: str = "status") -> Dict[str, int]:
    c: Dict[str, int] = {}
    for r in rows:
        c[r.get(key, "")] = c.get(r.get(key, ""), 0) + 1
    return c


def _write_status(ddir, per, ws, sources_cfg, out):
    today = dt.date.today().isoformat()
    L = [f"# rdfc2im - status ({today})", "",
         "*Regenerated by `rdfc2im docs`; the prose sections are maintained in `rdfc2im/docs.py`.*", "",
         "## Pipeline state", "",
         "| Step | State |", "|---|---|",
         "| `allow` (scope the model from project.xml) | done - `out/humanmine.allow` |",
         "| model load (core + allowed additions + keys, inheritance-aware terms/keys) | done |",
         "| `translate` (mapping_subjects.tsv / mapping_predicates.sssom.tsv / columns.tsv / queries / sparql.yaml / additions.xml) | done for the sources below |",
         "| 3-way merge of human edits | done - tested (`tests/`) |",
         "| `fetch` (POST queries to RDF Portal) | exercised - every generated query has returned rows from its live endpoint (small LIMITs); no full extract yet |",
         "| `tsv` (SPARQL-TSV -> plain TSV, transforms, constants, filters) | done - tested on synthetic fixtures; runs on `raw/` when present |",
         "| `items` (tsv/*.tsv -> Items XML with references/collections) | done - tested on synthetic fixtures; runs when `tsv/` exists |",
         "| `project` (project.xml, keys, additions, priorities, replaced_sources, links_report) | done - see `out/_mine/` |",
         "| `check` | runs (mapping vs model, `via` ranges, items ref_ids, keys); passes with 0 hard problems |",
         "| `humanmine-items` source | done: stock intermine-items-**large**-xml-file under its own type name + generated keys/additions; no Java |",
         "| `linkml` (HumanMine LinkML schema with corrected field terms) | done - `curation/linkml/humanmine.yaml`; not yet used by `check` |",
         "| InterMine build + load | **done** - a working demonstration mine, 9 sources (`go`/`ncbigene`/`reactome` in full, "
         "`hgnc`/`ensembl`/`uniprot`/`clinvar`/`gwascatalog` limited to a 113-gene panel, `pubmed` to their cited publications), "
         "served with BlueGenes; see LOAD-TRIAL.md, and `report/paper/paper.md` for the write-up (BH26JP BioHackrXiv report) |", "",
         "## Per-source status", "",
         "Row statuses: **sure** = evidence in the uploads (term URI / Java converter / .properties / exact name); "
         "**guess** = my proposal from general knowledge; **todo** = you decide; **human** = your edit; "
         "**drop** = deliberately not loaded; **link** = structural.", "",
         "Row counts are over *active* rows; `pruned` = rows under a dropped/undecided branch (kept in the file, not queried).", "",
         "| source | scope | replaces | subjects sure/guess/todo | rows sure/guess/human/todo/drop | pruned | tables | new fields | fetched | items |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for s, d in per.items():
        sc = sources_cfg.get(s, {})
        active = [r for r in d["rows"] if r.get("table") != "drop"]
        pr = _pruned_subjects(d)
        cs, cr = _cnt([x for x in d["subjects"] if x["subject"] not in pr]), _cnt(active)
        L.append(f"| {s} | {sc.get('scope','?')} | {', '.join(sc.get('replaces') or []) or '-'} | "
                 f"{cs.get('sure',0)}/{cs.get('guess',0)}/{cs.get('todo',0)} | "
                 f"{cr.get('sure',0)}/{cr.get('guess',0)}/{cr.get('human',0)}/{cr.get('todo',0)}/{cr.get('drop',0)} | "
                 f"{len(d['rows']) - len(active)} | "
                 f"{len(d['cols'])} | {d['report'].get('new fields proposed','?')} | "
                 f"{'yes ('+str(len(d['tsv']))+')' if d['tsv'] else 'no'} | {'yes' if d['items'] else 'no'} |")
    L += ["", "### Tables per source (each = one SPARQL query; all of a source's tables feed one items file / one `<source>`)", ""]
    for s, d in per.items():
        for t, cols in d["cols"].items():
            L.append(f"- `{s}/{t}`: " + ", ".join(f"`{c['im_class']}.{c['im_field']}`" for c in cols))
    L += ["", "### Sources in the spec's scope that are not translated", "",
          "- `disgenet`: rdf-config dir is empty (spec D8) - traditional load.",
          "- `orphanet`, `hpo-annotation` (annotations), InterPro / IntAct / BioGRID / SIGNOR: no rdf-config source (spec D3, D6, D8).",
          "- `expressionatlas`: rdf-config source exists but needs the EBI-hierarchy flattening (spec D10); not started.",
          "- `homologene`: translated, but `Homologue` needs the cluster->pairs expansion (D7) before items can be emitted; the same-class link itself is no longer a problem.",
          "- `mesh`: only `Descriptor` is a root; the 13 other MeSH subjects (Concept, Term, Qualifier, SCR_*) are `todo` - most will be `skip`.",
          "", "## Open decisions still with you", "",
          "D1 side-stepped (`typeOfGene` in `curation/extensions_additions.xml`); D2 `inSubset` rows are `todo`; "
          "D4 uniprot `classifiedWith`/GO is `sure` but flagged; D5 disease ids are `todo` in clinvar/hgnc; "
          "D7 homologene `todo`; D12 GWAS year is a `guess` with a `regex` transform; D13 ClinVar coordinates are `link` rows (blank nodes) not mapped; "
          "D14 resolved (2026-09-17) - `Gene.key_secondaryidentifier_org` ambiguity fixed, see LOAD-TRIAL.md.",
          "", FINDINGS, "",
          "## Where this stands (2026-09-18)",
          "",
          "A demonstration HumanMine was built end to end - 9 sources, a 113-gene food/drug-metabolism panel, served live "
          "with BlueGenes in containers - and written up as a BH26JP BioHackrXiv report (`report/` submodule; the finished "
          "text is `report/paper/paper.md`, its Future Work section is the authoritative forward-looking list). Read the "
          "paper first for the why and the overall shape; LOAD-TRIAL.md is the detailed, chronological technical record "
          "(one section per source's first real load, in the order they were done) that the paper's Results and Table 3 "
          "are drawn from.",
          "",
          "**The live demo mine does not persist across sandbox sessions.** Docker containers, the Postgres volumes, the "
          "Solr index and the userprofile-only templates all live inside whatever sandbox built them; a fresh session "
          "starts with none of it and must rebuild from LOAD-TRIAL.md's recipe (the `## Reproducing it` section covers "
          "the original `go`-only trial; the fuller 9-source build's steps - postprocessing, the Solr service, template "
          "SQL, `trial-stage.sh`'s jar patches - are recorded in the later per-source and `## Postprocessing and public "
          "templates` sections of the same file, not yet consolidated into one script).",
          "",
          "## Next steps",
          "",
          "In priority order, matching the paper's Future Work:",
          "",
          "1. **Script the whole build**, from RDF to a running, fully-configured mine (postprocessing, Solr, templates, "
          "the trimmed web configuration) - the paper calls this the most important next step, and LOAD-TRIAL.md has "
          "every step it would need to encode, just not yet as one script.",
          "2. **Re-load `ncbigene` with the D14 fix applied** and confirm live that the 255-gene ambiguity is actually "
          "resolved in a running mine, not just in the regenerated items file - the fix was verified offline but never "
          "re-integrated into the demo mine, to avoid disturbing an already-stable build.",
          "3. **Give `GWASResult` a real integration key** (it has none today, so re-loading GWAS Catalog duplicates "
          "rows - see LOAD-TRIAL.md's widget investigation) and review the DRAFT keys on `Pathway` and `MeshTerm`.",
          "4. **Retrofit `DataSource.url` onto a live mine safely**, or always get it from a fresh build - the code fix "
          "(`sources.yaml` + `items.py`) is durable and works for any new build, but patching it onto data already "
          "loaded needs re-integration, which broke other sources' data-tracking when tried once (see LOAD-TRIAL.md).",
          "5. Extend the demonstration: full-scale loads of the panel-limited sources, more of HumanMine's ~40 "
          "datasets, and the open mapping decisions above (D1-D13).",
          "6. Wire up a `gene_scope_field` resolver (`rdfc2im/scope.py`) for any source added after "
          "hgnc/ensembl/uniprot/clinvar/gwascatalog/ncbigene - only those six have one today.",
          "7. Work through `CURATION_GUIDE.md` for sources not yet touched by a real load (unbound subjects first, "
          "then `todo` rows, then confirm `guess` rows) - the usual pre-load curation cycle for anything beyond the "
          "demonstration panel.",
          "8. **`mesh`'s own fetch is very likely incomplete** - `id.nlm.nih.gov/mesh/sparql` returns exactly 1000 "
          "rows for `mesh/main` and stops, a different failure shape from the Virtuoso/TogoVar caps rdfc2im already "
          "handles (a plain `COUNT(*)` 502s, `LIMIT 1500` times out rather than truncating) - see LOAD-TRIAL.md. "
          "Needs its own investigation before `mesh` is trusted beyond curation-sample scale.",
          ""]
    with open(os.path.join(ddir, "STATUS.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def _tsv_line(r: dict, cols=CW_COLUMNS) -> str:
    return "\t".join(str(r.get(c, "")) for c in cols)


def _write_guide(ddir, per, ws, sources_cfg, out):
    L = ["# rdfc2im - curation guide", "",
         "*Regenerated by `rdfc2im docs`; examples below are taken from the current `out/` files, so they are literally the rows you will see.*", "",
         "## 1. What you edit", "",
         "Two tab-separated files per source, both under `out/<source>/`, both named `mapping_*`:", "",
         "| file | one row per | columns you may change | columns the tool owns |", "|---|---|---|---|",
         f"| `mapping_subjects.tsv` | rdf-config subject (incl. blank nodes) | `im_class`, `role`, `status`, `note` | {', '.join('`'+c+'`' for c in SUBJ_COLUMNS if c not in ('im_class','role','status','note'))} |",
         "| `mapping_predicates.sssom.tsv` | (subject, predicate, column) - SSSOM layout | `object_id` (the InterMine field, `intermine:Class.field`), `comment`, `ext_table`, `ext_status`, `ext_transform`, `ext_filter`, `ext_value`, `ext_required` | `subject_id`, `subject_label`, `predicate_id`, `object_label`, `mapping_justification`, `confidence`, `ext_subject`, `ext_predicate`, `ext_basis`, `ext_via`, `ext_kind`, `ext_multi`, `ext_example` |",
         "",
         "The SSSOM file starts with a commented YAML block (`#mapping_set_id`, `#curie_map`, `#extension_definitions`) - leave it; it declares the `ext_*` columns for SSSOM tools. In the rules below `status` means `ext_status`, `table` means `ext_table`, and `im_class`/`im_field` mean the two halves of `object_id`.",
         "",
         "Everything else in `out/<source>/` (`columns.tsv`, `queries/*.sparql`, `sparql.yaml`, `additions.xml`, `report.txt`, `raw/`, `tsv/`, `items/`) is derived - never edit it; re-run `rdfc2im translate` instead.",
         "",
         "**Your edits survive regeneration.** The tool keeps a hidden snapshot of its own last output "
         "(`.mapping_predicates.base.sssom.tsv`, `.mapping_subjects.base.tsv`). On the next run, any cell that differs between the file "
         "on disk and that snapshot is yours and is kept; cells you did not touch take the fresh value. If you change "
         "`im_class`/`im_field`/`table`/... but leave `status` alone, the row's status becomes `human` automatically.",
         "", "Edit in Emacs with `tsv-mode`/`csv-mode` (`C-c C-a` to align) or any spreadsheet that round-trips tabs. "
         "Keep the header line and the tab count.", "",
         "## 2. Vocabulary", "",
         "`status` (crosswalk rows):", "",
         "| value | meaning | goes into the query/TSV? |", "|---|---|---|",
         "| `sure` | evidence in the uploads: `basis` says which (`term`, `java:<Converter>`, `props:<file>`, `name`) | yes |",
         "| `guess` | my proposal from general knowledge - **please confirm or fix** | yes (unless `--no-guess`) |",
         "| `todo` | unresolved - **you decide** (`note` says what is missing) | no |",
         "| `human` | you set it | yes |",
         "| `drop` | deliberately not loaded | no |",
         "| `link` | structural (object is another subject / blank node); the sub-node's own rows carry the data | no |",
         "",
         "Other editable columns:", "",
         "- `im_class`, `im_field`: the InterMine attribute to write, e.g. `Gene` / `symbol`. Must be an **attribute** "
         "(not a reference/collection) of a concrete class. For a reference or collection you may name it directly "
         "(`Gene.organism`, `GOTerm.parents`): the tool rewrites it to the target class's key attribute (`Organism.taxonId`) "
         "and records the link in `ext_via`; `rdfc2im items` then emits the reference/collection and its reverse.",
         "- `table`: which query/TSV the column comes from. Multi-valued predicates get their own table by default "
         "(`main_gene_synonym`) so they never cross-multiply; all tables of a source are merged by key into one items file. "
         "Move a row by changing this. `*` = every table (constants).",
         "- `required`: `yes` makes the triple pattern mandatory (and the column is added to every table of that root). "
         "Only identifier columns should be `yes`.",
         "- `value`: fix the variable to a constant in the query (`taxonomy:9606`, `\"Homo sapiens\"`). For a `-const-` row it is the constant written to the TSV.",
         "- `transform`: applied when cleaning the fetched TSV; comma-separated chain. `iri_localname` (`.../GO_0008150` -> `GO_0008150`), "
         "`replace:_:\\:` (then -> `GO:0008150`), `prefix:HGNC:`, `strip_prefix:x`, `regex:^(\\d{4})` (first group), `split:,` (explode into several rows), `lower`, `upper`, `int`.",
         "- `filter`: `regex:^R-HSA-`, `nonempty`, `equals:Pubmed`. A value that fails the filter is blanked; if the column is `required=yes` the whole row is dropped.",
         "", "`role` (subjects rows): `root` = this subject starts a table set (its identifier is the row key); `node` = reached "
         "from a root; `skip` = ignore the subject and everything under it.", "",
         "## 3. Workflow", "", "```", "cd <workspace>            # the directory holding rdfc2im.yaml",
         "make translate            # (or: python3 -m rdfc2im translate)   regenerate + merge",
         "$EDITOR out/<source>/mapping_subjects.tsv out/<source>/mapping_predicates.sssom.tsv",
         "make translate            # apply your edits; look at out/<source>/report.txt and queries/",
         "make fetch LIMIT=20       # on a networked machine; then", "make tsv items check docs", "```", "",
         "Curation never blocks the later steps: `sure`/`human` rows and (by default) `guess` rows are used, `todo`/`drop` are not; "
         "`make ... GUESS=--no-guess` excludes guesses.", "",
         "## 4. Tasks, in order", ""]

    # ---- task 1: unbound subjects
    L += ["### 4.1 Bind the subjects that have no InterMine class (`mapping_subjects.tsv`, `status=todo`)", "",
          "Set `im_class` to a concrete model class (or `role` to `skip`). A subject with no class blocks every row under it.", ""]
    n = 0
    for s, d in per.items():
        pr = _pruned_subjects(d)
        todo = [r for r in d["subjects"] if r.get("status") == "todo" and r["subject"] not in pr]
        if not todo:
            continue
        L.append(f"**{s}** - {len(todo)} unbound (subjects under a pruned branch not listed): " + ", ".join(f"`{r['subject']}`" for r in todo[:12]) + (" ..." if len(todo) > 12 else ""))
        if n == 0:
            r = todo[0]
            L += ["", f"Example - `out/{s}/mapping_subjects.tsv`, the line", "", "```", _tsv_line(r, SUBJ_COLUMNS), "```", "",
                  f"Types: `{r.get('rdf_types')}`; reached via `{r.get('path')}`; example `{r.get('example')}`. "
                  "Decide the class and edit the `im_class` and `status` cells:", "", "```",
                  _tsv_line({**r, "im_class": "<ClassName>", "status": "human", "note": "your reason"}, SUBJ_COLUMNS), "```", "",
                  "or, if the subject is not wanted:", "", "```",
                  _tsv_line({**r, "role": "skip", "status": "human"}, SUBJ_COLUMNS), "```", ""]
        n += 1
    if n == 0:
        L.append("(none at the moment)")
    L.append("")

    # ---- task 2: todo rows
    L += ["### 4.2 Resolve `todo` rows (`mapping_predicates.sssom.tsv`)", "",
          "Each `todo` row's `comment` says why it is open. Set `object_id` to `intermine:Class.field` and `ext_status` to `human`, or set `ext_status` to `drop`.", ""]
    first = True
    for s, d in per.items():
        todo = [r for r in d["rows"] if r.get("status") == "todo" and r.get("table") != "drop"]
        if not todo:
            continue
        L.append(f"**{s}** - {len(todo)} active rows (rows under a pruned branch are not listed):")
        L.append("")
        L.append("| subject | predicate | column | example | why open |")
        L.append("|---|---|---|---|---|")
        for r in todo[:25]:
            L.append(f"| {r['subject']} | `{r['predicate']}` | `{r['column']}` | {_md(r['example'])} | {_md(r['note'])} |")
        if len(todo) > 25:
            L.append(f"| ... | | | | {len(todo)-25} more in the file |")
        L.append("")
        if first and todo:
            r = todo[0]
            cfg = d["cfg"]
            L += [f"Example - in `out/{s}/mapping_predicates.sssom.tsv` the line (column order: {', '.join(SSSOM_COLUMNS[:8])}, ext_...)", "", "```", _sssom_line(r, cfg), "```", "",
                  "becomes (mapping it to an attribute - change `object_id` and `ext_status`):", "", "```",
                  _sssom_line({**r, "im_class": "<Class>", "im_field": "<field>", "status": "human", "note": "your reason"}, cfg), "```", "",
                  "or (not loading it - change `ext_status`):", "", "```", _sssom_line({**r, "status": "drop"}, cfg), "```", ""]
            first = False

    # ---- task 3: guesses
    L += ["### 4.3 Confirm or correct `guess` rows", "",
          "These are already in the queries. To accept one, leave it (or set `status=human` to freeze it); to change it, "
          "edit `im_class`/`im_field`; to remove it, set `status=drop`. `--no-guess` builds queries without them if you prefer to review first.", ""]
    for s, d in per.items():
        g = [r for r in d["rows"] if r.get("status") == "guess" and r.get("table") != "drop"]
        if not g:
            continue
        L.append(f"**{s}** - {len(g)} guesses:")
        L.append("")
        L.append("| subject | predicate | column | -> | why I think so |")
        L.append("|---|---|---|---|---|")
        for r in g[:25]:
            L.append(f"| {r['subject']} | `{r['predicate']}` | `{r['column']}` | `{r['im_class']}.{r['im_field']}` | {_md(r['note']) or _md(r['basis'])} |")
        if len(g) > 25:
            L.append(f"| ... | | | | {len(g)-25} more in the file |")
        L.append("")
    subj_g = [(s, r) for s, d in per.items() for r in d["subjects"] if r.get("status") == "guess"]
    if subj_g:
        L += ["Subject bindings that are guesses (`subjects.tsv`): " + ", ".join(f"{s}:`{r['subject']}`->`{r['im_class']}`" for s, r in subj_g), ""]

    # ---- task 4: recipes
    L += ["### 4.4 Recipes", "",
          "**Add a constant column** (e.g. every GO term needs `Ontology.name=GO` for the `OntologyTerm.key_name_ontology` key): "
          "source-wide constants are in `rdfc2im/data/sources.yaml` (`consts:`); for one table add a line to `mapping_predicates.sssom.tsv`:", "", "```",
          _sssom_line(dict(table="main", subject="Class", predicate="-const-", column="const_Ontology_name", im_class="Ontology",
                           im_field="name", status="human", basis="", transform="", filter="", value="GO", required="no", via="",
                           kind="const", multi="no", example="", note="")), "```", "",
          "**Move a column to another table**: change `table`. Give two multi-valued columns the same table only if you want their cross product.", "",
          "**Make a new root** (a second independent entity in the same source): in `subjects.tsv` set `role=root`; its tables are named after it.", "",
          "**Restrict to human** where the source is multi-species: on the organism row set `value` (`taxonomy:9606`) and `required=yes` - see uniprot `core:organism`.", "",
          "**A field that does not exist yet**: map it anyway (`im_class`/`im_field`); the tool adds it to `out/<source>/additions.xml` "
          "(`basis` gains `new-field`) and `rdfc2im project` unions it into `humanmine-delimited_additions.xml`. Fields approved in the spec (section 8) are already in `curation/extensions_additions.xml`.", "",
          "**Cross-check a query with rdf-config**: `out/<source>/sparql.yaml` = the source's pristine `sparql.yaml` + generated `humanmine*:` blocks. "
          "Copy it over `rdf-config/config/<source>/sparql.yaml` in a *copy* of the repo and run `rdf-config --config config/<source> --sparql humanmine`.", "",
          "**Same-class links** (`OntologyTerm.parents`, `Homologue.homologue`): map the row to the collection/reference; `rdfc2im items` creates the target as another item of the root class.", "",
          "**Undo everything on a source**: delete `out/<source>/` and re-run `translate`.", ""]
    with open(os.path.join(ddir, "CURATION_GUIDE.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L))


def _sssom_line(r: dict, cfg=None) -> str:
    d = to_sssom(r, cfg)
    return "\t".join(str(d.get(c, "")) for c in SSSOM_COLUMNS)


def _md(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")[:160]
