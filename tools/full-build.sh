#!/usr/bin/env bash
# Full build: RDF Portal -> a running, fully-configured demonstration HumanMine.
#
# Encodes, as one script, the sequence LOAD-TRIAL.md records happening by hand over many
# sessions: the rdfc2im pipeline per source, the InterMine mine build, postprocessing, Solr,
# BlueGenes, and the demo templates. This is the paper's #1 Future Work item ("Script the whole
# build"). See RESTART.md and STATUS.md's "Where this stands" for the wider project context.
#
# Usage:
#   tools/full-build.sh [options]
#
# Options:
#   --dry-run           Print every command instead of running it. Always start here.
#   --from PHASE        Start at PHASE instead of the beginning (see --list-phases).
#   --only PHASE        Run exactly one phase, then stop.
#   --list-phases       Print the phase names, in order, and exit.
#   --sources "a b c"   Space-separated source list (default: the 9-source demo panel).
#   --genes FILE        Gene-panel file for --genes-scoped sources (default: demo panel).
#   --taxon LIST        Comma-separated NCBI taxon ids, passed to rdfc2im --taxon (default: 9606).
#   --skip-templates    Don't apply curation/demo_public_templates.sql.
#   -h, --help          This text.
#
# Every side-effecting external command (gradle, docker, psql, curl) is emitted through `run`,
# which is a no-op printer under --dry-run - so --dry-run is a real dry run of the *exact*
# command sequence, not a separate code path that could drift from what actually executes.
#
# Environment (all overridable; defaults match this project's own trial setup):
#   TRIAL_HOME        Scratch directory for the humanmine/humanmine-bio-sources checkouts and
#                     gradle build outputs. Not the rdfc2im repo - keep build noise separate.
#                     Default: ~/intermine-build/trial_home (matches .trial-home if present).
#   UPSTREAM_CACHE    Where tools/make-inputs.sh clones rdf-config/intermine/humanmine/
#                     humanmine-bio-sources from. Default: in/.upstream (see that script).
#   JAVA8_HOME        A JDK 8 install. Gradle 4.9 (this mine's build tooling) cannot start a
#                     daemon under anything newer - confirmed live, see LOAD-TRIAL.md's hgnc
#                     section. No default; the script fails fast in phase_check_prereqs if unset
#                     and not discoverable.
#   GRADLE49          Path to a Gradle 4.9 launcher (the mine checkout's own ./gradlew is used
#                     when present, matching every invocation recorded in LOAD-TRIAL.md; this is
#                     a fallback only).
#   COMPOSE           Docker Compose invocation. Default: "docker compose" (this project's own
#                     trial stack was built and proven against Docker specifically, not podman -
#                     see STATUS/handoff notes on this sandbox's environment).
#   PG_PORT, BG_PORT, SOLR_PORT   Host ports for the three published services. Defaults match
#                     trial/docker-compose.yml: 15432, 5000, 8983. NOTE: unlike PG_PORT and
#                     BG_PORT, the tracked compose file hardcodes Solr's port rather than reading
#                     ${SOLR_PORT} - changing SOLR_PORT here only takes effect via the BIND_ADDR
#                     override path below (which does parameterize it); at the default BIND_ADDR
#                     it's cosmetic and Solr stays on 8983 regardless.
#   BIND_ADDR         Host address the compose stack's ports are published on. Default 127.0.0.1
#                     (matches the tracked trial/docker-compose.yml - reach it via `ssh -L`, see
#                     LOAD-TRIAL.md "Connecting"). Set to 0.0.0.0 only if you know your sandbox's
#                     access model needs it (e.g. `sbx ports` rather than SSH) - this writes a
#                     LOCAL, untracked compose override, never the shared tracked file.
#   MINE_TITLE        Display name baked into the webapp and BlueGenes. Default matches the demo
#                     panel; override for a differently-scoped build.
#
# phase_resolve_bluegenes_deps was a reasoned reconstruction (LOAD-TRIAL.md recorded only the
# *result* of resolving BlueGenes' Clojure dependencies - "178 jars" - not the exact command) -
# now confirmed live (2026-09-21, outside this script, before any real build attempt): the
# throwaway-project approach below, run standalone with Gradle 4.9 / JDK 8, resolves
# org.intermine:bluegenes:1.4.5 from Clojars + Maven Central to exactly 178 jars, matching
# LOAD-TRIAL.md's number exactly. No longer a guess.
set -euo pipefail

# ============================================================== configuration
HERE=$(cd "$(dirname "$0")/.." && pwd)
cd "$HERE"

TRIAL_HOME=${TRIAL_HOME:-}
if [ -z "$TRIAL_HOME" ] && [ -f "$HERE/.trial-home" ]; then TRIAL_HOME=$(cat "$HERE/.trial-home"); fi
TRIAL_HOME=${TRIAL_HOME:-"$HOME/intermine-build/trial_home"}
UPSTREAM_CACHE=${UPSTREAM_CACHE:-"$HERE/in/.upstream"}
JAVA8_HOME=${JAVA8_HOME:-}
GRADLE49=${GRADLE49:-gradle}
COMPOSE=${COMPOSE:-"docker compose"}
PG_PORT=${PG_PORT:-15432}
BG_PORT=${BG_PORT:-5000}
SOLR_PORT=${SOLR_PORT:-8983}
BIND_ADDR=${BIND_ADDR:-127.0.0.1}
MINE_TITLE=${MINE_TITLE:-"rdfc2im: 113 gene food/drug-metabolism panel"}

# The demo panel this project actually built and verified this session (STATUS.md Table 2 /
# LOAD-TRIAL.md). Each source's scope mode decides which rdfc2im CLI flags it gets in
# phase_rdfc2im_pipeline - see that function. Override with --sources for a different build.
SOURCES=${SOURCES:-"go ncbigene reactome hgnc ensembl uniprot clinvar gwascatalog pubmed"}
GENE_PANEL=${GENE_PANEL:-"$HERE/curation/demo_gene_panel.txt"}
TAXON=${TAXON:-9606}
SKIP_TEMPLATES=0
DRY_RUN=0
FROM_PHASE=""
ONLY_PHASE=""

# Scope mode per source: full (no restriction), panel (--genes), pmid (--pmids from citing
# sources' own already-fetched data), none (not gene/organism-shaped - ontologies, pubmed itself
# takes --pmids separately). Matches STATUS.md Table 2 / LOAD-TRIAL.md exactly - do not add a
# source here without also adding its scope mode, or it silently gets a full, unscoped fetch.
declare -A SCOPE_MODE=(
  [go]=none [hpo]=none [mp]=none [uberon]=none [mesh]=none [homologene]=none [expressionatlas]=none
  [ncbigene]=panel [reactome]=full
  [hgnc]=panel [ensembl]=panel [uniprot]=panel [clinvar]=panel [gwascatalog]=panel
  [pubmed]=pmid
)

