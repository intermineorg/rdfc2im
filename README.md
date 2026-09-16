# rdfc2im

Map DBCLS **rdf-config** sources onto the **HumanMine/InterMine** model, generate the SPARQL,
fetch, clean, and emit **InterMine Items XML** plus everything HumanMine needs to load it through the
stock items-xml source.  Implements `SPECIFICATION_15sep26.md` (v0.2: Items XML route, SSSOM mapping).

Requires Python 3.9+ with `pyyaml` and `lxml` (`pip install -r requirements.txt`).

```
in/   <- the uploads, extracted; see "in/ layout" below
curation/            what you maintain by hand (extra allow tokens, approved schema extensions)
rdfc2im/             the package  (python3 -m rdfc2im <cmd>;  no dependencies beyond lxml + pyyaml)
rdfc2im/data/        knowledge.yaml (my mappings, reviewable) + sources.yaml (per-source settings)
out/<source>/        mapping_subjects.tsv, mapping_predicates.sssom.tsv  <- YOU EDIT THESE; all else derived
out/<source>/items/  <source>.xml - the Items XML HumanMine loads          (rdfc2im items)
out/_mine/           project.xml, keys, additions, priorities, links_report (rdfc2im project)
out/_docs/           STATUS.md, CURATION_GUIDE.md                           (rdfc2im docs)
curation/linkml/     humanmine.yaml - LinkML schema of HumanMine            (rdfc2im linkml)
humanmine-items/     the loader source: stock intermine-items-xml-file + generated keys/additions (no Java)
tests/               unit tests on synthetic fixtures (make test; runs without pytest too)
```

See `USAGE.md` for the command reference and workflow.

## in/ layout

`rdfc2im.yaml` expects (edit it if your extraction differs):

```
in/config/<source>/model.yaml ...        rdf-config configs      (config_root)
in/intermine/bio/model/core.xml ...      intermine bio sources   (model_dirs[0])
in/humanmine-bio-sources/<src>/...       humanmine bio sources   (model_dirs[1])
in/humanmine_project.xml                 (project_xml)
in/humanmine_model.xml                   (live_model)
in/humanmine_model.json                  (model_json, only for `rdfc2im linkml`)
```

## Quick start

```
make all            # allow -> translate (all good/structural sources) -> tsv -> items -> project -> check -> docs
$EDITOR out/ncbigene/mapping_predicates.sssom.tsv   # fix a mapping (see out/_docs/CURATION_GUIDE.md)
make translate SRC=ncbigene                 # re-run one source; your edits survive
make fetch LIMIT=20                         # needs network; DRY_RUN=1 / FORCE=1 / SLEEP=2 also honoured
make tsv items check docs                   # items/<source>.xml appears once tsv/ exists
make fork-sync                              # copy generated keys/additions into humanmine-items/
```

`make all` is safe on a curated `out/` - it merges; only `rm -rf out` throws edits away.

`rdfc2im.yaml` holds the paths and defaults (limit, include_guess, src_data_dir, ...).
Every Makefile target is a one-line `python3 -m rdfc2im <cmd> ...` you can run directly.

## How the translation decides

For each rdf-config subject: source-specific knowledge > class `term=` URI matching a subject type
> generic knowledge by type > subject name equals a model class > `todo`.

For each (subject, predicate, column): source-specific knowledge (`java:<Converter>` evidence)
> `term=` URI on the bound class or an ancestor > generic knowledge > `term=` declared on an unrelated
class that has the same field name (guess) > exact field-name match > `todo`.

Rows carry a status (`sure` / `guess` / `todo` / `human` / `drop` / `link`) and a basis that says
where the mapping came from, so you can see at a glance what is evidence and what is my proposal.
`knowledge.yaml` is the complete list of what I proposed; edit it or override in the mapping files.
The predicate mapping is an SSSOM file (`mapping_predicates.sssom.tsv`): standard columns
(`subject_id` = predicate IRI, `object_id` = `intermine:Class.field`, `predicate_id`, `mapping_justification`,
`confidence`, `comment`) plus rdfc2im's operational columns as declared `ext_*` extension slots.
`ext_status` is the switch: `sure`/`human`/`guess` load (`--no-guess` drops the guesses), `todo`/`drop` do not.

## Design choices worth knowing

- **One table (= one query) per multi-valued branch.** Multi-valued predicates that create objects of
  another class (synonyms, xrefs, citations) get their own table, so nothing cross-multiplies and each
  table is `root key + satellite`. `rdfc2im items` merges all of a source's tables by key into one
  items file, emitting references/collections from the mapping's `via` (and their reverse references),
  so same-class links such as `OntologyTerm.parents` are ordinary rows.
- **Only identifier rows are required in SPARQL**; everything else is `OPTIONAL` (replaces the spec's
  `optionalize_model`). `value` puts a `VALUES` clause on a variable (`taxonomy:9606`).
- **The subject IRI is a column** (`-self-` rows) - UniProt accessions live in the IRI.
- **Pruning**: everything under a link/blank-node row whose status is `drop` or `todo`, or a subject
  with `role=skip`, is kept in the TSV but assigned `table=drop` - so the 70 UniProt annotation
  subtrees do not clutter the queries until someone wants them.
- **3-way merge** keeps human edits across regenerations (see CURATION_GUIDE.md).
- **Why Items XML and not the delimited loader** (v0.1): see STATUS.md finding 1.

See `out/_docs/STATUS.md` for the state of every source and the loader findings that shaped this.
