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

**Resolved in a follow-up pass** (`ef5e40b`): a cheap `COUNT(*)` pre-check catches a table that
will cross the cap before paging starts, and switches to `fetch_values_batched` over the query's
own required (non-OPTIONAL) key domain - `VALUES`-equality is the one comparison RDF Portal's
Virtuoso evaluates reliably. Verified against the live endpoint: all three tables now match
independently-confirmed server `COUNT(*)` exactly (`main_alternative` 265514, `main_dblink`
247254, `main_gene_synonym` 239974), zero duplicates, full 193288-gene coverage. `ncbigene` is
loaded into the trial mine.

## hgnc: the first real merge test, and three more bugs a load - not a read - would find

The next trial: loading `hgnc` alongside the already-loaded `ncbigene`, to exercise integration
keys merging a `Gene` arriving from two different sources for the first time. All fetched cleanly
(16 tables, none near the 200000 cap). `tsv`/`items`/`project`/`check` all passed with 0 hard
problems. The load itself found three more real bugs, none of them visible from static review.

1. **`have.large.file.xml.tgt`'s retrieve path cannot handle non-ASCII bytes.** A single Greek
   letter ("alpha") in one HGNC `alt_label` synonym value failed the whole retrieve with
   `PSQLException: invalid byte sequence for encoding UTF8: 0x00` - reproduced on a minimal 5-item
   file, confirmed the character is the trigger (an ASCII substitute proceeds cleanly), confirmed
   `have.file.xml.tgt` (a different Java code path, no COPY BINARY) does not have this bug on the
   same data. Fixed (`8185114`) by transliterating attribute values to their closest ASCII on the
   way into the items file - Greek letters spell out by name, "smart" punctuation maps to ASCII,
   everything else goes through NFKD. This is a workaround for a confirmed bug in this InterMine
   version's own loader, not an rdfc2im correctness fix.
2. **`have.file.xml.tgt` needs far more Postgres connections than `have.large` at real volumes.**
   Loading hgnc's 44413 Gene items exhausted the postgres:14 image's default `max_connections=100`
   - reproducible on a fresh restart, not a leak from a prior attempt. This is why (1) transliterates
   rather than switching the type-wide loader setting: `have.file.xml.tgt` was still needed as a
   diagnostic to isolate bug (1) from bug (3) below, but is markedly slower and more
   connection-hungry, not a viable default. Raised to 300 (`41ee6a8`) as a real, if partial,
   mitigation for whichever route needs it.
3. **`out/_mine/genomic_priorities.properties` must be re-synced into the mine checkout after
   *every* new source, not just once.** The checkout still had the stock `ncbi-gene, hgnc` entries
   from before either humanmine-* source existed; loading hgnc hit `Conflicting values for field
   Gene.name ... needs configuring in genomic_priorities.properties` even though the freshly
   generated file already had the right `humanmine-hgnc, humanmine-ncbigene` entry - it just was
   not deployed. No rdfc2im code changed; this is a build-process step to remember, same class of
   thing as `make fork-sync`.

**A fourth, systemic finding - not fixed, needs a decision**: 255 distinct Ensembl gene ids in
the already-loaded `ncbigene` data are each shared by two or more different NCBI Entrez ids (e.g.
`ENSG00000196951` <- both `100129858` "SCOC-AS1" and `124900785` "LOC124900785" - a real,
overlapping-locus/readthrough-annotation ambiguity in NCBI's own cross-reference data, not an
rdfc2im mapping error). `Gene.key_secondaryidentifier_org` (secondaryIdentifier + organism) is
one of Gene's stock integration keys, and is not actually unique against this real data. Loading
hgnc past it requires `dataLoader.allowMultipleErrors=true`, which does let the load proceed but
was, at real scale (44413 items, 255 known conflicts), still running with no visible progress
after 15+ minutes - not confirmed to ever finish in reasonable time, as opposed to failing fast
(15s) without it. This is not an rdfc2im bug - rdfc2im faithfully reproduced ncbigene's real data
- and not something to resolve by editing already-verified-correct data. Whether
`key_secondaryidentifier_org` should stay an active Gene merge key given real NCBI/Ensembl data,
or what else should give, is a data-modelling decision for a human.

**Resolved (D14, 2026-09-17)**, in a separate worktree so it could be investigated without
touching the running mine mid-load. Checked every one of the 255 groups (531 NCBI records), not
just the sample above, against `Gene.typeOfGene`: all are real overlapping-transcript/antisense
pairs, but "prefer the protein-coding claimant" - the obvious rule from the sample - only resolves
49 of the 255 (19%); the other 206 are ncRNA-vs-ncRNA/pseudogene pairs or have 2+ protein-coding
claimants, with no principled winner. `rdfc2im/items.py` gained `resolve_key_ambiguity()`, driven
by a `key_ambiguity` entry in `sources.yaml`'s `ncbigene` config: after a source's items are built,
group a field's values by (value, organism); where a value is shared, keep it only where the
configured disambiguator picks out exactly one holder, otherwise drop it from every holder in the
group - matching `clean_table`'s existing "can't resolve cleanly -> drop, don't corrupt" rule, not
a new one. Verified against the full 193,289-gene extract: `Gene.secondaryIdentifier` unique
across every item (zero duplicates), 49 of 531 affected records keep their Ensembl cross-reference
and 482 lose it, no unambiguous gene changed, `check` stays at 0 hard problems. Regression test:
`test_key_ambiguity_keeps_only_the_disambiguated_holder`.

