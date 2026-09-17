# Load trial - `go`, end to end, 2026-09-16

The first time an rdfc2im items file has been loaded into a real InterMine and browsed in
BlueGenes. It found five problems in rdfc2im that nothing short of a load would have found; all
five are fixed in the commits that reference this file. Recorded here so the next person can
repeat it.

## What was loaded

A full `go` extract, no `LIMIT`: 48,351 `oboinowl:id` rows, 99,892 synonym rows, 92,208
`rdfs:subClassOf` rows, fetched from `https://rdfportal.org/primary/sparql` in 5-10 pages of
10,000 per table (~65 s total). `rdfc2im items` turned that into a 50 MB items file in 3.5 s.

In the production database afterwards:

| table | rows | |
|---|---|---|
| `goterm` | 48,340 | the 48,351 ids minus 11 OWL properties (finding 5); no stubs |
| `ontologytermsynonym` | 76,859 | |
| `ontologytermparents` | 65,768 | exactly the OBO-namespace superclass rows; the other 26,440 raw rows were blank nodes, `owl:Thing` or absent |

Spot-checked in both UIs: `GO:0000016` "lactase activity", namespace `molecular_function`,
parent `GO:0004553` "hydrolase activity, hydrolyzing O-glycosyl compounds", synonym
"lactose galactohydrolase activity" (exact). All correct.

## What it found in rdfc2im

1. Keys and additions were not packaged (`src/main/resources/` vs `resources/`). Build
   reports SUCCESS, additions silently unmerged, load dies in `DataLoaderHelper`.
2. `<source>` needs `version="4.3.0"`, or the mine resolves `bioVersion` and never finds the jar.
3. The gradle project must be `bio-source-humanmine-items`, not `humanmine-items`.
4. **Classes contributed by a replaced source disappear with it.** `GOTerm` is not core; it is
   declared in `go`, `go-annotation`, `interpro-go`, `uniprot` and `psi-complexes`. We replace
   `go` and `uniprot`. The load failed with `class "GOTerm" does not exist`. Now carried into
   `humanmine-items_additions.xml`, along with the other ten non-core classes.
5. **OWL properties loaded as terms.** OBO ontologies give `oboinowl:id` to object and
   annotation properties as well as classes, so 11 GOTerms had identifiers like `part_of`,
   `occurs_in` and `term_tracker_item`. Invisible until BlueGenes sorted `ends_during` to the top
   of the class. Now filtered to the `PREFIX:digits` shape, which covers GO, HP, MP and UBERON.

## The stack

Three containers, one compose file, everything published to `127.0.0.1` only:

| service | image | port | |
|---|---|---|---|
| `postgres` | `postgres:14` | 15432 | data in the named volume `rdfc2im-pgdata` |
| `mine` | `tomcat:8.5-jdk8-temurin` | 8090 | the exploded webapp from `trial/artifacts/humanmine` |
| `bluegenes` | `eclipse-temurin:17-jre` | 5000 | BlueGenes 1.4.5 and its 178 jars from `trial/artifacts/bluegenes-lib` |

```
make up          # idempotent; waits for postgres to be healthy before the mine
make status
make logs SVC=mine
make restart SVC=mine
make down        # stops and removes containers, KEEPS the database volume
make trial-destroy   # also removes the volume - the only target that loses data
```

Compose needs the rootless podman API socket, once per user:
`systemctl --user enable --now podman.socket`. `podman compose` then drives the
`docker-compose` CLI plugin against it.

### Connecting

```
ssh -L 5000:localhost:5000 -L 8090:localhost:8090 -L 15432:localhost:15432 you@host
```

BlueGenes at `http://localhost:5000`; the classic webapp at `http://localhost:8090/humanmine`.
Forward **8090 as 8090**: BlueGenes' frontend runs in your browser and calls
`http://localhost:8090/humanmine` itself, so that URL has to resolve on your machine.

### Things about the stack that are not obvious