LOG_DIR="$TRIAL_HOME/logs"
RUN_ID=${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}
LOGFILE="$LOG_DIR/build-$RUN_ID.log"
TIMING_TSV="$LOG_DIR/build-$RUN_ID.timing.tsv"
STATUS_FILE="$LOG_DIR/build-$RUN_ID.status"   # one line, current phase - `watch cat` this for a live view

PHASES=(check_prereqs fetch_inputs rdfc2im_pipeline rdfc2im_project build_humanmine_items
        build_reactome_source prepare_mine_checkout stage_src_data start_databases build_dbmodel
        integrate_sources postprocess build_webapp resolve_bluegenes_deps stage_artifacts
        start_mine_stack apply_templates verify)

# ============================================================== logging & timing
# Every log line goes to both the terminal and $LOGFILE, timestamped, so `tail -f "$LOGFILE"`
# from another shell is a live view of a build in progress, and the file survives after the
# terminal is gone. $TIMING_TSV gets one row per phase (phase, start, end, seconds) for exactly
# the "learn about bottlenecks" use case - `sort -t$'\t' -k4 -rn "$TIMING_TSV"` after a run ranks
# phases by wall-clock cost.
_ts() { date -u +%H:%M:%S; }

log() { printf '%s [%s] %s\n' "$(_ts)" "${CURRENT_PHASE:-main}" "$*" | tee -a "$LOGFILE" >&2; }

die() { log "FATAL: $*"; exit 1; }

# Wraps every side-effecting command. Under --dry-run this only logs; otherwise it logs then
# execs, with the command's own stdout/stderr still tee'd into $LOGFILE so gradle/docker output
# is part of the same live-tailable log, not lost to a separate stream.
run() {
  log "+ $*"
  if [ "$DRY_RUN" -eq 1 ]; then return 0; fi
  "$@" 2>&1 | tee -a "$LOGFILE"
  return "${PIPESTATUS[0]}"
}

# Like `run`, but for a command that must run in a directory an earlier (possibly dry-run-skipped)
# step creates. Plain `( cd "$dir" && run ... )` still executes the real `cd` under --dry-run,
# which fails when that directory doesn't exist yet - found by actually dry-running this script,
# not by inspection. The whole cd-and-run is a no-op together under --dry-run instead.
run_in() {
  local dir=$1; shift
  log "+ (cd $dir && $*)"
  if [ "$DRY_RUN" -eq 1 ]; then return 0; fi
  ( cd "$dir" && "$@" ) 2>&1 | tee -a "$LOGFILE"
  return "${PIPESTATUS[0]}"
}

phase_start() {
  CURRENT_PHASE=$1
  PHASE_T0=$(date +%s)
  echo "$CURRENT_PHASE" > "$STATUS_FILE" 2>/dev/null || true
  log "===== START ====="
}

phase_end() {
  local dt=$(( $(date +%s) - PHASE_T0 ))
  log "===== DONE (${dt}s) ====="
  printf '%s\t%s\t%s\t%s\n' "$CURRENT_PHASE" "$(_ts)" "$dt" "$RUN_ID" >> "$TIMING_TSV"
}

# ============================================================== arg parsing
usage() { sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --from) FROM_PHASE=$2; shift ;;
    --only) ONLY_PHASE=$2; shift ;;
    --list-phases) printf '%s\n' "${PHASES[@]}"; exit 0 ;;
    --sources) SOURCES=$2; shift ;;
    --genes) GENE_PANEL=$2; shift ;;
    --taxon) TAXON=$2; shift ;;
    --skip-templates) SKIP_TEMPLATES=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

mkdir -p "$LOG_DIR"
: > "$TIMING_TSV" 2>/dev/null || true
log "full-build.sh starting - run id $RUN_ID, log $LOGFILE, dry_run=$DRY_RUN"
log "sources: $SOURCES"
log "trial home: $TRIAL_HOME"

GRADLE_ENV=()   # populated by phase_check_prereqs; every ./gradlew call below is prefixed with it

# ============================================================== phases
phase_check_prereqs() {
  for bin in python3 docker git curl unzip; do  # psql runs inside the postgres container, not on the host
    command -v "$bin" >/dev/null 2>&1 || die "missing required tool: $bin"
  done
  $COMPOSE version >/dev/null 2>&1 || die "'$COMPOSE' does not run - is Docker Compose installed?"
  if [ -z "$JAVA8_HOME" ]; then
    for cand in /usr/lib/jvm/java-8-openjdk-* /usr/lib/jvm/java-1.8.0*; do
      [ -d "$cand" ] && JAVA8_HOME=$cand && break
    done
  fi
  [ -n "$JAVA8_HOME" ] && [ -x "$JAVA8_HOME/bin/java" ] || die \
    "JAVA8_HOME not set and no JDK 8 found under /usr/lib/jvm - Gradle 4.9 cannot start a daemon
     under a newer JDK (confirmed live, LOAD-TRIAL.md's hgnc section). Install one and set
     JAVA8_HOME, e.g.: apt-get install -y openjdk-8-jdk && export JAVA8_HOME=/usr/lib/jvm/java-8-openjdk-\$(dpkg --print-architecture)"
  GRADLE_ENV=(env "JAVA_HOME=$JAVA8_HOME")
  log "JAVA8_HOME=$JAVA8_HOME"
  [ -f "$GENE_PANEL" ] || die "gene panel file not found: $GENE_PANEL"
  # A misspelled --sources entry falling through to SCOPE_MODE's ${...:-none} default would
  # silently fetch that source completely unscoped instead of erroring - exactly the kind of
  # mistake that would only surface hours later as an unexpectedly huge ClinVar/UniProt pull.
  # Fail fast, before any real work starts, on any name not in SCOPE_MODE at all.
  for src in $SOURCES; do
    [ -v "SCOPE_MODE[$src]" ] || die "unknown source '$src' - not in SCOPE_MODE (tools/full-build.sh); " \
      "known sources: ${!SCOPE_MODE[*]}"
  done
  mkdir -p "$TRIAL_HOME"
  # rdfc2im.yaml's src_data_dir (/micklem/data/rdfc2im - a real Micklem-lab deployment path,
  # deliberately not rewritten to somewhere sandbox-local; see phase_stage_src_data) lives under
  # a root-owned path on a fresh sandbox. One-time, idempotent: only touches it if not already
  # ours.
  if [ ! -w /micklem ] 2>/dev/null || [ ! -e /micklem ]; then
    run sudo mkdir -p /micklem
    run sudo chown "$(id -u):$(id -g)" /micklem
  fi
  # Gradle 4.9's daemon default heap (1024m) is not enough for InterMine's
  # ParallelBatchingFetcher, which spawns a worker thread per available CPU core, each doing its
  # own DataTracker.prefetchIds() - confirmed live: "OutOfMemoryError: GC overhead limit
  # exceeded" in a worker thread on the very first source's load. The exception is in a
  # background thread, not main, so the daemon doesn't fail cleanly - it spins in a GC
  # death-spiral, burning CPU with no forward progress, rather than exiting. GRADLE_USER_HOME's
  # own gradle.properties (default ~/.gradle) applies to every daemon Gradle starts from here,
  # across both the humanmine and humanmine-bio-sources checkouts.
  # file.encoding=UTF-8: the daemon's own default (US-ASCII) comes from this sandbox having no
  # LANG/LC_ALL set at all, which Java's platform-default-charset detection falls back to
  # US-ASCII for. Harmless for plain ASCII data, but confirmed live to actively corrupt real
  # data: reactome's own UniProt2Reactome.txt has non-ASCII bytes (Greek letters in pathway
  # names, normal UTF-8), and BioFileConverter reads files via a platform-default-charset
  # Reader (no explicit UTF-8) - under US-ASCII those bytes get mis-decoded, and one specific
  # mis-decode produced a literal NUL byte, which Postgres's COPY then rejected outright:
  # "invalid byte sequence for encoding UTF8: 0x00".
  local gradle_props="$HOME/.gradle/gradle.properties"
  if ! grep -q "^org.gradle.jvmargs" "$gradle_props" 2>/dev/null; then
    write_file "$gradle_props" "org.gradle.jvmargs=-Xmx4096m -Dfile.encoding=UTF-8"
  fi
  log "prerequisites OK"
}

phase_fetch_inputs() {
  # rdf-config, intermine, humanmine, humanmine-bio-sources, the live HumanMine model, and
  # HumanMine's own genomic_priorities.properties - see tools/make-inputs.sh's own header.
  run env CACHE="$UPSTREAM_CACHE" sh tools/make-inputs.sh
}

# Emits the rdfc2im scope flags for one source, per SCOPE_MODE. Kept as one function so the
# per-source scoping rule lives in exactly one place, matching this project's own "general fix,
# not a special case" preference (see RESTART.md's process notes).
scope_flags_for() {
  local src=$1
  case "${SCOPE_MODE[$src]:-none}" in
    full)  printf -- '--taxon %s' "$TAXON" ;;
    panel) printf -- '--taxon %s --genes @%s' "$TAXON" "$GENE_PANEL" ;;
    pmid)  printf -- '' ;;  # pubmed's --pmids needs other sources' PMIDs already loaded; see integrate_sources
    none)  printf -- '' ;;
  esac
}