**Not done**: the fix was verified against the regenerated items file, but never re-integrated
into the running demo mine (still loaded from before the fix) - re-loading `ncbigene` to confirm
this live, without disturbing the other 8 already-loaded sources, is on the Next Steps list in
`STATUS.md`.

**The actual thing this trial set out to verify - full-source-scale conflicts aside - is
confirmed working.** A targeted 73-item subset (BRCA1 and TP53's Gene items plus their Organism/
DataSet/DataSource/Synonym/CrossReference/Publication satellites, built by BFS from the two Gene
items and *not* expanding `Publication.entities`-style back-collections, which fan out to
unrelated genes and were the source of an earlier 46047-item over-inclusion) loaded cleanly with
neither famous gene among the 255 ambiguous ids. Verified via the REST API: BRCA1 (primaryIdentifier
672) and TP53 (7157) are each exactly one Gene row carrying `primaryIdentifier`/`secondaryIdentifier`/
`chromosome` from ncbigene alongside `symbol`/`cytoLocation` from hgnc, with synonyms merged from
both sources - including HGNC's own id ("HGNC:1100") loaded as a Synonym. No duplicate rows.
`hgnc` is not loaded into the trial mine at full scale; the verification subset was not left
loaded either (project.xml points back at the full `hgnc.xml`, currently absent from the database).

## gwascatalog: four transport/data bugs, all found only by an actual load

GWAS Catalog is served through TogoVar (`togovar.org`), a completely different SPARQL endpoint
from RDF Portal - every one of these was specific to that endpoint's behaviour, not a repeat of
an earlier finding:

1. **A redirected POST's GET fallback breaks once the query is long.** `togovar.org/sparql`
   302-redirects every request to `grch38.togovar.org/sparql`; `_NoRedirectPost` converted the
   retry to a GET with the query embedded in the URL (`fetch.py:321`, pre-existing, written for
   whatever endpoint motivated it originally). Fine for a short query, but the gene-panel filter
   (113 OR'd conditions) makes a GET URL long enough that `grch38.togovar.org` 414s it. Fixed by
   retrying as POST at the redirect target instead, once the GET URL would exceed a safe length
   (`bd3d198`).
2. **That POST retry carried the wrong Host header.** `req.header_items()` (used to copy headers
   onto the retried request) also returns *unredirected* headers, including `Host`, still pinned
   to `togovar.org`. Cloudflare used the mismatched `Host` to keep re-issuing the same redirect
   forever, until urllib's own repeat-visit guard gave up with an "infinite loop" error. Fixed by
   using `req.headers.items()` instead, same as the base class's own `redirect_request` (`1794061`).
3. **Gene-scope filtering assumed a literal that TogoVar returns as an IRI.** `snp_gene_ids` is
   `http://identifiers.org/ensembl/ENSG...` on the live endpoint, not a bare `"ENSG..."` literal -
   `resolve_ensembl_symbols`'s docstring claim that it matched ensembl's own literal scheme was
   wrong for this source. `STR(?x) = "ENSG..."` equality can never match an IRI, so every
   gene-scoped fetch came back with zero rows. Fixed by suffix-matching (`STRENDS`) fields whose
   `transform` is `iri_localname` instead of comparing by equality (`043664b`).
4. **TogoVar's own Sorted TOP cap is far smaller than RDF Portal's.** RDF Portal allows 200000
   rows past an `ORDER BY`+`LIMIT`/`OFFSET`; TogoVar's Virtuoso instance allows only 10000. The
   panel-scoped table's true count (30108) sits comfortably under the first and over the second,
   so the existing upfront COUNT(*) pre-check never triggered batching, and paging 500'd once
   offset+limit passed 10000. Fixed by reading the HTTPError body (previously discarded) and
   reacting to Virtuoso's "Sorted TOP clause" message live, mid-paging, exactly like the upfront
   check does - switching to VALUES-batching and discarding whatever partial pages were already
   written (`898fd63`, `db4969a`).

A fifth bug, once fetch itself worked: **`riskAlleleFreqInControls` (`java.lang.Double`) is `"NR"`
("Not Reported") on 10853 of 30108 rows.** Writing that straight through produced an items.xml
InterMine's own loader could not accept - the whole retrieve failed with a bare
`NumberFormatException: For input string: "NR"` and no indication of which item or field caused
it. Fixed generally, not just for this one field: `ItemStore.get()` now checks a value against
its field's declared type for the numeric Java types and drops (not writes) one that does not
parse, logged via the same mechanism as attribute conflicts (`56e3224`).

Loaded clean after all five fixes: `BUILD SUCCESSFUL`, 33140 items, verified via the REST API that
CYP2D6 and TAS2R38 (both in the demo panel) each show multiple `GWASResult`s with real p-values,
and that a `riskAlleleFreqInControls` sourced from `"NR"` comes back `null` rather than crashing
the load. Also found live: the mine's system Java default is now 25 (a sandbox change made after
the last successful build in this trial), which Gradle 4.9 cannot start a daemon under; every
`./gradlew` invocation in this checkout needs `JAVA_HOME=/usr/lib/jvm/java-8-openjdk-arm64`
until the mine's own build tooling is upgraded - not an rdfc2im issue, noted here so the next
session does not re-diagnose it.

