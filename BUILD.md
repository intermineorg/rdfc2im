# Building the demonstration mine

`tools/full-build.sh` takes this repository from nothing to a running, fully-configured
demonstration HumanMine: RDF Portal SPARQL extraction, Items XML generation, the InterMine
database build and load, postprocessing, the Solr search index, the webapp, BlueGenes, and the
demo templates. One command, 18 phases, no manual steps in between.

This file is the operator's guide to running it on an ordinary Linux machine with Docker. The
script's own header comment (`sed -n '1,65p' tools/full-build.sh`, or `tools/full-build.sh
--help`) is the authoritative reference for its options and environment variables; this file
covers what you have to provide, what to expect, and what to do when it goes wrong.

It was written from the record of many hand-run sessions (`LOAD-TRIAL.md`) and first run for
real on 2026-09-21, which surfaced and fixed about thirty genuine bugs. It was then run again
from scratch in a different sandbox the same day - new checkout state, new Docker volumes, a
new `TRIAL_HOME`, a workspace still holding derived files from earlier sessions - which found
ten more (see "A second, from-scratch run" below). Everything here is from those runs, not
from reading the script.

## What you get

A 113-gene food/drug-metabolism demonstration HumanMine:

- **Nine rdfc2im-generated sources** - `go`, `ncbigene`, `reactome`, `hgnc`, `ensembl`,
  `uniprot`, `clinvar`, `gwascatalog`, `pubmed` - all loaded through the stock
  `intermine-items-large-xml-file` loader, with no per-source Java.
- **Plus the stock `reactome` source loaded alongside.** rdfc2im's own reactome mapping covers
  `Pathway.description` but not pathway participants (the `pathwayComponent` link is
  unmapped/pruned), so without the stock source a `Pathway` never connects to any `Protein` or
  `Gene`. The script builds `org.intermine:bio-source-reactome` from upstream source (its
  published jar is dead on JCenter) and runs `ReactomePostProcess`, which is what fills
  `Gene.pathways` from `Protein.pathways`.
- **Four containers**: the InterMine webapp at `http://localhost:8090/humanmine`, BlueGenes at
  `http://localhost:5000`, PostgreSQL on `15432`, Solr on `8983`. All four publish to
  `127.0.0.1` only, so they are reachable from the machine that built them and nowhere else.

Scope is controlled by `--sources`, `--genes` and `--taxon`; the defaults are the demo panel
described above.

## Prerequisites

The script checks all of these in `phase_check_prereqs` and dies with a usable message if one is
missing - but it installs none of them. Get these in place first.

### JDK 8 (required, and it must be 8)

InterMine's mine build uses Gradle 4.9, which **cannot start a daemon under a newer JDK**. This
is not a warning you can work around; it fails immediately.

```sh
sudo apt-get install -y openjdk-8-jdk-headless          # 8u502 is what the first real build used
```

The script auto-detects `/usr/lib/jvm/java-8-openjdk-*` or `/usr/lib/jvm/java-1.8.0*`. If yours
lives elsewhere, set `JAVA8_HOME` to it. A newer JDK may remain your system default: the script
passes `JAVA_HOME=$JAVA8_HOME` to every Gradle invocation itself.

### Python venv at the repo root

```sh
sudo apt-get install -y python3-venv
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt               # pyyaml, lxml
```

The path is not configurable: `phase_rdfc2im_pipeline` looks for `<repo>/.venv/bin/activate` and
dies with *"no .venv at ... - create one with pyyaml+lxml first"* if it is absent.

### A standalone Gradle 4.9 launcher

```sh
curl -fsSLO https://services.gradle.org/distributions/gradle-4.9-bin.zip
unzip -q gradle-4.9-bin.zip -d ~/opt && export PATH="$HOME/opt/gradle-4.9/bin:$PATH"
```

This is needed by `phase_resolve_bluegenes_deps` and `phase_build_reactome_source`, which build
throwaway Gradle projects that have no wrapper of their own. Put it on `PATH` as `gradle`, or
point `GRADLE49` at the launcher. The humanmine and humanmine-bio-sources checkouts use their
own `./gradlew` wrapper and are unaffected by this.

### Docker and Docker Compose

Docker 29.7.2 with Compose v5.5.0 were used for the first real build. Compose v2 syntax is
required (the script calls `docker compose`, overridable with `COMPOSE=`). Your user must be
able to run `docker` without `sudo`.