phase_rdfc2im_pipeline() {
  local venv_activate="$HERE/.venv/bin/activate"
  [ -f "$venv_activate" ] || die "no .venv at $venv_activate - create one with pyyaml+lxml first"
  # shellcheck disable=SC1090
  source "$venv_activate"
  for src in $SOURCES; do
    local flags; flags=$(scope_flags_for "$src")
    log "-- $src (scope=${SCOPE_MODE[$src]:-none}) --"
    run python3 -m rdfc2im translate --source "$src" $flags
    if [ "$src" = "pubmed" ]; then
      # PMID-scoped to the union of PMIDs the panel's own already-loaded data cites (hgnc's
      # main_reference, uniprot's main_citation, gwascatalog, reactome) - see LOAD-TRIAL.md's
      # pubmed section. Requires those sources' items to already exist on disk.
      local pmid_file="$TRIAL_HOME/cited_pmids.txt"
      # -a forces text mode: without it, grep inside this `bash -c` context (unlike an interactive
      # shell) mis-detects these UTF-8 items XML files as binary and reports "binary file matches"
      # with zero actual matches - confirmed live, silently produced an empty cited_pmids.txt, which
      # made cli.py's `if pmids and pmid_field` (falsy on []) fall through to an unrestricted,
      # LIMIT-0 fetch of all of PubMed instead of the intended PMID-scoped one.
      run bash -c "grep -ahoE '\"[0-9]{4,9}\"' out/{hgnc,uniprot,gwascatalog,reactome}/items/*.xml 2>/dev/null \
                    | tr -d '\"' | sort -u > '$pmid_file'"
      run python3 -m rdfc2im fetch --source pubmed --limit 0 --force --pmids "@$pmid_file"
    else
      run python3 -m rdfc2im fetch --source "$src" --limit 0 --force $flags
    fi
    run python3 -m rdfc2im tsv --source "$src"
    run python3 -m rdfc2im items --source "$src"
  done
}

phase_rdfc2im_project() {
  source "$HERE/.venv/bin/activate"
  run python3 -m rdfc2im project
  # `check` needs out/_mine/project.xml (rdfc2im check -> check_project(..., out/_mine, ...)), which
  # `project` above is what creates - USAGE.md's own documented order is project -> check -> docs.
  # Originally called at the end of phase_rdfc2im_pipeline, before project.xml existed - confirmed
  # live: "check: no project.xml - run `rdfc2im project` first", exit 2. --dry-run never runs `check`
  # for real, so this ordering bug survived every prior dry-run test.
  run python3 -m rdfc2im check
  run make fork-sync
}

phase_build_humanmine_items() {
  local bio="$TRIAL_HOME/humanmine-bio-sources"
  [ -d "$bio" ] || run cp -r "$UPSTREAM_CACHE/humanmine-bio-sources" "$bio"
  # The dead JCenter/Bintray plugin in bio-source-humanmine-static fails Gradle's config phase
  # for every sibling project (confirmed live, LOAD-TRIAL.md). Not needed for this source list.
  if grep -q "^[^#]*bio-source-humanmine-static" "$bio/settings.gradle" 2>/dev/null; then
    run sed -i.bak "s/^\(.*bio-source-humanmine-static.*\)$/\/\/ \1  # disabled by full-build.sh: dead JCenter plugin/" \
        "$bio/settings.gradle"
  fi
  if ! grep -q "bio-source-humanmine-items" "$bio/settings.gradle" 2>/dev/null; then
    # An absolute path, not a relative guess up from $bio: the original '../../../humanmine-items'
    # assumed TRIAL_HOME sits a fixed depth alongside the rdfc2im checkout as a sibling directory.
    # Confirmed live that assumption doesn't hold here (TRIAL_HOME under ~/intermine-build/, the
    # checkout under /Users/gos/git/rdfc2im/ - unrelated trees): it resolved to
    # /home/agent/humanmine-items, which doesn't exist - "Basedir ... does not exist", Gradle
    # initConfig FAILED. $HERE is already known precisely, so use it directly instead of guessing.
    run bash -c "cat >> '$bio/settings.gradle' <<GRADLE

include ':bio-source-humanmine-items'
project(':bio-source-humanmine-items').projectDir = new File('$HERE/humanmine-items')
GRADLE"
  fi
  run_in "$bio" "${GRADLE_ENV[@]}" ./gradlew :bio-source-humanmine-items:install
}