## reactome: loaded clean, but 5687 pathways was wrong - flagged from outside, not caught by `check`

Reactome's own `BUILD SUCCESSFUL` and `check`'s "0 hard problems" both looked clean on the first
load, and it was only questioned because the loaded pathway count (5687) was roughly double the
real number of human Reactome pathways (~2884) - a sanity check against outside knowledge, not
anything internal to the pipeline. Investigating found a genuine merge bug: `key_attributes()`
(the plural helper `items.py`'s in-memory merging uses, added earlier in this same session) had
no fallback for a class with no curated key in any keys file - only its sibling `key_attribute()`
(singular, used by `project` to propose a DRAFT key) had one. Pathway is exactly that class:
`project` can only ever propose a DRAFT `Pathway.key_identifier=identifier`, generated too late
in the pipeline (project runs after items) for items.py's own run to see it. Every Pathway row
fell back to the fragile all-values hash key instead, and RDF Portal binding Reactome's
multi-valued `biopax:comment` once per value meant 2803 of 2883 real pathways loaded as two
separate, never-merged items (one row with a real description, one with the filter-blanked
"Edited:"/"Authored:" comment). Fixed by giving `key_attributes()` the same fallback (`d1ade30`).

Re-loading the corrected 2886-item file into the already-loaded (buggy) database needed three
rounds of manual cleanup beyond just restaging the file, none of them an rdfc2im bug - all
InterMine data-tracking state left over from integrating the same source twice, which had never
come up earlier in this trial (every other source's fix was applied before its first load, not
after):
1. Deleted the stale `pathway`/`datasetspathway` rows (5687 each) directly - safe, since nothing
   else in the shared production DB referenced them (no `genespathways`/`pathwayproteins` rows
   yet at this build stage).
2. First retry failed: "There is already an equivalent in the database from this source... noticed
   problem while merging field species" on the shared `Organism` row (taxonId 9606, used by
   every source) - `humanmine-reactome`'s own leftover rows in the `tracker` table (InterMine's
   per-object-per-field-per-source provenance log) from the first load conflicted with the fresh
   contribution. Fixed by deleting `tracker` rows where `sourcename='humanmine-reactome'`.
3. Second retry failed: "Object o1 is not in the data tracking system... DataSet:26000002" -
   deleting *all* of reactome's tracker rows in step 2 left the reactome-only `DataSet`/
   `DataSource` rows ("Reactome pathways (RDF Portal)" / "Reactome") without the tracking history
   the loader expected for an *update*. Fixed by deleting those two rows outright (both were
   reactome-exclusive, confirmed via `bioentitiesdatasets`/`datasourcepublications` having zero
   references to either), so the reload creates them fresh instead of trying to update them.

Loaded clean after that: `BUILD SUCCESSFUL`, pathway count 2883 (matches the real number of human
Reactome pathways almost exactly), verified via the REST API.

## pubmed: PMID-scoped via a new fetch-time mechanism, not the existing gene-panel one

PubMed has no Gene field of its own, so it cannot be scoped via `apply_gene_scope`/`--genes`;
the plan from the start was to scope it instead to the PMIDs already referenced by the panel's
genes/variants/associations, gathered from the other sources' own already-generated items.xml
files (`extract_publication_pmids`, scanning for `Publication.pubMedId` attributes - 3881 unique
PMIDs across hgnc/uniprot/gwascatalog; clinvar contributed none).

The first attempt reused the existing gene-panel scoping mechanism as-is
(`apply_publication_scope`, a thin wrapper over `restrict_field` - same FILTER-embedding
`apply_gene_scope` uses) and it did not work at this scale: a ~3881-condition
`FILTER(STR(?identifier) = "..." || ...)` got RDF Portal's endpoint to redirect to
`errordocument.dbcls.jp`, a generic error page, rather than returning a normal SPARQL error -
confirmed this is a real size/complexity ceiling, not a network-policy or transport issue, since
the exact same mechanism works fine at the gene panel's own scale (~113 terms).

Fixed by adding a second, separate scoping mechanism for this case: `pipeline.fetch_source_by_keys`
restricts every one of a source's tables via `fetch_values_batched` (the same VALUES-batching
`_fetch_paged` already uses to page a table past Virtuoso's Sorted TOP cap) instead of embedding
the restriction into the committed query at translate time. `apply_publication_scope` was removed
again since it had no remaining caller - PubMed's cited-publication list will always be too large
for FILTER-embedding, so there was no smaller-scale use case left to justify keeping it. `--pmids`
now applies at fetch time only, gated by a `pmid_scope_field` entry in sources.yaml (pubmed's is
`identifier`, its own SELECT variable name in all three of its tables).