### Everything else

`git`, `curl`, `unzip`, and `sudo` - the last only for a one-time, idempotent bootstrap of
`/micklem`, where `rdfc2im.yaml`'s `src_data_dir` points (a real Micklem-lab deployment path,
deliberately not rewritten to something machine-local). The script creates it and chowns it to
you if it does not already exist or is not writable.

### Network

The build reaches out to `rdfportal.org`, `grch38.togovar.org` (GWAS Catalog),
`www.humanmine.org`, `reactome.org`, `github.com`, `repo.clojars.org`, `services.gradle.org`,
`repo.maven.apache.org`, and Docker Hub.

### What the script sets up for you

You do not need to do any of this by hand:

- JDK 8 discovery and the `JAVA_HOME` override on every Gradle call.
- The `/micklem` bootstrap, and symlinking each source's items file into
  `/micklem/data/rdfc2im/<source>/`.
- `~/.gradle/gradle.properties`, written (if it has no `org.gradle.jvmargs` line) as:

  ```
  org.gradle.jvmargs=-Xmx4096m -Dfile.encoding=UTF-8
  ```

  Both halves matter. The heap: Gradle 4.9's 1 GB daemon default is not enough for InterMine's
  `ParallelBatchingFetcher`, which runs a prefetch thread per CPU core; it dies with
  `OutOfMemoryError: GC overhead limit exceeded` **in a background thread**, so the daemon does
  not fail cleanly - it spins in a GC death-spiral, burning CPU with no forward progress. The
  encoding: with no `LANG`/`LC_ALL` set, Java's platform default charset falls back to US-ASCII,
  and `BioFileConverter` reads data files through a platform-default-charset reader. Reactome's
  own `UniProt2Reactome.txt` contains ordinary UTF-8 (Greek letters in pathway names); under
  US-ASCII one of those mis-decodes to a literal NUL byte, and Postgres rejects the `COPY` with
  `invalid byte sequence for encoding UTF8: 0x00`. This is data corruption, not cosmetics.
- Source-list validation: a name not in the script's `SCOPE_MODE` table is a fatal error before
  any work starts, rather than silently becoming an unscoped full-database fetch hours later.
- All the upstream workarounds the first real build needed - the dead JCenter/Bintray plugins,
  the relative-path Ant bug in InterMine's own `DBModelPlugin`, the war built with no
  `WEB-INF/web.xml`, the missing userprofile schema, the reduced-mine trims of
  `genomic_priorities.properties`, `objectstoresummary.config.properties` and
  `webconfig-model.xml`.

## Running it

Always start with a dry run. It prints the exact command sequence without executing anything -
`run`/`run_in`/`write_file` are the only things in the script that touch the outside world, and
under `--dry-run` all three are no-op printers, so there is no separate code path that could
drift from what really executes.

```sh
tools/full-build.sh --dry-run
tools/full-build.sh
```

Useful options:

| Option | Effect |
|---|---|
| `--dry-run` | Print every command instead of running it |
| `--list-phases` | Print the 18 phase names, in order, and exit |
| `--from PHASE` | Resume: start at `PHASE` instead of the beginning |
| `--only PHASE` | Run exactly one phase and stop |
| `--sources "a b c"` | Override the nine-source demo panel |
| `--genes FILE` | Gene panel for the panel-scoped sources (default `curation/demo_gene_panel.txt`) |
| `--taxon LIST` | NCBI taxon ids (default `9606`) |
| `--skip-templates` | Do not apply `curation/demo_public_templates.sql` |
| `--use-cached-data` | Restore the fetched raw data from `cached_raw_data/` instead of fetching it again (see below) |
| `--cache-dir DIR` | Use `DIR` instead of `cached_raw_data/` |
| `--reactome-levels default\|all` | Which of Reactome's own two files to load (see below); default `default` |

`--from` and `--only` always run `check_prereqs` first regardless, because that phase is what
sets the `JAVA_HOME` override the Gradle calls need.

Key environment variables (see the script header for the rest): `TRIAL_HOME` (scratch tree for
the upstream checkouts and Gradle output, default `~/intermine-build/trial_home`),
`UPSTREAM_CACHE` (default `in/.upstream`), `JAVA8_HOME`, `GRADLE49`, `COMPOSE`, `PG_PORT`,
`BG_PORT`, `SOLR_PORT`, `MINE_TITLE`.