# Builds and installs org.intermine:bio-source-reactome:5.0.8 into the local Maven repo, in one
# of two modes - the same project, built twice, because of a genuine chicken-and-egg:
#
#   converter-only  ReactomeConverter alone. Needed BEFORE :dbmodel:builddb, because
#                   addSourceDependencies resolves bio-source-<type> for every source in
#                   project.xml, and before integrate_sources, which actually runs the converter.
#                   ReactomeConverter itself only uses the generic Item API, so it compiles with
#                   no mine-specific classes - which is why this mode works this early.
#   with-postprocess  Adds ReactomePostProcess, which imports org.intermine.model.bio.Pathway.
#                   Confirmed live: Pathway is NOT in the stock bio-model jar (Gene and Protein
#                   are) - it is contributed by reactome_additions.xml, so it exists only in the
#                   mine's OWN generated dbmodel.jar, which does not exist until builddb has run.
#                   Hence the second build, from phase_postprocess.
#
# group/version match exactly what IntegratePlugin.groovy resolves for a project.xml
# <source type="reactome"> entry with no explicit version="..." attribute: "bio-source-" + type,
# at bioVersion (5.0.8, confirmed live - the same version already resolved and cached for
# bio-model/intermine-integrate elsewhere in this build).
_install_reactome_jar() {
  local mode=$1
  local build="$TRIAL_HOME/reactome-source"
  local dbmodel_jar="$TRIAL_HOME/humanmine/dbmodel/build/libs/dbmodel.jar"
  local src_java="java { srcDirs = ['src/main/java']; exclude '**/postprocess/**' }"
  local extra_dep=""
  if [ "$mode" = "with-postprocess" ]; then
    src_java="java { srcDirs = ['src/main/java'] }"
    extra_dep="    compile files('$dbmodel_jar')"
  fi
  write_file "$build/build.gradle" "$(cat <<GRADLE
apply plugin: 'java'
apply plugin: 'maven'

group = 'org.intermine'
version = '5.0.8'
sourceCompatibility = 1.8
targetCompatibility = 1.8

repositories {
    mavenLocal()
    mavenCentral()
}

sourceSets {
    main {
        $src_java
        resources { srcDirs = ['src/main/resources'] }
    }
}

processResources {
    from('.') { include('*.properties') }
}

dependencies {
    // bio-core provides org.intermine.bio.util.*/BioFileConverter - not in reactome's own
    // build.gradle dependencies block (confirmed live: compileJava failed without it, "package
    // org.intermine.bio.util does not exist") because intermine/bio's real root build.gradle
    // applies it to every bio/sources/* subproject via its own subprojects{} block; this
    // standalone build has no such parent, so it's declared explicitly here instead.
    compile group: 'org.intermine', name: 'bio-core', version: '5.0.8', transitive: false
    compile group: 'org.intermine', name: 'bio-model', version: '5.0.8', transitive: false
    compile group: 'org.intermine', name: 'intermine-integrate', version: '5.0.+'
    runtime fileTree(dir: 'libs', include: '*.jar')
$extra_dep
}
GRADLE
)"
  write_file "$build/settings.gradle" "rootProject.name = 'bio-source-reactome'"
  run_in "$build" "${GRADLE_ENV[@]}" "$GRADLE49" install
}

phase_build_reactome_source() {
  # Stock reactome (type="reactome", org.intermine:bio-source-reactome) - our own
  # humanmine-reactome only maps Pathway.description, no participants (unmapped/pruned
  # pathwayComponent link - sources.yaml's own note), so without this alongside source Pathway
  # never connects to any Protein or Gene. Its jar is equally dead on JCenter/Maven Central
  # (confirmed: 403 / 404) but unlike the ~39 sources dropped in phase_prepare_mine_checkout,
  # this one is worth building from the local source make-inputs.sh already cloned
  # (in/.upstream/intermine/bio/sources/reactome) - same "isolated standalone build, not the
  # whole dead-JCenter-riddled intermine/bio umbrella" trick as bio-source-humanmine-items.
  local src="$UPSTREAM_CACHE/intermine/bio/sources/reactome"
  local build="$TRIAL_HOME/reactome-source"
  [ -d "$build" ] || run mkdir -p "$build"
  run cp -r "$src/src" "$build/src"
  run cp -r "$src/libs" "$build/libs"
  run cp "$src/reactome.properties" "$build/reactome.properties"
  # group/version match exactly what IntegratePlugin.groovy resolves for a project.xml
  # <source type="reactome"> entry with no explicit version="..." attribute: "bio-source-" +
  # type, at bioVersion (5.0.8, confirmed live - the same version already resolved and cached
  # for bio-model/intermine-integrate elsewhere in this build).
  _install_reactome_jar converter-only

  # Data: reactome's own project.xml entry expects a plain UniProt-to-pathway mapping file
  # under src.data.dir (a directory, not a single named file - have.file.custom.tgt processes
  # whatever it finds there). ReactomeConverter.java confirms the exact 6-column tab-delimited
  # shape (accession, pathway id, url, pathway name, evidence code, organism name) this is -
  # Reactome's own public UniProt2Reactome mapping, not a full BioPAX dump. Fetched once, not
  # re-fetched on a --from resume - confirmed live: ~325k rows total, ~55k human, a few seconds
  # to download, not worth repeating.
  local dest_dir="/micklem/data/reactome/current"
  run mkdir -p "$dest_dir"
  if [ "$DRY_RUN" -eq 1 ] || [ ! -s "$dest_dir/UniProt2Reactome.txt" ]; then
    run curl -fsSL -o "$dest_dir/UniProt2Reactome.txt" https://reactome.org/download/current/UniProt2Reactome.txt
  fi
}

# Writes $2 to file $1, or just logs the intent under --dry-run - a real dry run must not touch
# $HOME or the working tree. `run cmd > file` does NOT achieve this on its own: bash opens a
# bare redirect on the *caller's* command line before `run` is even invoked, so the file gets
# created (or the write attempted) regardless of DRY_RUN - found by dry-running this script, not
# by inspection. Route every generated-file write through this instead of a bare `>`.
write_file() {
  local dest=$1 content=$2
  log "+ write $dest ($(printf '%s' "$content" | wc -l) lines)"
  [ "$DRY_RUN" -eq 1 ] && return 0
  mkdir -p "$(dirname "$dest")"
  printf '%s\n' "$content" > "$dest"
}