Loaded clean on the first real attempt after that: `BUILD SUCCESSFUL`, 37917 items (3879
Publication + MeshTerm + Author), 0 hard problems, and MeshTerm - which shares the exact same
"no curated key, DRAFT only" situation as Reactome's Pathway (see the reactome section above) -
merged correctly on the first try because the `key_attributes()` fix already covered it: 3606
MeshTerm items, all with distinct identifiers, zero duplicates. Verified via the REST API: PMID
23696881 (already loaded with just a bare `pubMedId` by GWAS Catalog) now also carries its real
title from PubMed, MeshTerm links resolve (e.g. PMID 10022751 -> 5 MeSH descriptors), and CYP2D6
(a demo panel gene) shows a real cited publication with its title through `Gene.publications`.

This completes the demo panel build: ncbigene (full) + hgnc/ensembl/uniprot/clinvar/gwascatalog
(gene-panel-scoped) + reactome (full) + pubmed (PMID-scoped) all loaded and cross-verified.

## Postprocessing and public templates: two gaps found in the running mine, not rdfc2im itself

Neither is an rdfc2im bug - both are steps the trial stack never exercised until asked to check
the classic webapp's own features (search, autocomplete, the QueryBuilder class list) rather than
just the REST API and BlueGenes, which don't need either.

**No postprocess had ever been run** - every source went through `dbmodel:integrate` only. Real
symptoms this caused, none of them data loss (every class was fully populated in the database the
whole time): the classic webapp's QueryBuilder class selector didn't bold `Gene`/`Protein` (it
reads counts from the object-store summary, which had never been generated), and
`/service/search` 500'd (no search index existed). Ran, in the order `project.xml`'s own
`<post-processing>` block declares: `create-references`, `create-attribute-indexes` (2 harmless
failures - `pathway__description_equals`/`_like`, a CLOB column too long for a plain btree index),
`create-search-index`, `create-autocomplete-index`, `summarise-objectstore`. Skipped the
genomic-location-specific ones (`create-chromosome-locations-and-lengths`, `transfer-sequences`,
`create-gene-flanking-features`, `create-location-overlap-index`, `create-overlap-view`,
`populate-child-features`) - confirmed live that `location` has 0 rows and there is no `sequence`
table at all, so none of this build's sources populate the coordinate/sequence data these need;
running them would either no-op or fail on a missing prerequisite, not produce anything real.
Also skipped `do-sources` (an umbrella that runs every configured source's own postprocess hook,
including several sources this build never fetched - too broad to run blind) and the
`update-data-sources`/`update-publications` *sources* (not postprocess tasks): `update-data-sources`
needs `/micklem/data/uniprot/xrefs/current/dbxref.txt`, a Micklem-lab path that does not exist in
this sandbox - an expected limitation of a reduced, non-lab environment, not something to
manufacture fake data to satisfy.

