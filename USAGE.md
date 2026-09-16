# rdfc2im - usage

*Maintained alongside the code; regenerated docs are in `out/_docs/`. Version: 0.2.5 (Items XML route).*

## Setup

```
git clone <this repo> && cd rdfc2im
pip install -r requirements.txt        # pyyaml, lxml (a venv is fine)
make inputs                            # clone/refresh upstream into in/  (~150 MB, gitignored)
```

`make inputs` (= `tools/make-inputs.sh`) assembles this layout; place the files yourself instead
if you already have the checkouts (edit `rdfc2im.yaml` if your paths differ):

```
in/config/<source>/model.yaml ...        rdf-config configs      config_root
in/intermine/bio/model/core.xml ...      intermine bio sources   model_dirs[0]
in/humanmine-bio-sources/<src>/...       humanmine bio sources   model_dirs[1]
in/humanmine_project.xml                 project_xml
in/humanmine_model.xml                   live_model
in/humanmine_model.json                  model_json (only for `rdfc2im linkml`)
```

`rdfc2im` aborts with a list of missing paths if any of these is absent.

## Commands

| command | does | reads | writes (under `out/`) |
|---|---|---|---|
| `make allow` | scope the model from project.xml (+ `curation/extra_allow.txt`) | project.xml | `humanmine.allow` |
| `make translate` | build/merge the mappings, generate queries | `in/`, `knowledge.yaml`, `sources.yaml`, your edits | `<src>/mapping_*.tsv`, `columns.tsv`, `queries/*.sparql`, `sparql.yaml`, `additions.xml`, `report.txt` |
| `make fetch` | POST each query to its endpoint (needs network) | `queries/` | `<src>/raw/*.tsv` |
| `make tsv` | clean SPARQL-TSV, apply transforms/filters/constants | `raw/`, `columns.tsv` | `<src>/tsv/*.tsv` |
| `make items` | merge a source's tables by key into Items XML | `tsv/`, `columns.tsv` | `<src>/items/<src>.xml` |
| `make project` | mine configuration | all `columns.tsv`, `curation/extensions_additions.xml` | `_mine/project.xml`, `humanmine-items_keys.properties`, `humanmine-items_additions.xml`, `genomic_priorities.properties`, `replaced_sources.xml`, `links_report.txt` |
| `make check` | validate mappings vs model, `via` ranges, items ref_ids, keys, duplicate loads | `_mine/`, `columns.tsv`, `items/` | (report to stdout; exit 1 on hard problems) |
| `make docs` | status + curation guide | everything above | `_docs/STATUS.md`, `_docs/CURATION_GUIDE.md` |
| `make all` | one process running allow → translate → tsv → items → project → check → docs; add `FETCH=1` to include fetch | | |
| `make linkml` | HumanMine LinkML schema with corrected field terms | `model_json`, `live_model` | `curation/linkml/humanmine.yaml` |
| `make fork-sync` | copy generated keys/additions into `humanmine-items/` | `_mine/` | `humanmine-items/resources/` |
| `make test` | unit tests (pytest if present, else `tests/run.py`) | | |

Variables: `SRC=ncbigene` (repeatable) restricts to sources; `LIMIT=0` removes the SPARQL LIMIT (default 20) - it applies to `translate` (written into `queries/`) and to `fetch` (overrides at send time, so `make fetch LIMIT=0 FORCE=1` does a full extract);
`GUESS=--no-guess` excludes `guess` rows. Every target is `python3 -m rdfc2im <cmd> [flags]`; `python3 -m rdfc2im <cmd> -h` lists flags
(`--source/-s`, `--limit`, `--no-guess`, `--types root|union|all`, `--no-from`, `--dry-run`, `--force`, `--sleep`, `--timeout`).
`ITERATE=n` pages every query (`LIMIT n OFFSET k`, `ORDER BY` the root variable) until a short page comes back - default 5000, `0` = one request per query; `SLEEP=s` waits between requests (default 1). Fetch also honours the environment variables `DRY_RUN=1`, `FORCE=1`, `SLEEP`, `ITERATE`. Typical full extract: `make fetch LIMIT=0 FORCE=1` (add `SRC=<source>` to do one at a time). A failing endpoint is logged and skipped (POST then GET; SPARQL-JSON, then TSV, then CSV are tried - JSON first because it is dialect-free; all are converted to W3C TSV in raw/); a query that returns no rows is flagged `<-- no rows`. Re-fetch one source with `make fetch SRC=<source> FORCE=1`.

## The workflow

```
make all                       # first pass: everything auto-mapped
$EDITOR out/<source>/mapping_subjects.tsv out/<source>/mapping_predicates.sssom.tsv
make translate SRC=<source>    # apply edits; read out/<source>/report.txt and queries/*.sparql
make fetch LIMIT=20            # networked machine; inspect out/<source>/raw/*.tsv (LIMIT=0 FORCE=1 for the full extract)
make tsv items check docs
make fork-sync                 # then install humanmine-items/ and use out/_mine/project.xml
```

Curation never blocks later steps: rows with `ext_status` `sure`, `human` or `guess` are used, `todo` and `drop` are not.
Running `make all` on a curated `out/` merges; only `rm -rf out` (or `rm -rf out/<source>`) throws edits away.
`make clean` removes only `out/_mine` and `out/_docs`. Keep `out/*/mapping_*.tsv` and the hidden `.mapping_*.base.*`
snapshots under version control - they are the state.

## What you edit

- `out/<source>/mapping_subjects.tsv` - one row per rdf-config subject: `im_class` (InterMine class), `role` (`root` | `node` | `skip`), `status`, `note`.
- `out/<source>/mapping_predicates.sssom.tsv` - one row per (subject, predicate, column), SSSOM layout. Editable:
  `object_id` (`intermine:Class.field`), `comment`, `ext_table`, `ext_status`, `ext_transform`, `ext_filter`, `ext_value`, `ext_required`.
  Leave the commented YAML header alone.
- `curation/extra_allow.txt` - extra source dirs whose additions/keys should enter the model (e.g. `/uberon/`).
- `curation/extensions_additions.xml` - approved new fields/classes (spec section 8 + `MeshTerm.identifier`).
- `rdfc2im/data/knowledge.yaml` - my subject/predicate proposals; edit to change defaults for every run.
- `rdfc2im/data/sources.yaml` - per-source `replaces`, `consts`, `roots`, DataSet/DataSource names, scope.

Statuses: `sure` (evidence in the uploads; `ext_basis` says which) · `guess` (my proposal) · `todo` (you decide) ·
`human` (your edit) · `drop` (not loaded) · `link` (structural). `out/_docs/CURATION_GUIDE.md` lists the open items
per source with literal example lines.

## Delivering to HumanMine

1. `make fork-sync`; copy `humanmine-items/` into `humanmine-bio-sources/`, register it as `':bio-source-humanmine-items'` with `projectDir` `./humanmine-items` in `settings.gradle` (the `bio-source-` prefix is how the mine resolves the artifact - see `humanmine-items/README.md`), then `./gradlew :bio-source-humanmine-items:install`.
2. Use `out/_mine/project.xml` (HumanMine's project.xml with `humanmine-<source>` sources inserted; superseded originals are in `replaced_sources.xml`).
3. Put each `out/<source>/items/<source>.xml` at the `src.data.file` path (`src_data_dir` in `rdfc2im.yaml`).
4. Review `out/_mine/humanmine-items_keys.properties` (DRAFT keys are marked) and `genomic_priorities.properties` before building.

## Not yet exercised

`fetch` against the real endpoints and an actual InterMine load of an items file; `linkml-validate` is not wired into `check`.