# COMPOSE_FILES: computed globally, not inside phase_start_databases, and unconditionally (not
# gated on DRY_RUN) - both phase_start_databases and phase_start_mine_stack need it, and a
# `--from`/`--only` resume can run either one without the other having run first in THIS
# invocation. A phase-local variable set in one phase and read in another breaks the moment a
# resume skips the setter - confirmed live: `--from stage_artifacts` hit start_mine_stack with
# an empty COMPOSE_FILES (never set this run), so `docker compose up` ran with no -f flags at
# all and failed "no configuration file provided: not found". BIND_ADDR/PG_PORT/etc are plain
# config, known from script start regardless of resume point, so this has everything it needs
# without depending on any phase having already run.
COMPOSE_FILES=(-f trial/docker-compose.yml)
if [ "$BIND_ADDR" != "127.0.0.1" ]; then
  write_file "trial/docker-compose.local.yml" "$(cat <<YML
# Untracked, sandbox-local override generated by full-build.sh (BIND_ADDR=$BIND_ADDR).
# Never commit this - the tracked trial/docker-compose.yml deliberately binds 127.0.0.1
# and expects ssh -L; this is for sandboxes that publish ports a different way instead.
#
# !override (not a plain list): Compose merges \`ports:\` across -f files by concatenating,
# not replacing - confirmed live, every service ended up with BOTH the base file's 127.0.0.1
# binding AND this one simultaneously, and since 0.0.0.0 already covers 127.0.0.1, the second
# bind failed with "address already in use" ("docker compose ... up" died before any container
# actually started). \`!override\` (Compose spec's sequence-merge tag) replaces the list instead.
services:
  postgres: {ports: !override ["$BIND_ADDR:$PG_PORT:5432"]}
  solr:     {ports: !override ["$BIND_ADDR:$SOLR_PORT:8983"]}
  mine:     {ports: !override ["$BIND_ADDR:8090:8090"]}
  bluegenes: {ports: !override ["$BIND_ADDR:$BG_PORT:5000"]}
YML
)"
  COMPOSE_FILES+=(-f "trial/docker-compose.local.yml")
fi