`create-search-index`/`create-autocomplete-index` need a Solr server the trial stack never had.
Added one (`trial/docker-compose.yml`'s new `solr` service, `solr:8.6.2`, the same version
InterMine's own CI uses) with a persistent volume so the two cores it needs
(`humanmine-search`, `humanmine-autocomplete` - create once after the first `make up`, see the
compose file's own comment) survive `down`/`up`, same as `pgdata`. The webapp's own copy of the
Solr URL (`keyword_search.properties`/`objectstoresummary.config.properties`, baked into
`dbmodel.jar` inside the war at build time - not loose files like the DB host, so patching them
needs rewriting a zip entry, not a plain `sed`) defaults to `localhost:8983`, correct for
`dbmodel:postProcess` running natively in the sandbox against Solr's published port, but wrong
for the *webapp*, which runs inside `rdfc2im-mine` and needs the compose network's own `solr`
hostname instead - same class of problem as the DB host, fixed the same way: `trial-stage.sh` now
rewrites the jar entry after exploding the war, same idempotent, re-run-after-every-build pattern.

`create-search-index` also stalled once, mid-run, with zero progress for 15+ minutes and a
growing pile of `Connection attempt timed out` errors against `humanmine-items`/
`humanmine-production` alongside a climbing idle-connection count on postgres (`pg_stat_activity`)
- not a data or config problem (the identical command had already succeeded once earlier at the
same data volume, and succeeded again cleanly in 72s on the very next attempt after `kill -9`ing
the stuck gradle daemon/JVM and clearing the accumulated idle connections). Recorded here as an
environment quirk to recognise rather than re-diagnose if it recurs: kill and retry rather than
waiting indefinitely once the doc count truly stops moving for several minutes.

**Only 3 of HumanMine's stock templates survive** "reducing a mine" (see that section above) -
expected, they're the only ones that don't reference a class/field this build doesn't load. Added
6 new public templates for the demo panel (GO term structure, cross-source gene identifiers,
UniProt proteins, ClinVar alleles, GWAS associations, cited publications), each with its
identifying constraint `editable="true"`, matching the stored XML shape of the 3 survivors
exactly (`savedtemplatequery.templatequery`, one row per template, owned by the superuser
account) plus an `im:public` row in `tag` for each. Both tables are 3-column-ish and have no
`id` default (i.e. no Postgres sequence backs them for these rows), so new ids were picked well
clear of the existing rows and of `objectstore_unique_integer`'s own low range. **This only lives
in the running `humanmine-userprofile` database - the webapp caches its template list at startup,
so a change needs a `docker restart rdfc2im-mine` to show up, and neither table is part of any
config file rdfc2im or the mine checkout writes.** It survives `docker compose down`/`up` (keeps
the userprofile volume) but not `make trial-destroy` or a fresh build. Saved the exact SQL as
`curation/demo_public_templates.sql`, with the re-apply command, so this is at least one command
away from durable rather than living only in a database only this session touched. Verified all 9
templates appear via `/service/templates`, ran each of the 6 new ones via
`/service/template/results` with its default value (real results for all six - e.g. GO:0000016
resolves to "lactase activity" with its real parent term, TPMT resolves to a real cited
publication), and re-ran `Gene_MultiSource_Identifiers` with `Gene.symbol=TPMT` instead of its
default `CYP2D6` to confirm it is a genuine parameterised template, not a query with the look of
one.

## Two follow-up corrections, caught by checking the live system directly, not the report

**`summarise-objectstore`'s output is not a table called `objectstoresummary`** - `\dt` against
either `humanmine-production` or `humanmine-items` will never find one; `SummariseObjectstoreProcess`
stores it as a single row in the generic `intermine_metadata` table (`key='objectStoreSummary'`,
a serialized properties blob - 25916 bytes in this build). Confirmed the postprocess genuinely ran
and the QueryBuilder fix is real, not inferred from the other postprocess tasks succeeding: the
`objectStoreSummary` row is present with real content, and a fresh `customQuery.do` fetch (not a
cached one from earlier in the session) still shows `Gene`/`Protein` bold in the class list.

**Updating `BLUEGENES_DEFAULT_MINE_NAME` in the compose override needs the container recreated,
not just the file edited** - `docker restart` reuses the container's existing environment,
baked in at creation time; only `docker compose up -d <service>` (or an explicit recreate)
re-reads the compose files and applies a changed `environment:` value. The env var change had
been made but the container was never brought up again with it, so BlueGenes kept serving the
old name for the rest of the session. Fixed with `docker compose -f trial/docker-compose.yml -f
trial/docker-compose.local.yml up -d bluegenes`, confirmed via the container's own `env` and via
the mine name appearing in the page's embedded bootstrap config
(`:bluegenes-default-mine-name "rdfc2im: 113 gene food/drug-metabolism panel"`) and JSON-LD
metadata on a fresh fetch of `/`.

That same `up -d` also recreated `rdfc2im-postgres` (compose decided its spec had changed too,
likely from the `solr` service's `depends_on` addition affecting the stack's dependency graph) -
worth knowing about, since "recreate" sounds alarming next to a stateful service: the named
`pgdata` volume is untouched by container recreation (only `down -v`/`trial-destroy` drops it),
and every count checked after the fact (193288 Genes, 2883 Pathways, 9 templates) matched exactly
what was there before. No data was at risk, but an `up -d` that recreates more than the one
service you asked for is worth a second look before assuming it did.

## Two stock templates still defaulted to Plasmodium falciparum

`All_Proteins_In_Organism_To_Publications` and `Organism_Protein` (both pre-existing stock
templates, not the 6 added for the demo panel) had their editable `Protein.organism.name`
constraint defaulting to `"Plasmodium falciparum 3D7"` - a leftover from HumanMine's full stock
template set. Harmless but confusing on a human-only build: running either with no override
returned zero results, reading as broken rather than "wrong default organism". Verified this
mine's actual `Organism.name` string live (`Homo sapiens`, not e.g. `H. sapiens`) rather than
assuming it, then repointed both stored templates at it with a plain `UPDATE ... replace(...)` on
`savedtemplatequery` (same table the 6 new templates live in). Verified via
`/service/template/results` with `value1=Homo sapiens` (the now-correct default) that both return
real data - `All_Proteins_In_Organism_To_Publications` includes a real cited publication (PMID
26871637), `Organism_Protein` returns real Protein accessions. The UPDATE is appended to
`curation/demo_public_templates.sql` so it reapplies alongside the 6 INSERTs after a fresh build.

## DataSource.url: durable code fix done; retrofitting the live mine is NOT safe as a quick patch

`DataSource.url` was empty on every one of the 137 rows - rdfc2im never set it, entirely
dependent on stock HumanMine's `update-data-sources` postprocess task, which needs a Micklem-lab
config path this sandbox doesn't have (see the earlier postprocessing section). This is what
breaks BlueGenes' "Browse Sources" page. Fixed durably: `sources.yaml` gained an optional
`data_source_url` for the 9 sources this build actually loads, wired into `items.py`'s DataSource
creation, with the URL for each chosen from what it actually queried (an RDF Portal dataset page,
confirmed live to return a real "Dataset Details" page, for the RDF-Portal-hosted sources; the
source's own site - UniProt, TogoVar - for the two that go straight to their own native
endpoints; GO falls back to its own project site since RDF Portal has no `/dataset/go` landing
page). Regenerated all 9 items.xml files with the fix and confirmed it in the generated XML.

**Retrofitting the already-loaded live mine turned out to be genuinely unsafe, not just
inconvenient**, and was abandoned rather than forced:

1. A direct `UPDATE datasource SET url = ...` on the 9 rows looked like it worked (confirmed via
   plain SQL) but the live webapp kept returning `null` for `url` no matter how many times the
   container was restarted or even fully recreated. Root cause, found by enabling Postgres
   statement logging and watching the actual query: InterMine materializes query *results* from a
   separate `InterMineObject.OBJECT` column (a serialized full-object snapshot, part of Postgres
   table inheritance), not from the typed `datasource.url` column directly - a raw SQL UPDATE
   changes the typed column but leaves that serialized snapshot stale, so nothing reading through
   normal query materialization ever sees the change. (A red herring along the way: an earlier
   `WEB-INF/objectstoresummary.properties` mentioning `DataSource.emptyAttributes=...url` looked
   like the cause and pointed at a real, separate gap - `summarise-objectstore`'s DB-side output
   was never being baked into the war - but fixing that alone did not fix this.)
2. The only correct way to update that serialized snapshot is through InterMine's own loader
   (`dbmodel:integrate`), so items.xml was regenerated and re-staged for all 9 sources for a
   proper re-integrate. `go`'s re-integrate hit a genuine, pre-existing, unrelated bug on the
   first attempt: `Duplicate objects found for pk OntologyTerm.key_name_ontology` (two distinct
   GO terms, e.g. two "obsolete elastin" entries, sharing the same name+ontology) - real upstream
   ambiguity in GO's own data, never surfaced before because `go` had never been re-integrated
   since its original load at the very start of this trial. Left unfixed, same category as
   STATUS.md's other documented upstream ambiguities.
3. Re-integrating a source that has *already* been loaded once needs its `tracker` rows (the
   per-object-per-field-per-source provenance log - see the reactome section above) cleared
   first, same as before. This time the tracker rows for all 9 sources were cleared in one
   blanket `DELETE`, which was too broad: `ncbigene` is the root every other Gene-touching source
   merges onto, and wiping *its* tracking for a shared field (`Gene.typeOfGene`) broke every
   other source's ability to re-integrate too, each failing with `Object o1 is not in the data
   tracking system` the moment it touched a Gene ncbigene had already set that field on.
   `dataLoader.allowMultipleErrors=true` (this session's established workaround for a *handful*
   of genuine conflicts) does not help here: every touched object throws this error, not a
   bounded number of real conflicts, so it immediately hits "Too many data loading exceptions"
   regardless of source size - tried and reverted for both `ncbigene` (57s, real work attempted)
   and `reactome` (4s) before concluding this path doesn't apply.
4. Recovered by reverting the 9 `datasource.url` values back to `''` (undoing the raw SQL update
   that never actually took visible effect anyway) and confirming no data was corrupted by any of
   the failed load attempts - InterMine's per-source load is transactional and rolled back
   cleanly every time: Gene (193288), Pathway (2883), Publication (4025), GWASResult (23201) and
   DataSource (137) row counts are exactly what they were before this was attempted, and search/
   templates/QueryBuilder all still work. The live mine is stable, just without this particular
   fix - the same "Browse Sources is broken" state as before, not worse.

**Conclusion**: the code fix is real, tested, and will apply automatically to any *fresh* build
(`dbmodel:builddb` + a normal first-time integrate of all 9 sources, the situation every other
fix here has usually landed in). Getting it into *this specific, already-loaded* mine would need
a properly incremental, source-by-source tracker reconciliation at a scale this session did not
have the budget to do safely (ncbigene alone tracks ~4.6M rows) - or a full rebuild from scratch.
Neither was attempted further; forcing it risked the stability of a mine several other fixes
already depend on, for a page (Browse Sources) that was already broken before this session
started. Flagging this as an explicit, deliberate decision point for the user rather than a
"still working on it."

## Quicksearch for "cyp" found one wrong gene and a phantom duplicate - two distinct bugs

Reported: searching "cyp" showed only one Gene (CYP4F2, out of 121 real CYP-family genes), and
clicking through it showed two Gene objects, one throwing InterMine's classic "may have existed
before and been assigned a new ID" error. Confirmed before any fix: `/service/search?q=cyp` -
`totalHits: 25`, `Gene: 1`, and that one Gene hit was **PPIG**, textually unrelated to "cyp" at
all. Two separate, real bugs, both found by direct investigation rather than assumption:

**1. Orphaned `intermineobject` rows** - InterMine's generic object store (`intermineobject`:
`id`, `class`, a serialized snapshot) is separate from each class's own typed table (`gene`,
`pathway`, ...); every real object needs a row in both. Found 5802 rows that only had the
generic one: 113 Gene (stub objects with just a symbol and id, e.g. `id=22001211,
symbol=CYP4F2` - the exact phantom duplicate reported) and 5687 Pathway, both leftovers from
earlier manual SQL cleanups this session that deleted rows from the typed table but never
touched their `intermineobject` counterpart - the 113 from the DataSource.url retrofit's failed
`humanmine-ncbigene`/hgnc/etc. re-integration attempts (see that section above), the 5687 from
the very first reactome Pathway-duplicate cleanup, several sections above, which predates this
finding entirely. `create-search-index` indexes from `intermineobject`, so these orphans became
phantom search hits with almost no real content, and any object lookup by their id - e.g.
clicking through from search - correctly finds nothing in the typed table, producing exactly
the "assigned a new ID" error InterMine gives for this situation. A full sweep confirmed these
were the only two affected classes (every other class checked both directions, zero orphans);
deleted all 5802, confirmed zero orphans remain in either direction afterward.

**2. Solr's auto-guessed field type had no partial-word matching at all.** This trial stack's
Solr cores use the `_default` schemaless configset (the same one used to create them - see the
`solr` service's own compose comment), which infers each field's type from the first value
written to it. The inferred `analyzed_string` type (used for every InterMine content field -
`gene_symbol`, `gene_name`, `publication_title`, ...) came out as plain whitespace-tokenize +
lowercase, nothing more. InterMine's own quicksearch never appends a wildcard server-side
(confirmed reading `SolrKeywordSearchHandler.java`: the raw query string reaches Solr
unmodified), so a bare term like "cyp" could only ever exact-match a whole token - "cyp4f2" is
one token under whitespace tokenization, so it never matched, while `gene_symbol:cyp*` (with an
explicit wildcard) matched fine. With no real match anywhere, edismax's boost query
(`bq=classname:Gene^1.5`) was apparently enough on its own to surface *something* - hence PPIG,
not a "no results" empty page. Fixed by giving `analyzed_string` an `EdgeNGramFilter` on the
index-side analyzer only (`tools/solr-search-schema-fix.sh`) so a query term matches any
indexed prefix-gram without needing a wildcard. A first attempt at `maxGramSize=25` reproducibly
OOM'd the indexing JVM against this build's longer free-text fields (publication abstracts,
pathway descriptions - EdgeNGram generates a token per gram length per word, and a paragraph
has a lot of words); `maxGramSize=10` indexed cleanly in ~70s with no cap on document count.

Verified thoroughly, not just "no errors": `/service/search?q=cyp` now returns `totalHits: 600`,
`Gene: 172` (real matches - CYP-family genes and pseudogenes, plus genes whose description text
mentions "cyp", both legitimate). CYP4F2, CYP2D6, CYP3A4 and CYP1A1 all resolve to exactly one
real Gene each on an exact-symbol search (ids 4191380, 4168641, 4168665, 4168584), and every one
of those ids round-trips through a direct REST query with no error. The specific phantom
(id 22001211) no longer exists in the search index at all. PPIG's own search now correctly shows
PPIG and its real pseudogene PPIGP1, not CYP4F2. Unrelated searches (TPMT, VKORC1, "clinical
significance", "rs123") still return real, sensible results. Response time is unaffected
(~20ms). Templates and the QueryBuilder class list, both fixed earlier this session, are
unaffected by any of this.

## List widget investigation: mostly working, one real widget-statistics bug found

Created two test lists to exercise the widgets that need one (`demo_panel_genes`, 113 genes;
`demo_panel_snps`, 2505 SNPs - every SNP in the mine, since gwascatalog was fetched scoped to
the panel genes in the first place) - left both in the mine as a reusable demo artifact rather
than deleting them. REST list creation needs an authenticated session token (`/service/session`
after `/service/user/authenticate`, then `?token=...` on every list call) and the identifiers
must go as the raw POST body (`Content-Type: text/plain`), not a form field - the first few
attempts 401'd or 500'd (a bare `NullPointerException` in `ListUploadService`) until landing on
that shape.

**Widgets with real underlying data:**

- `publication_enrichment` (Gene) - works. 152 results with `correction=None` including real
  overlaps (e.g. PMID 11099417, matched by both ABCG5 and ABCG8 - confirmed by hand first).
  First attempt returned an empty result set with `wasSuccessful: true` and no error - traced to
  passing `correction=Holm-Bonferroni`, not a name this widget recognises; it silently computes
  zero matches instead of rejecting the parameter, worth knowing next time this needs checking by
  hand.
- `chromosome_distribution_for_gene` (chart) - works. Real actual-vs-expected counts per
  chromosome for all 113 panel genes.
- `snp_publication_enrichment` (SNP) - works. 1178 results, e.g. PMID 17293876 ("A genome-wide
  association study identifies novel risk loci for type 2 diabetes"), matches=2.
- `snp_gwas_study_enrichment` (SNP) - **genuinely broken**, but not a mine-infrastructure bug.
  The response carries `wasSuccessful: true` and an empty result set, but also a `message`:
  `number of successes (3,716) must be less than or equal to population size (2,311)` - the
  underlying hypergeometric test rejects its own inputs. Root cause: GWASResult has no
  integration key (already documented earlier this session - "its items are stored without
  merging"), and a single real GWAS association can list more than one gene; each
  (association, gene) pair becomes its own separate GWASResult item instead of one item with a
  multi-valued gene collection, so the same real (SNP, study) pair is recorded as several
  distinct GWASResult rows. Confirmed directly: 2828 of 23201 GWASResult rows are exact
  duplicates of another row (same snpid, studyid, phenotype, p-value, risk allele), differing
  only in which gene they point at (e.g. `snpid=25000004, studyid=25000003` appears twice,
  identical in every other column). That inflates the "successes" count this one widget's
  statistics see past what its "population" (distinct SNP count) supports. A proper fix needs a
  real key-design decision for GWASResult (a key across SNP+study+phenotype+riskAllele, excluding
  gene, so genes accumulate into a collection instead of splitting the object) followed by
  re-integrating gwascatalog - given how much operational risk a re-integration carried earlier
  in this same session (see the DataSource.url section above), this was not attempted; flagging
  it as a decision point rather than forcing it.
- `chromosome_distribution_for_snp` (chart) - **correctly empty, not broken**: confirmed
  `snp.chromosomeid` is null on all 2505 rows - rdfc2im's gwascatalog mapping never populates
  `SNP.chromosome` (same "no genomic Location/coordinate data loaded" gap the postprocessing
  section above already found for the genomic-location-specific postprocess tasks).

**Widgets already known to lack data - verified correctly empty, not silently broken:**
`go_enrichment_for_gene`, `prot_dom_enrichment_for_gene`, `pathway_enrichment` (even with
`filter=All`) and `interactions` (a `table`-type widget, not `enrichment` - needs
`/service/list/table`, not `/service/list/enrichment`) all return `wasSuccessful: true`, zero
results, and no `message`/`error` field - a clean, honest "no data" rather than a masked
failure. `pathway_enrichment` being empty matches what `sources.yaml` already documents:
rdfc2im's reactome source only ever creates `Pathway`/`Organism` items, never the
`Gene.pathways` link stock reactome provides.

## MeSH's own endpoint has a row limit too, found refreshing STATUS.md's counts

Not from a load - found re-fetching `hpo`/`mp`/`uberon`/`mesh` to refresh `STATUS.md`'s
`fetched`/`items` columns after they'd gone stale (the demo mine's actual loaded MeshTerm count,
3606, includes stubs PubMed's citations create too, so this was not otherwise visible).
`mesh/main`'s plain `SELECT DISTINCT` (no `ORDER BY`, so none of the Sorted-TOP-cap machinery
built for RDF Portal/TogoVar applies) against `https://id.nlm.nih.gov/mesh/sparql` returns exactly
1000 rows and stops - `rdfc2im fetch`'s own pagination loop correctly read that as "fewer than a
full page, must be done" and moved on, which is the right call for a paged endpoint but wrong
here. Confirmed live: a plain `COUNT(*)` over `meshv:TopicalDescriptor` 502s (an unbounded
aggregate crashing the upstream is not what a ~1000-row dataset does), and `LIMIT 1500` on the
same query that returns cleanly at `LIMIT 1000` times out completely rather than truncating - a
different failure shape from Virtuoso's clean 200,000-row cutoff or TogoVar's: no error at all, a
plain HTTP 200 silently short of what was asked for.

**Resolved.** `LIMIT`/`OFFSET` paging in chunks at or below the true per-request ceiling works
correctly here - confirmed live with `LIMIT 1000 OFFSET 1000` and `OFFSET 2000`, each returning
real, distinct, correctly-ordered rows (`ORDER BY ?descriptor_id`, the variable `_order_by`
already adds). The fix is a general one, not specific to this endpoint: `_fetch_paged` now treats
a short page as ambiguous rather than conclusive - when a page returns fewer rows than requested,
a cheap `LIMIT 1 OFFSET <past this page>` probe checks whether more data exists before believing
"done". If it does, the returned row count becomes the new page size for every request from then
on, and paging continues; if not, this is a real end and the existing behaviour is unchanged. Also
found and fixed while making this work end to end: this endpoint sends a **genuinely empty body**
(`Content-Length: 0`, still HTTP 200) as its real end-of-data signal one page past the last real
one, which `_normalise`/`_json_to_tsv` crashed on (`json.loads("")`) rather than reading as zero
rows - a latent bug that this endpoint's page-1 truncation is what first made reachable, since
without the retry-at-a-smaller-page-size fix `_fetch_paged` never paged far enough to hit it.

Verified end to end: `mesh/main` now fetches 10,000 rows across 11 pages (was silently capped at
1,000) and terminates on a real empty final page, not another truncation; `mesh` builds 9,809
`MeshTerm` items (was 968); `check` stays at 0 hard problems; a new regression test
(`test_paged_fetch_recovers_all_rows_past_a_silent_per_request_truncation`) pins the general
behaviour down independent of this one endpoint.