### Reactome pathway levels

The stock reactome source (phase 6/11/12 - see "What you get") reads one of Reactome's own two
published UniProt-to-pathway files:

- **`default`** (`UniProt2Reactome.txt`) - a protein is listed only against the specific, lowest-
  level pathway it participates in. A broad pathway like "Metabolism" gets no direct protein or
  gene participants of its own unless something happens to be annotated at that exact level - most
  of the demo panel's own pathway hits are on much more specific pathways (e.g. "Aspirin ADME"),
  which is why this is the default.
- **`all`** (`UniProt2Reactome_All_Levels.txt`, via `--reactome-levels all`) - every ancestor
  pathway also carries its descendants' proteins and genes, matching how a pathway's "Proteins"
  tab looks on reactome.org itself. The file is larger, `Gene.pathways` and `Pathway.proteins` fan
  out a lot more (a top-level pathway can have thousands of participants), and Reactome
  postprocessing and search indexing take longer.

Only one of the two files is ever present under `/micklem/data/reactome/current/` at a time - the
stock converter processes every file it finds there, so both together would double-count. Switching
`--reactome-levels` between runs removes the other one first. The cache
(`cached_raw_data/reactome-uniprot-map/`) records which level was fetched and refuses to restore it
for a build that asked for the other one.

## Caching the fetched data

The slow, remote part of a build is the SPARQL extraction (phase 3) plus one download (phase 6).
Every normal run saves what it fetched to `cached_raw_data/` (gitignored, in the repository root -
so on a sandbox it is on the host side of the workspace mount and outlives the sandbox), and

```sh
tools/full-build.sh --use-cached-data
```

restores it instead of touching the network for that data. (Phase 2, the upstream git clones and
the live HumanMine model, is not covered by the cache.)

Layout: one directory per source (`go/`, `ncbigene/`, ...) plus `reactome-uniprot-map/`, each
holding `data/` (the files), `MD5SUMS` (`cd cached_raw_data/go && md5sum -c MD5SUMS` works) and
`META.json`. `python3 -m rdfc2im cache list|verify|save|restore` manages it directly.

Two guards, both because of failures this project has had:

- **Copies are verified.** Plain `cp` has silently turned a file into the same number of NUL bytes
  on the virtiofs workspace mount. Every copy into or out of the cache hashes what it read,
  re-reads what it wrote and compares before renaming into place; a bad copy is retried, then
  fails the build - it never leaves a wrong file under the real name. Restore also checks the
  cached files against `MD5SUMS` first.
- **A cache records what it was fetched for** - scope mode, taxon, and the md5 of the gene panel
  (for `pubmed`, of the cited-PMID list) - and a restore whose build needs something different is
  refused, rather than quietly building a different mine from the wrong data.

If the cache is missing, incomplete or corrupt, `--use-cached-data` stops with the reason; drop
the option to fetch afresh and re-save.

### The phases

| # | Phase | What it does |
|---|---|---|
| 1 | `check_prereqs` | Tool checks, JDK 8, `/micklem`, `~/.gradle/gradle.properties`, source-list validation |
| 2 | `fetch_inputs` | `tools/make-inputs.sh`: clones rdf-config, intermine, humanmine, humanmine-bio-sources; fetches the live HumanMine model |
| 3 | `rdfc2im_pipeline` | Per source: `translate` → `fetch` → `tsv` → `items`. The RDF extraction. |
| 4 | `rdfc2im_project` | `project` → `check` → `make fork-sync` (project.xml, keys, additions, priorities) |
| 5 | `build_humanmine_items` | Builds and installs the `bio-source-humanmine-items` loader jar |
| 6 | `build_reactome_source` | Builds stock `bio-source-reactome` (converter only, for now) and downloads `UniProt2Reactome.txt` |
| 7 | `prepare_mine_checkout` | Copies and patches the humanmine checkout; writes `~/.intermine/humanmine.properties`; trims project.xml to the sources actually loaded |
| 8 | `stage_src_data` | Symlinks each `out/<src>/items/<src>.xml` into `/micklem/data/rdfc2im/<src>/` |
| 9 | `start_databases` | `docker compose up -d postgres solr`; creates the three databases and two Solr cores |
| 10 | `build_dbmodel` | `:dbmodel:builddb` and `:dbmodel:buildUserDB`; the reduced-mine trims |
| 11 | `integrate_sources` | `:dbmodel:integrate` per source, in project.xml's own order |
| 12 | `postprocess` | `create-references`, `create-attribute-indexes`, `ReactomePostProcess`, the Solr index (build → schema fix → clear → rebuild), autocomplete, objectstore summary |
| 13 | `build_webapp` | `:webapp:war` |
| 14 | `resolve_bluegenes_deps` | Resolves `org.intermine:bluegenes:1.4.5` to its 178 runtime jars |
| 15 | `stage_artifacts` | `tools/trial-stage.sh`: explodes the war, patches its baked-in DB/Solr hosts to the compose network's |
| 16 | `start_mine_stack` | `docker compose up -d mine bluegenes`, then restarts the webapp (it reads the DB once, at startup) |
| 17 | `apply_templates` | Loads `curation/demo_public_templates.sql` into the userprofile DB; restarts the webapp |
| 18 | `verify` | `/service/version`, `/service/model`, BlueGenes' root |