- **The webapp must listen on 8090 inside its container, not Tomcat's default 8080.** InterMine
  validates every query against `query.xsd`, which it fetches *from itself* at
  `webapp.baseurl` (`http://localhost:8090`, baked into the war). With the usual `8090:8080`
  mapping that self-fetch is refused and every query-service call returns 500 while the UI and
  `/service/model` look fine. `trial/tomcat/server.xml` moves the connector; the published port
  is deliberately not a variable, because changing it alone reintroduces the bug.
- **The webapp reads the database once, at startup.** Start the mine before the data exists and
  Struts marks itself unavailable (bare 503) and stays that way. After loading or restoring,
  `make restart SVC=mine`.
- **Two database addresses, on purpose.** The host-side gradle tasks (`builddb`, `integrate`)
  reach Postgres at `localhost:15432` through `~/.intermine/humanmine.properties`; the webapp
  reaches it as `postgres:5432` on the compose network. The war bakes the former in, so
  `make trial-stage` patches `WEB-INF/classes/intermine.properties` and `WEB-INF/web.properties`
  in the exploded copy.
- **BlueGenes' Clojars jar is thin.** It holds the AOT-compiled BlueGenes classes but not the
  Clojure runtime (`NoClassDefFoundError: clojure.lang.Var`). Resolving its 46 declared
  dependencies gives 178 jars; no Leiningen or ClojureScript build is needed.
- **BlueGenes always binds `0.0.0.0`** - `run-jetty` is called without `:host`. In a container
  that is confined to the container's network namespace, and podman publishes it to loopback
  only. On the bare host it would be LAN-visible, which is one reason it runs in a container.
- **BlueGenes has two service roots.** `BLUEGENES_DEFAULT_SERVICE_ROOT` is what the browser
  calls (`localhost:8090`, via the tunnel); `BLUEGENES_BACKEND_SERVICE_ROOT` is what the
  BlueGenes server calls (`mine:8090`, container to container).

## Reducing a mine

HumanMine's webapp configuration describes the whole mine. Run any subset of its sources and
four files name classes that no longer exist. Each failure is fatal, and each looks different:

| file | what breaks | symptom |
|---|---|---|
| `dbmodel/resources/genomic_priorities.properties` | an entry whose class is absent (`ProteinDomain`, `Disease`) | the **load** fails: `PriorityConfig` - *Class 'ProteinDomain' not found in model* |
| `webapp/.../WEB-INF/webconfig-model.xml`, `<class>` | `className` absent, or a `fieldExpr` that does not resolve | UI shows *The webconfig-model.xml file is not valid* |
| `webapp/.../WEB-INF/webconfig-model.xml`, `<widgets>` | any `views`, `enrich`, `enrichIdentifier`, `constraints` or `pathStrings` path that does not resolve from the widget's `startClass`/`typeClass` | same message, with the widget ids and paths listed |
| `dbmodel/resources/objectstoresummary.config.properties` | an `X.autocomplete` entry whose class is absent | `AutoCompleter` throws during init, the Struts servlet is marked unavailable: a bare 503 with **nothing** in `intermine.log` - it is only in Tomcat's `localhost.<date>.log` |

For the GO-only mine that meant: priorities lost `ProteinDomain` and `Disease`; webconfig lost 12
classes (`Complex`, `Component`, `GOAnnotation`, `GOEvidenceCode`, `Homologue`, `Interaction`,
`InteractionDetail`, `InteractionTerm`, `ProteinAtlasExpression`, `ProteinDomain`, `Source`,
`UniProtFeature`), 3 field expressions, and 7 widgets (`go_enrichment_for_gene`,
`prot_dom_enrichment_for_gene`, `prot_dom_enrichment_for_protein`, `snp_gwas_study_enrichment`,
`snp_publication_enrichment`, `pathway_enrichment`, `interactions`); autocomplete lost
`ProteinDomain` and `InteractionTerm`.

Validate the widgets by resolving paths, not by checking class names - every one of those seven
widgets is on a class that exists (`Gene`, `Protein`, `SNP`). It is the path *through* the class
that is missing.

The rule for all four is the same: check against the **merged** model the mine actually built
(`dbmodel/build/resources/main/genomic_model.xml`), not the live HumanMine model.

## Reproducing it

The mine build this trial used lived in a session scratch directory that no longer exists.
`trial/artifacts/` (the staged webapp and BlueGenes jars) and the `rdfc2im-pgdata` volume
survive, so `make up` still works; re-staging needs the mine rebuilt somewhere durable:

```
make fetch SRC=go LIMIT=0 FORCE=1     # ~65 s
make tsv items project
make fork-sync
# in humanmine-bio-sources/, building the one source in isolation (see below)
./gradlew :bio-source-humanmine-items:install
# in a humanmine checkout whose project.xml lists only humanmine-go (version="4.3.0"),
# with the four files above reduced
./gradlew :dbmodel:builddb
./gradlew :dbmodel:integrate -Psource=humanmine-go
./gradlew :webapp:war
TRIAL_HOME=<that build dir> make trial-stage
make up && make restart SVC=mine
```

JDK 8 for all the gradle steps. `~/.intermine/humanmine.properties` needs `db.production`,
`db.common-tgt-items` and `db.userprofile-production` at `localhost:15432`, and the mine's
`default.intermine.properties.file` must be InterMine's
`intermine/resources/src/main/resources/default.intermine.production.properties` - it defines
`integration.production`. The copy under `bio/model` defines `integration.bio-test` instead and
gets you *Failed to instantiate IntegrationWriter class*.

`humanmine-bio-sources` does not build as a whole: `bio-source-humanmine-static` depends on the
JCenter/Bintray gradle plugins, and Gradle configures every sibling, so one dead sibling fails
the build. A directory holding just `humanmine-items` and the root `build.gradle` works.

## Not covered

Only `go` was loaded, into an otherwise empty mine. Nothing here exercises integration *keys*
doing real work - merging a Gene arriving from both `humanmine-ncbigene` and `humanmine-hgnc` -
or the priorities file resolving a genuine conflict. That needs two sources loaded in sequence
and is the obvious next trial.

## ncbigene: three tables exceed Virtuoso's 200000-row page cap

The next trial, fetching `ncbigene` in full. Virtuoso refuses any `ORDER BY` + `LIMIT`/`OFFSET`
request once offset+limit exceeds 200000 ("SR353: Sorted TOP clause specifies more then N rows to
sort. Only 200000 are allowed"). `main_alternative`, `main_dblink` and `main_gene_synonym` are all
per-gene multi-valued and each cross that line; `main` (193289 genes) and `main_db_xref` (193967
rows) stay under it and fetch complete. Before the fix, the page that crossed 200000 simply failed
and was swallowed as an ordinary "partial" page failure - indistinguishable from a genuinely short
final page - so the truncated file was written and treated as complete data by everything
downstream. Confirmed on `main_gene_synonym`: server `COUNT(*)` is 239974, 39974 rows (16.7%)
silently missing. `_fetch_paged` now recognises the cap before making the doomed request and
reports `"partial"` distinctly from `"fetched"`.

The obvious general fix - page via `FILTER(?var > last-seen-value)` instead of a growing OFFSET,
which sidesteps the cap entirely since every request's own offset stays 0 - was built, unit
tested, and then found unsafe against the real endpoint: RDF Portal's Virtuoso evaluates `>` on
plain-literal values unreliably. `FILTER(?id > "1")` on ncbigene's own data excludes `"2"` through
`"9"` while admitting `"10"`, `"100"`, ... - reproduced directly against the live endpoint, not
inferred. Shipping it would have silently dropped 28487 of 193288 genes (14.7%) from a table that
fetched fully under the old method - a worse, harder-to-notice bug than the one it fixed. Reverted;
not committed.

**Still open**: fully extracting a table past the cap needs a comparison-free strategy - e.g.
fetching the full (sub-200000) set of `main`'s gene ids first, then batching them through
`VALUES ?id { ... }` for the wide tables, since RDF Portal does evaluate `VALUES`-equality
correctly (confirmed: `FILTER(?id = "9997")` finds the right rows). Not implemented - it needs its
own live-verification pass with the same care this finding took, not a same-session follow-on to
an already-reverted fix. `ncbigene` is not yet loaded into the trial mine: three of its five raw
tables are now honestly incomplete rather than silently wrong, and loading honestly-incomplete
gene identifier/synonym/xref data as if it were the real load is a decision for a human, not a
default to fall into.