phase_prepare_mine_checkout() {
  local mine="$TRIAL_HOME/humanmine"
  # shellcheck disable=SC1090
  source "$HERE/.venv/bin/activate"   # tools/reduce_mine.py below needs lxml
  [ -d "$mine" ] || run cp -r "$UPSTREAM_CACHE/humanmine" "$mine"
  # webapp/build.gradle pulls the Gretty plugin from JCenter/Bintray (dead since 2021, same class
  # of problem already worked around for bio-source-humanmine-static) - confirmed live: 403
  # Forbidden from jcenter.bintray.com, which fails Gradle's CONFIGURE phase for the whole
  # humanmine project (every subproject is evaluated up front, so this breaks :dbmodel:builddb
  # too, not just :webapp). Gretty is only used for its own embedded-Jetty dev-convenience tasks
  # (`gretty run`) - nothing in `war`'s own dependsOn graph needs it, and this build's actual
  # deployment goes through the Tomcat container (trial/docker-compose.yml), never gretty. Safe
  # to disable: comment out the plugin apply and its now-orphaned `gretty {}` config block.
  if grep -q "^apply from:.*gretty.plugin" "$mine/webapp/build.gradle" 2>/dev/null; then
    run sed -i.bak \
      -e 's|^\(apply from:.*gretty\.plugin.*\)$|// \1  # disabled by full-build.sh: dead JCenter plugin|' \
      -e '/^gretty {/,/^}/{ s/^/\/\/ /; }' \
      "$mine/webapp/build.gradle"
  fi
  # dbmodel/build.gradle's soTermListFilePath/soAdditionFilePath are relative strings, passed
  # straight into an Ant task (DBModelPlugin.groovy: `ant.createSoModel(soTermListFile:
  # config.soTermListFilePath, outputFile: config.soAdditionFilePath)`) with no project.file(...)
  # resolution first. Ant resolves a relative path against the JVM's OS-level `user.dir`, which
  # for a Gradle DAEMON process is fixed at $GRADLE_USER_HOME/daemon/<version>/ for the daemon's
  # whole lifetime, not the calling client's directory - confirmed live: createSoModel failed
  # looking for ".../daemon/4.9/dbmodel/resources/so_terms" (that exact prefix), even against a
  # freshly restarted daemon started from the right directory. A genuine bug in intermine's own
  # gradle plugin, not rdfc2im or full-build.sh; making both paths absolute here avoids it
  # without disabling the daemon for every other ./gradlew call this script makes.
  if grep -q '^\s*soTermListFilePath = "dbmodel/resources/so_terms"' "$mine/dbmodel/build.gradle" 2>/dev/null; then
    run sed -i.bak \
      -e "s|soTermListFilePath = \"dbmodel/resources/so_terms\"|soTermListFilePath = \"$mine/dbmodel/resources/so_terms\"|" \
      -e "s|soAdditionFilePath = \"dbmodel/build/so_additions.xml\"|soAdditionFilePath = \"$mine/dbmodel/build/so_additions.xml\"|" \
      "$mine/dbmodel/build.gradle"
  fi
  # webapp/build.gradle's warWebApp task never told Gradle's War type where the REAL (struts-
  # merged, by addStrutsConfig) WEB-INF/web.xml lives, so it silently produced a war with NO
  # web.xml at all - confirmed live: the war deployed and Tomcat logged success, but every
  # request 404'd (no servlet mappings exist without one), with nothing resembling an error
  # anywhere in Tomcat's own logs. Gradle's War task manages WEB-INF/web.xml through its own
  # dedicated `webXml` property, not through the generic `from()` copy, and since `webXml` was
  # never set, nothing added one. Must ALSO exclude it from `from()` - setting webXml alone
  # added it twice (once via the property, once because it's a real file under
  # explodedWebAppDir), producing a corrupted war with two overlapping web.xml entries that
  # `unzip` refused as "possible zip bomb".
  if grep -q '^\s*exclude "WEB-INF/web.properties"$' "$mine/webapp/build.gradle" 2>/dev/null; then
    run sed -i.bak \
      -e 's|exclude "WEB-INF/web.properties"|exclude "WEB-INF/web.properties", "WEB-INF/web.xml"\n    webXml = file("${explodedWebAppDir}/WEB-INF/web.xml")|' \
      "$mine/webapp/build.gradle"
  fi
  write_file "$mine/project.xml" "$(cat out/_mine/project.xml)"
  # rdfc2im's own project.xml keeps HumanMine's full ~40-source list, our sources inserted as
  # replacements (USAGE.md) - correct for a full mine, wrong for this reduced demo build.
  # dbmodel:addSourceDependencies tries to resolve a Maven jar for every listed source, and the
  # ~39 stock sources we don't run were only ever published to JCenter/Bintray (dead since 2021,
  # confirmed live: 403 from jcenter.bintray.com, and confirmed absent from Maven Central too -
  # not a dead-link, genuinely unpublished anywhere else). Trim to just our own sources (all
  # share one `type`, set once in rdfc2im.yaml) - the same reduction LOAD-TRIAL.md's own
  # "Reducing a mine" section describes doing by hand for the original go-only trial ("project.xml
  # lists only humanmine-go"), generalised to all 9 and finally scripted instead of manual.
  # "reactome" (stock, type="reactome") is kept alongside our own humanmine-reactome by exact
  # name, not by type: sources.yaml's own `alongside: [reactome]` already declares it a real
  # dependency, not an unused extra - our own reactome source maps Pathway.description only, no
  # participants (the pathwayComponent link is unmapped/pruned - see sources.yaml's own note),
  # so without the stock source Pathway never connects to any Protein or Gene at all. Its own
  # jar (org.intermine:bio-source-reactome) is equally dead on JCenter/Maven Central, but unlike
  # the ~39 sources dropped above, this one is worth building from local source and keeping -
  # see phase_build_humanmine_items' sibling handling of the same JCenter problem.
  run python3 tools/reduce_mine.py sources "$mine/project.xml" humanmine-items reactome
  # Restrict to human only, matching the rest of this build - rdfc2im's own generated project.xml
  # carries this source's original "9606 10090" (human+mouse) value untouched.
  run sed -i 's|<property name="reactome.organisms" value="9606 10090"/>|<property name="reactome.organisms" value="9606"/>|' \
    "$mine/project.xml"
  # HumanMine's own priorities file, merged with our sources (rdfc2im project already did the
  # merge; this just deploys it) - re-syncing after adding a source is required, not optional
  # (confirmed live, LOAD-TRIAL.md's hgnc section, finding 3).
  write_file "$mine/dbmodel/resources/genomic_priorities.properties" "$(cat out/_mine/genomic_priorities.properties)"
  local prod_props
  prod_props=$(find "$UPSTREAM_CACHE/intermine" -path "*resources/src/main/resources/default.intermine.production.properties" | head -1)
  [ -n "$prod_props" ] || die "default.intermine.production.properties not found under $UPSTREAM_CACHE/intermine - did make-inputs.sh run?"
  # Must be the one defining integration.production, NOT bio/model's copy (integration.bio-test)
  # - confirmed live, LOAD-TRIAL.md: the wrong one fails with "Failed to instantiate
  # IntegrationWriter class".
  write_file "$HOME/.intermine/humanmine.properties" "$(cat <<PROPS
db.production=psql
db.production.datasource.serverName=localhost:$PG_PORT
db.production.datasource.databaseName=humanmine-production
db.production.datasource.user=intermine
db.production.datasource.password=intermine

db.common-tgt-items=psql
db.common-tgt-items.datasource.serverName=localhost:$PG_PORT
db.common-tgt-items.datasource.databaseName=humanmine-items
db.common-tgt-items.datasource.user=intermine
db.common-tgt-items.datasource.password=intermine

db.userprofile-production=psql
db.userprofile-production.datasource.serverName=localhost:$PG_PORT
db.userprofile-production.datasource.databaseName=humanmine-userprofile
db.userprofile-production.datasource.user=intermine
db.userprofile-production.datasource.password=intermine

default.intermine.properties.file=$prod_props
webapp.baseurl=http://localhost:8090
webapp.path=humanmine
# webapp/build.gradle's cargo {} block (Gradle-Cargo remote-Tomcat deploy plugin) eagerly reads
# this during Gradle's CONFIGURE phase, not lazily when a cargo task actually runs - confirmed
# live: it calls getServerName(props.getProperty("webapp.deploy.url")) with a null deployUrl,
# which NPEs on deployUrl.contains("//") and fails the whole project's configuration (blocking
# :dbmodel:builddb too, same class of problem as the gretty fix above). The code LOOKS like it
# should skip this via `if (props.hasProperty("webapp.hostname")) ... else getServerName(...)`,
# but that's a Groovy trap: Properties.hasProperty(key) is the Groovy MOP method (does this
# OBJECT have a declared field/property with this name?), not Properties.containsKey(key) - it
# is FALSE for every loaded entry, always, regardless of what's in the file, so the `else`
# branch always runs and webapp.hostname (tried first, see prior commit) can never help. Setting
# webapp.deploy.url instead works because THAT one is read with the real, correct
# Properties.getProperty(key) API. This build never runs a cargo task at all (deployment goes
# through the Tomcat container in trial/docker-compose.yml instead), so the value only needs to
# be a syntactically valid URL - reusing webapp.baseurl's is the natural choice.
webapp.deploy.url=http://localhost:8090
project.title=$MINE_TITLE
# Neither default.intermine.production.properties nor anywhere else sets these - every real
# InterMine deployment must supply its own (confirmed: grep for superuser.account across every
# upstream checkout found it only in test/CI fixtures, never a real default). Without it,
# :webapp:loadDefaultTemplates (part of :dbmodel:buildUserDB, which creates the userprofile
# schema) fails live: "Unable to load super user profile" / "Loading default templates and tags
# into profile null" - and without THAT, the webapp's own ActionServlet fails to initialise at
# startup ("userprofileOSW is null"), so InterMineContext never initialises and every request
# 500s with ContextNotInitialisedException, however long you wait. Values match the convention
# every one of intermine's own CI/test properties files uses (config/ci.properties etc).
superuser.account=superuser@intermine.org
superuser.initialPassword=intermine
PROPS
)"
  log "~/.intermine/humanmine.properties done (default.intermine.properties.file=$prod_props)"
}

phase_stage_src_data() {
  # project.xml's src.data.file for each of our sources points at rdfc2im.yaml's src_data_dir
  # (/micklem/data/rdfc2im/<source>/<source>.xml - a real Micklem-lab deployment path; USAGE.md
  # documents this as where "items/<source>.xml will live on the build machine", i.e. something
  # meant to be staged there, not rewritten). Nothing before this phase ever puts a file there -
  # confirmed live: :dbmodel:integrate died on the very first source, "Exception while reading
  # from: /micklem/data/rdfc2im/clinvar/clinvar.xml" - this step was simply missing. Symlink
  # rather than copy: these items files can be large (clinvar alone is 54039 items) and a
  # symlink keeps out/ as the single source of truth, so a later `rdfc2im items` re-run is
  # picked up without re-staging.
  for src in $SOURCES; do
    local dest_dir="/micklem/data/rdfc2im/$src"
    run mkdir -p "$dest_dir"
    run ln -sf "$HERE/out/$src/items/$src.xml" "$dest_dir/$src.xml"
  done
}