Load order in phase 11 is read back out of `project.xml` rather than re-decided, because the
`alongside` sources (`uniprot`, `reactome`) must integrate *after* the stock source they
supplement or they duplicate instead of merging.

## Watching a build

Every log line goes to the terminal and to `.build-logs/build-<run-id>.log` (in the repository,
gitignored; override with `LOG_DIR`), timestamped
and tagged with the current phase. Alongside it the script writes `build-<run-id>.status` (one
line: the current phase) and `build-<run-id>.timing.tsv` (one row per completed phase).

```sh
tail -f .build-logs/build-*.log                           # raw
tools/watch-build.sh                                      # per-phase dashboard
```

`tools/watch-build.sh` reads only those files and the phase order from `full-build.sh
--list-phases`, so it cannot drift from the build or affect it. It takes `--run-id`, `--once`
and `--interval N`. Because the logs live in the repository, the same command works from a host
terminal watching a build that runs in a sandbox sharing the checkout. If the build used a
different `LOG_DIR`, set the same one when you watch.

Every run ends by printing its own phase timings, longest first.

## Time and resources

### Timings

These are real numbers from 2026-09-21, from a run whose Maven, Gradle and Docker caches were
already warm. **A first-ever run is substantially slower**, because phases 2, 5, 6, 13 and 14
are then downloading the upstream checkouts, the Gradle wrapper distribution, the InterMine jar
tree, BlueGenes' 178 jars and four Docker images rather than finding them cached.

The mine build and load, `--from prepare_mine_checkout` through `verify`, took **4 minutes**
end to end (`build-20260921T134218Z`):

```
125s  integrate_sources
 68s  postprocess
 17s  build_dbmodel
 12s  build_webapp
  7s  verify
  4s  stage_artifacts
  1s  each: start_databases, start_mine_stack, resolve_bluegenes_deps, apply_templates, check_prereqs
  0s  stage_src_data, prepare_mine_checkout
```

On the first full-scale integrate of all ten sources the same two phases took 234 s and 176 s
respectively - `integrate_sources` and `postprocess` dominate in every run.

`rdfc2im_pipeline` (phase 3, the RDF extraction) is the other big one, and is almost entirely
time spent waiting on remote SPARQL endpoints. From the run that exercised it, eight of the nine
sources took **10 min 24 s** between them, with `pubmed`'s PMID-scoped fetch on top - call it
about a quarter of an hour:

```
clinvar      4m39s        reactome     1m13s        ncbigene       20s
gwascatalog  1m15s        go           1m03s        ensembl         8s
uniprot        56s        hgnc           50s        pubmed       (varies with the cited-PMID set)
```

### A second, from-scratch run

Run on 2026-09-21 in a sandbox whose only carry-over was the repository and the package caches.
It reached `verify` after **ten** fixes, each with a test that failed beforehand
(`make test`: 108 passed / 6 failed at the start; 152 passed / 17 skipped at the end), and the
result passed all 17 checks of `make test-live`: 113 genes, 2,883 pathways, 50,283
pathway-protein rows, 450 gene-pathway rows. Phase timings from the runs that completed them:

```
530s  rdfc2im_pipeline    (network: 9 sources, 10 min)      75s  postprocess
233s  integrate_sources                                       29s  resolve_bluegenes_deps
 15s  build_dbmodel        11s  build_webapp                   8s  verify / 8s fetch_inputs
```

What it found - most of it invisible to every earlier run, because those had been run once and
then resumed, or on a workspace already in the right state:

- **Copying on the workspace mount.** `make-inputs.sh` died in phase 2 twice: `cp -r` of a clone
  failed on its read-only `.git` pack, and its replacement `tar | tar` failed with "Directory
  renamed before its status could be extracted". It now copies through `tools/copy_tree.py`,
  which re-reads what it wrote.
- **`rdfc2im fetch` exited 0 when queries failed**, so a missing or truncated table went on to
  the mine (and would have been cached). It now exits 1.
- **`--sources` did not reach `rdfc2im project`**, so stale derived output from sources nobody
  asked for (six, in this workspace) landed in `project.xml` and the mine's model.
- **`Gene.pathways` without its `Pathway.genes` end** made phase 5's isolated `mergeModels` fail;
  `rdfc2im check` now flags any reverse-reference whose other end is missing.
- **Link fields were never carried**, so `Protein.keywords` (declared by the stock uniprot
  source that a reduced mine drops) did not exist when uniprot loaded. The general fix replaces
  three earlier hand-patches (`Protein.ecNumbers`, `Allele.diseases`, `Pathway.dataSets`).
- **A stale generated model looked like success.** Gradle called `:dbmodel:builddb` up to date
  after the loader jar's *content* changed, so a resume kept the old model. Phase 10 now deletes
  `dbmodel/build` first.
- **The first `:webapp:war` of a fresh checkout deleted its own `web.xml`** (Gradle's stale-output
  cleanup on a task with no history), and passed on the second try. Phase 13 now runs
  `copyWebappContent` once first.
- **Test tooling:** `make test` ran the live tests against any mine that happened to be
  listening, and `make` re-ran failed tests under a runner that cannot skip.

Reusing the fetched data: the same phase 3 with `--use-cached-data` took **73 s instead of 530 s**,
made no network requests, and produced items files byte-identical to the fresh fetch's (all nine).

### A third run: one command, start to finish, entirely from the cache

Every run above was assembled from `--from`/`--only` segments, resumed after each fix - useful for
isolating a bug, but not proof the *whole* script runs clean in one go. On 2026-09-22, a single
`tools/full-build.sh --use-cached-data` (no `--from`/`--only`, a brand new `TRIAL_HOME`) went from
nothing to a verified mine in **12 min 5 s**, all fetched data restored from `cached_raw_data/`
with no network requests beyond `fetch_inputs`' git pulls and the live model, and passed all 17
`make test-live` checks with the same counts as the from-scratch run above:

```
354s  postprocess           24s  build_dbmodel          3s  stage_artifacts
229s  integrate_sources     14s  build_humanmine_items   2s  start_mine_stack
 64s  rdfc2im_pipeline       9s  build_webapp             1s  each: build_reactome_source,
 12s  fetch_inputs           6s  verify                     resolve_bluegenes_deps, start_databases
  4s  rdfc2im_project        1s  prepare_mine_checkout      0s  each: check_prereqs, stage_src_data,
                                                               apply_templates
```

One real bug surfaced getting here, worth recording because it can recur whenever a cache's `--meta`
contract changes: `cached_raw_data/reactome-uniprot-map/` had been saved by an earlier commit,
before `--meta levels=...` existed. The very first single-command attempt died in phase 6 -
`rdfc2im cache: reactome-uniprot-map: cached data was fetched for levels=None, but this build needs
levels='default' - refusing to restore it` - correct behaviour (the recorded meta genuinely didn't
say what level it was), but with no migration path for a cache saved before the key existed. Since
`cached_raw_data/` is gitignored and local to each sandbox, this cannot affect a fresh clone (every
future save already includes `levels`) - only this one already-fetched entry needed a one-time fix:
its `META.json` was hand-edited to add `"levels": "default"`, true of the data it actually held (its
own recorded `url` ends in `UniProt2Reactome.txt`, the default file), then `rdfc2im cache verify`
confirmed the entry still checksums correctly before trusting it.

### Disk

Budget **5 GB**, and leave real headroom on whichever filesystem holds `/var/lib/docker`.
Measured after a complete build:

| | |
|---|---|
| `in/.upstream` (upstream checkouts) | 150 MB |
| `out/` (generated items, TSV, queries) | 257 MB |
| `trial/artifacts` (exploded war + BlueGenes jars) | 177 MB |
| `$TRIAL_HOME` (mine checkout, bgdeps, logs) | 264 MB |
| `/micklem` (staged source data) | 42 MB |
| Docker images (postgres 14, solr 8.6.2, tomcat 8.5, temurin 17) | ~2.5 GB |
| Docker volumes (`rdfc2im-pgdata`, `rdfc2im-solrdata`) | 1-1.6 GB |

### Memory

Peak around **9 GB of 15 GB** during `integrate_sources`. The Gradle daemon is given 4 GB, the
Tomcat container 3 GB (`-Xmx3g`), and Postgres runs with `max_connections=2000` - InterMine's
XML loader opens far more concurrent connections than you would expect, and the long-lived
Gradle daemon does not appear to release them between per-source `integrate` invocations.

## Verifying the result

`phase_verify` only checks that the three services answer. The real check is:

```sh
make test-live
```

`tests/test_live_mine.py` asks the running mine real questions - autocomplete populated for
Gene and Protein, keyword search returning a real panel gene, a panel-sized Gene table (not
~193,000 genes, which is what the full-vs-panel scope bug looked like), cross-source identifier
merge on one gene, `Pathway` → `Protein` participants and `Gene.pathways`, and the classic UI's
webconfig validity. Each check is anchored to a specific bug found on 2026-09-21 and named in
its docstring.

This suite exists because of the failure mode this project kept hitting: **a step that reports
success while silently doing nothing.** A Gradle task returning SUCCESS having indexed nothing;
a source postprocessor whose failure `PostProcessPlugin` catches, prints as one line, and
carries on from with exit code 0; an `integrate` loop whose regex silently skipped the stock
reactome source entirely, so nine sources loaded cleanly and reactome's data simply never
arrived. None of those are visible to an offline test, and several of them survived every
dry run. The live tests are opt-in - `make test-live` sets `RDFC2IM_LIVE=1`, and they skip if no
mine is reachable - so plain `make test` stays green offline and ignores a stale mine left
running from an earlier session. Point them elsewhere with
`MINE_BASE=http://host:8090/humanmine SOLR_BASE=http://host:8983 make test-live`.

Then look at it: `http://localhost:8090/humanmine` (classic UI, with the demo templates on the
home page) and `http://localhost:5000` (BlueGenes).

## Troubleshooting

**`FATAL: JAVA8_HOME not set and no JDK 8 found under /usr/lib/jvm`** - exactly what it says,
and it aborts in phase 1 before any work. Install `openjdk-8-jdk-headless`, or set `JAVA8_HOME`
if your JDK 8 is somewhere non-standard. If you get past this phase but a Gradle call later
reports it cannot start a daemon, you are running Gradle 4.9 under a newer JVM anyway - check
that `JAVA8_HOME` really points at an 8.

**`FATAL: no .venv at ... - create one with pyyaml+lxml first`** - see Prerequisites. The venv
must be at the repository root.

**Gradle appears to hang during `integrate_sources`, CPU pinned, no log output.** That is the
OOM death-spiral: an `OutOfMemoryError` in a background prefetch thread that the daemon never
surfaces. Check `~/.gradle/gradle.properties` actually contains
`org.gradle.jvmargs=-Xmx4096m -Dfile.encoding=UTF-8`. The script only writes that file if it has
no `org.gradle.jvmargs` line at all, so a pre-existing line with a smaller heap is left alone
and is the thing to fix.

**`invalid byte sequence for encoding UTF8: 0x00` during reactome's load** - the same file, the
`-Dfile.encoding=UTF-8` half. See Prerequisites.

**Postgres dies mid-build and will not restart; `No space left on device` in its log.** The
Docker filesystem filled. This happened for real on 2026-09-21: an invalid `webconfig-model.xml`
made every classic-UI page log a full Struts/Tiles stack trace and every failing DWR call log
another, Tomcat's logs in the `rdfc2im-mine` container reached 3.5 GB
(`localhost.<date>.log` 2.3 GB, `ddi-ws.log` 1.3 GB), `/var/lib/docker` hit 100%, and Postgres
died mid-WAL-redo and could not come back until space was freed. Both causes are now fixed -
`reduce_mine.py webconfig` trims the widgets that cannot resolve, and
`/usr/local/tomcat/logs` is a 512 MB tmpfs so an error storm fails at the cap instead of eating
the host disk - but the log directory is still the first place to look if the mine starts
misbehaving:

```sh
docker exec rdfc2im-mine du -sh /usr/local/tomcat/logs
docker exec rdfc2im-mine ls -lS /usr/local/tomcat/logs | head
df -h /var/lib/docker
```

Those logs are deliberately not durable across a container restart.

**Every request 500s with `ContextNotInitialisedException`, however long you wait.** The
userprofile database has no schema, so the webapp's `ActionServlet` never initialises
(`userprofileOSW is null`). `:dbmodel:buildUserDB` is a separate task from `:dbmodel:builddb`;
re-run `--only build_dbmodel`.

**The webapp deploys, Tomcat logs success, and every request 404s.** The war was built without
`WEB-INF/web.xml`, so it has no servlet mappings and nothing logs an error.
`phase_prepare_mine_checkout` patches `webapp/build.gradle` to fix this; if you are working from
a hand-made checkout, that patch is the thing to reproduce.

**Phase 2 (`fetch_inputs`) dies copying a clone: `cp: failed to extend ... .git/objects/pack/...:
Permission denied`, or `tar: .: Directory renamed before its status could be extracted`.** You are on
a virtiofs-backed workspace mount, where `cp` and `tar` are not trustworthy for trees. `make-inputs.sh`
no longer uses either - it copies through `tools/copy_tree.py`, which re-reads and compares every file
- so this means an older copy of the script. If you write your own copy step on such a mount, do the
same; see `RESTART.md`.

**`--use-cached-data` stops with `cached data ... refusing to restore it`, `... is corrupt in the
cache`, or `no cached data for ...`.** Working as intended: the cache was fetched for a different gene
panel, taxon, scope or `--reactome-levels` than this build needs, a cached file no longer matches its
`MD5SUMS`, or nothing was cached yet. `python3 -m rdfc2im cache list` and `cache verify` show what is
there. Run once without the option to fetch afresh (which re-saves) - or, if the entry's actual data is
right and only its recorded `META.json` predates a newer `--meta` key rdfc2im/rawcache.py now checks
(confirmed live for `levels`, see "A third run" above), edit `META.json`'s `meta` object by hand to add
the missing key with its true value, then confirm with `cache verify` before trusting it.

**Something reported success but the data is not there.** Assume this is happening rather than
assume it is not - it was the single most common failure of the first real build. Run
`make test-live`, and check the claim directly with a query or a row count before believing it.

**Resuming.** Fix the cause, then `tools/full-build.sh --from <phase>`. The phases are designed
to be resumable: the reactome data download, the upstream checkouts and the mine checkout copy
are all skipped when already present.

## Persistence, and what does not survive

**The loaded mine lives in Docker named volumes** - `rdfc2im-pgdata` and `rdfc2im-solrdata`.
`docker compose -f trial/docker-compose.yml down` stops the containers and keeps the volumes;
`down -v` throws them away, and so does destroying the machine. There is no other copy: the
mine is not backed up anywhere, and rebuilding it means running this script again. (The
Makefile's `up`/`down`/`status`/`logs`/`trial-destroy` targets do the same things but default to
`podman compose`; pass `COMPOSE="docker compose -f trial/docker-compose.yml"` to use them here.)

To keep the loaded data independently of the containers:

```sh
docker exec rdfc2im-postgres pg_dump -U intermine humanmine-production      > humanmine-production.sql
docker exec rdfc2im-postgres pg_dump -U intermine humanmine-userprofile     > humanmine-userprofile.sql
```

The userprofile dump is the one that carries the public demo templates; `humanmine-items` is
scratch and not worth keeping. The Solr index is derived data - rebuild it with
`--only postprocess` rather than dumping it.

## See also

- `tools/full-build.sh` header - the authoritative option and environment reference, plus a
  comment per phase explaining why each workaround exists.
- `LOAD-TRIAL.md` - the chronological record of every one of those findings being made by hand.
- `USAGE.md` - the `rdfc2im` command reference, and build-scope flags in detail.
- `STATUS.md` - per-source state, mapping decisions, and what is still open.
- `RESTART.md` - how to pick this project up cold.
