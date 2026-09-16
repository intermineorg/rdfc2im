# Load trial - `go`, end to end, 2026-09-16

The first time an rdfc2im items file has been loaded into a real InterMine. It found four
problems that nothing short of a load would have found; all four are fixed in the commits
that reference this file. Recorded here so the next person can repeat it.

## What was loaded

A full `go` extract, no `LIMIT`: 48,351 terms, 99,892 synonym rows, 92,208 `rdfs:subClassOf`
rows, fetched from `https://rdfportal.org/primary/sparql` in 5-10 pages of 10,000 per table
(~65 s total). `rdfc2im items` turned that into a 50 MB items file, 125,213 items, in 3.5 s.

In the production database afterwards:

| table | rows | |
|---|---|---|
| `goterm` | 48,351 | exactly the terms fetched - no stubs |
| `ontologytermsynonym` | 76,859 | |
| `ontologytermparents` | 65,768 | exactly the OBO-namespace superclass rows; the other 26,440 raw rows were blank nodes, `owl:Thing` or absent |
| `intermineobject` | 125,213 | |

Spot-checked: `GO:0000016` "lactase activity", namespace `molecular_function`, parent
`GO:0004553` "hydrolase activity, hydrolyzing O-glycosyl compounds", synonym
"lactose galactohydrolase activity" (exact). All correct.

## Environment

PostgreSQL 14 in rootless podman on port 15432; JDK 8 (the mine is `sourceCompatibility = 1.8`
and Gradle 4.9 cannot use a newer one); InterMine artifacts resolved from Maven Central, so
InterMine itself does not have to be built from source.

```
podman run -d --name im-pg -e POSTGRES_PASSWORD=intermine -e POSTGRES_USER=intermine \
    -p 15432:5432 docker.io/library/postgres:14
for db in humanmine-production humanmine-items humanmine-userprofile; do
    podman exec im-pg psql -U intermine -d postgres -c "CREATE DATABASE \"$db\";"
done
export JAVA_HOME=/usr/lib/jvm/java-8-openjdk-<arch>
```

`~/.intermine/humanmine.properties` needs `db.production`, `db.common-tgt-items` and
`db.userprofile-production` pointing at those three, and the mine's
`default.intermine.properties.file` must be InterMine's
`intermine/resources/src/main/resources/default.intermine.production.properties` - it is what
defines `integration.production`. The copy under `bio/model` defines `integration.bio-test`
instead and gets you *Failed to instantiate IntegrationWriter class*.

## Steps

```
make fetch SRC=go LIMIT=0 FORCE=1     # ~65 s
make tsv items project
make fork-sync
# in humanmine-bio-sources/ (see humanmine-items/README.md for the settings.gradle entry)
./gradlew :bio-source-humanmine-items:install
# in the mine
./gradlew :dbmodel:builddb
./gradlew :dbmodel:integrate -Psource=humanmine-go
```

## What it found

1. Keys and additions were not packaged (`src/main/resources/` vs `resources/`). Build
   reports SUCCESS, additions silently unmerged, load dies in `DataLoaderHelper`.
2. `<source>` needs `version="4.3.0"`, or the mine resolves `bioVersion` and never finds the jar.
3. The gradle project must be `bio-source-humanmine-items`, not `humanmine-items`.
4. **Classes contributed by a replaced source disappear with it.** `GOTerm` is not core; it is
   declared in `go`, `go-annotation`, `interpro-go`, `uniprot` and `psi-complexes`. We replace
   `go` and `uniprot`. The load failed with `class "GOTerm" does not exist`. Now carried into
   `humanmine-items_additions.xml`, along with the other ten non-core classes.

## Two things that were the trial's fault, not the tool's

- HumanMine's `genomic_priorities.properties` names `ProteinDomain` and `Disease`, which exist
  only once interpro and the disease sources are present. A cut-down `project.xml` must trim the
  priorities to classes in the merged model, or `PriorityConfig` refuses to start. This is worth
  remembering for the real load: **`out/_mine/genomic_priorities.properties` must be merged into
  the mine's existing file**, and every entry's class must exist in the merged model.
- `humanmine-bio-sources` does not currently build as a whole: `bio-source-humanmine-static`
  depends on the JCenter/Bintray gradle plugins, and Gradle configures every sibling project, so
  one dead sibling fails the build. Building the single source in isolation works.

## Not covered

Only `go` was loaded, and into an otherwise empty mine. Nothing here exercises integration
*keys* doing real work - merging a Gene arriving from both `humanmine-ncbigene` and
`humanmine-hgnc` - or the priorities file resolving a genuine conflict. That needs two sources
loaded in sequence and is the obvious next trial.