phase_start_databases() {
  # COMPOSE_FILES (and its docker-compose.local.yml, if BIND_ADDR needs one) is computed
  # globally, above - see that block's comment for why (resume-safety: this phase can be
  # skipped by --from without start_mine_stack losing access to it).
  run env PG_PORT="$PG_PORT" $COMPOSE "${COMPOSE_FILES[@]}" up -d postgres solr
  log "waiting for postgres to report healthy..."
  if [ "$DRY_RUN" -eq 0 ]; then
    for _ in $(seq 1 60); do
      docker inspect --format '{{.State.Health.Status}}' rdfc2im-postgres 2>/dev/null | grep -q healthy && break
      sleep 2
    done
  fi
  for db in humanmine-production humanmine-items humanmine-userprofile; do
    # \gexec must be fed via stdin, not -c: confirmed live that psql's -c mode never recognises
    # it as a meta-command regardless of line placement (it's sent to the server as literal SQL
    # text and fails with "syntax error at or near \"\\\""), while the identical text piped
    # through stdin (`docker exec -i ... <<SQL`) works. `-i` + a heredoc, matching the same
    # pattern phase_apply_templates already uses below for demo_public_templates.sql.
    run bash -c "docker exec -i rdfc2im-postgres psql -U intermine -d postgres <<SQL
SELECT 'CREATE DATABASE \"$db\"' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$db')
\\gexec
SQL"
  done
  # Solr's image only precreates one core via `command:`; both of this mine's cores are made
  # here. tools/solr-search-schema-fix.sh does NOT run here (moved to phase_postprocess) - see
  # that phase's own comment for why: it needs a field type Solr's schemaless configset only
  # creates once real documents exist, which they don't yet on a freshly created, empty core.
  for core in humanmine-search humanmine-autocomplete; do
    run bash -c "docker exec rdfc2im-solr solr create_core -c $core 2>&1 | grep -qv 'already exists' || true"
  done
}

phase_build_dbmodel() {
  local mine="$TRIAL_HOME/humanmine"
  run_in "$mine" "${GRADLE_ENV[@]}" ./gradlew :dbmodel:builddb
  # os.production only - the userprofile database's own schema (savedtemplatequery, tag, ...)
  # is a SEPARATE task, never run anywhere else in this script. Without it the webapp's own
  # ActionServlet fails to initialise at startup ("userprofileOSW is null" - confirmed live),
  # InterMineContext never initialises, and every single request 500s with
  # ContextNotInitialisedException regardless of how long you wait - a much bigger symptom than
  # its cause looks like. buildUserDB (not just createUserDB) also loads the superuser account
  # and default templates (:webapp:loadDefaultTemplates), which curation/demo_public_templates.sql
  # depends on via foreign key (phase_apply_templates' own inserts reference the superuser row).
  run_in "$mine" "${GRADLE_ENV[@]}" ./gradlew :dbmodel:buildUserDB
  # genomic_priorities.properties (as written by phase_prepare_mine_checkout) still has entries
  # for classes only some OTHER, unrun stock source would have contributed (ProteinDomain, from
  # protein-atlas) - the exact "Reducing a mine" failure LOAD-TRIAL.md documents: PriorityConfig
  # throws "Class 'ProteinDomain' not found in model", which fails IntegrationWriter
  # instantiation for every single source, not just one - confirmed live. Must run AFTER builddb
  # (needs the real merged genomic_model.xml, not the live HumanMine model - same rule LOAD-
  # TRIAL.md states) and BEFORE integrate_sources (which needs a working IntegrationWriter).
  # shellcheck disable=SC1090
  source "$HERE/.venv/bin/activate"
  run python3 tools/reduce_mine.py priorities \
    "$mine/dbmodel/resources/genomic_priorities.properties" \
    "$mine/dbmodel/build/resources/main/genomic_model.xml"
}

phase_integrate_sources() {
  local mine="$TRIAL_HOME/humanmine"
  # Load order matters for `alongside` sources (uniprot, reactome): each must integrate after
  # the stock source it supplements, or it duplicates instead of merging (confirmed live,
  # commit 323e6fe). rdfc2im's own generated project.xml already encodes the correct order and
  # `check` already validated it - read it back rather than re-deciding the order here. Matches
  # ANY source name, not just humanmine-* ones: confirmed live that restricting to
  # "humanmine-[a-z]+" silently skipped the stock "reactome" alongside source entirely (added
  # back into project.xml by phase_prepare_mine_checkout's reduce_mine.py call, but this regex
  # predates that and was never updated) - integrate_sources ran all 9 of our own sources with
  # no error, reactome's own real data just never made it into the database, only found by
  # checking for pathway-protein rows after a "successful" build. project.xml is already
  # trimmed to exactly the sources we want by this point, so no prefix filter is needed at all.
  local order
  order=$(grep -oP '(?<=<source name=")[A-Za-z0-9_-]+(?=")' "$mine/project.xml" 2>/dev/null || true)
  if [ -z "$order" ] && [ "$DRY_RUN" -eq 1 ]; then
    order="reactome humanmine-$(echo "$SOURCES" | sed 's/ /\nhumanmine-/g')"  # placeholder so dry-run still shows the loop shape
    log "note: $mine/project.xml does not exist yet in this dry run - showing SOURCES as a stand-in for the real (project.xml-derived) load order"
  fi
  for src in $order; do
    run_in "$mine" "${GRADLE_ENV[@]}" ./gradlew :dbmodel:integrate -Psource="$src"
  done
}

phase_postprocess() {
  # Order from HumanMine's own project.xml <post-processing> block (LOAD-TRIAL.md). The
  # genomic-location-specific tasks (create-chromosome-locations-and-lengths,
  # transfer-sequences, create-gene-flanking-features, create-location-overlap-index,
  # create-overlap-view, populate-child-features) are deliberately not run: this build loads no
  # coordinate/sequence data, so they would no-op or fail on a missing prerequisite rather than
  # produce anything real (confirmed live). update-data-sources needs a Micklem-lab-specific
  # file path this build does not have; also skipped.
  local mine="$TRIAL_HOME/humanmine"
  for task in create-references create-attribute-indexes; do
    run_in "$mine" "${GRADLE_ENV[@]}" ./gradlew :dbmodel:postProcess -Pprocess="$task"
  done
  # Gene.pathways is populated ONLY by reactome's own source postprocessor - stock
  # reactome_additions.xml says so in as many words ("<!-- populated by postprocess -->" above
  # its Gene class), and ReactomePostProcess's javadoc is literally "Copy over Protein.pathways
  # to Gene.pathways": it derives the gene-level links by walking Gene<->Protein over the
  # protein-level ones the converter loaded. Without it, genespathways stays empty (confirmed
  # live: 0 rows) even though pathwayproteins is fully populated.
  #
  # Two things are needed, in this order. First rebuild the jar WITH the postprocessor class,
  # now that builddb has produced the mine's own dbmodel.jar to compile it against (see
  # _install_reactome_jar's header for the chicken-and-egg this resolves).
  _install_reactome_jar with-postprocess
  # Then run it. `do-sources` is the ONLY task that dispatches source-specific postprocessors
  # (PostProcessPlugin.groovy: every other process name goes down the "core postprocess" branch
  # instead). It is NOT being run blind/umbrella-wide here - that same plugin reads an optional
  # `-Psource=` and restricts itself to exactly those sources (line 75-82: an empty value is
  # what makes it iterate every source in project.xml). Scoping it to reactome alone keeps the
  # existing "do-sources is too broad to run blind" caution intact while still getting this one
  # source's postprocessor run - confirmed live: logs "Performing source postprocess on
  # reactome" and nothing else.
  run_in "$mine" "${GRADLE_ENV[@]}" ./gradlew :dbmodel:postProcess -Pprocess=do-sources -Psource=reactome
  # solr-search-schema-fix.sh must run AFTER a first create-search-index pass, not before:
  # confirmed live, calling it against a freshly created empty core 400s with "The field type
  # 'analyzed_string' is not present in this schema, and so cannot be replaced" - Solr's
  # schemaless configset only creates that field type once a real document uses it (see that
  # script's own header comment). Originally called from phase_start_databases, right after the
  # core is created and before anything has been indexed into it - moved here instead. Its own
  # header comment already documents the fix-then-reindex sequence (the schema change is not
  # retroactive): index once, fix the type, clear the now-wrongly-analyzed docs, index again.
  run_in "$mine" "${GRADLE_ENV[@]}" ./gradlew :dbmodel:postProcess -Pprocess=create-search-index
  run env SOLR_URL="http://localhost:$SOLR_PORT" sh tools/solr-search-schema-fix.sh
  run curl -sf "http://localhost:$SOLR_PORT/solr/humanmine-search/update?commit=true" \
    -H "Content-Type: application/json" -d '{"delete": {"query": "*:*"}}'
  run_in "$mine" "${GRADLE_ENV[@]}" ./gradlew :dbmodel:postProcess -Pprocess=create-search-index
  for task in create-autocomplete-index summarise-objectstore; do
    run_in "$mine" "${GRADLE_ENV[@]}" ./gradlew :dbmodel:postProcess -Pprocess="$task"
  done
}

phase_build_webapp() {
  run_in "$TRIAL_HOME/humanmine" "${GRADLE_ENV[@]}" ./gradlew :webapp:war
}

phase_resolve_bluegenes_deps() {
  # Confirmed live 2026-09-21 (standalone, ahead of any real build attempt): resolves to exactly
  # 178 jars, matching LOAD-TRIAL.md's number exactly - see this script's header comment. If the
  # `org.intermine:bluegenes:1.4.5` coordinate or the Clojars repo ever changes, this fails loudly
  # at `resolveBgDeps` (an unresolvable dependency), not silently.
  local bgdir="$TRIAL_HOME/bgdeps"
  write_file "$bgdir/build.gradle" "$(cat <<'GRADLE'
repositories {
    maven { url 'https://repo.clojars.org' }
    mavenCentral()
}
configurations { bluegenes }
dependencies { bluegenes 'org.intermine:bluegenes:1.4.5' }
task resolveBgDeps(type: Copy) {
    from configurations.bluegenes
    into "$buildDir/lib"
}
task classpathTxt {
    doLast {
        file("$projectDir/classpath.txt").text =
            configurations.bluegenes.resolvedConfiguration.resolvedArtifacts
                .collect { it.file.absolutePath }.join(':')
    }
}
GRADLE
)"
  write_file "$bgdir/settings.gradle" "rootProject.name = 'bgdeps'"
  # No ./gradlew here (this is a throwaway project, not a checkout) - needs a plain Gradle 4.9
  # launcher on PATH or at $GRADLE49 (see this script's header comment).
  run_in "$bgdir" "${GRADLE_ENV[@]}" "$GRADLE49" resolveBgDeps classpathTxt
  if [ "$DRY_RUN" -eq 0 ]; then
    [ -f "$bgdir/classpath.txt" ] || die "bgdeps/classpath.txt was not produced - see this function's header comment"
  fi
}

phase_stage_artifacts() {
  run env TRIAL_HOME="$TRIAL_HOME" sh tools/trial-stage.sh
}

phase_start_mine_stack() {
  run $COMPOSE "${COMPOSE_FILES[@]}" up -d mine bluegenes
  # The webapp reads the database once, at startup - postprocessing and the war it's serving
  # must both be in place before this, and it must run again after either changes.
  run docker restart rdfc2im-mine
}

phase_apply_templates() {
  [ "$SKIP_TEMPLATES" -eq 1 ] && { log "skipped (--skip-templates)"; return 0; }
  [ -f curation/demo_public_templates.sql ] || { log "no curation/demo_public_templates.sql - skipping"; return 0; }
  run bash -c "docker exec -i rdfc2im-postgres psql -U intermine -d humanmine-userprofile < curation/demo_public_templates.sql"
  run docker restart rdfc2im-mine
}

phase_verify() {
  if [ "$DRY_RUN" -eq 1 ]; then log "skipping live checks under --dry-run"; return 0; fi
  sleep 5
  run curl -sf "http://localhost:8090/humanmine/service/version"
  run curl -sf "http://localhost:8090/humanmine/service/model" -o /dev/null
  run curl -sf "http://localhost:$BG_PORT/" -o /dev/null
  log "mine webapp: http://localhost:8090/humanmine  BlueGenes: http://localhost:$BG_PORT"
  log "if BIND_ADDR is still 127.0.0.1 (the default), reach these via: ssh -L 8090:localhost:8090 -L $BG_PORT:localhost:$BG_PORT ..."
}

# ============================================================== main
run_phase() { local p=$1; phase_start "$p"; "phase_$p"; phase_end; }

# check_prereqs sets GRADLE_ENV (the JAVA_HOME override every gradle call below needs) as a
# side effect - it must run even when --from/--only skip past it in the phase list, or every
# gradle invocation in a resumed build silently loses that override. Always run it once, here,
# regardless of --from/--only; the loop below explicitly skips it so `--only check_prereqs`
# still runs it exactly once, not twice.
run_phase check_prereqs
CURRENT_PHASE=main

started=0
for p in "${PHASES[@]}"; do
  [ "$p" = "check_prereqs" ] && continue  # already ran, unconditionally, above
  if [ -n "$ONLY_PHASE" ]; then
    [ "$p" = "$ONLY_PHASE" ] && run_phase "$p"
    continue
  fi
  if [ -n "$FROM_PHASE" ] && [ "$started" -eq 0 ]; then
    [ "$p" = "$FROM_PHASE" ] && started=1 || continue
  else
    started=1
  fi
  run_phase "$p"
done

CURRENT_PHASE=main
log "build complete. Per-phase timings:"
if [ -s "$TIMING_TSV" ]; then
  sort -t $'\t' -k3 -rn "$TIMING_TSV" | while IFS=$'\t' read -r name _ secs _; do
    log "  ${secs}s  $name"
  done
fi
log "full log: $LOGFILE"
