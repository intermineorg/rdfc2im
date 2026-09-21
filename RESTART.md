# Restarting this project in a fresh session

Paste this whole file as your first message when picking rdfc2im back up in a new session (a
new sandbox, a new LLM conversation, or both) - it's written to be read cold, not browsed.

## What this project is, in one paragraph

rdfc2im maps rdf-config models (DBCLS's machine-readable descriptions of RDF Portal datasets)
onto the InterMine data model, keeps that mapping as reviewable data with evidence per row, and
loads the result with InterMine's stock Items XML loader - no per-source Java code. It was built
at the DBCLS BioHackathon 2026 and demonstrated with a working HumanMine (9 rdfc2im sources, a
113-gene food/drug-metabolism panel, plus the stock `reactome` source loaded alongside so that
`Pathway` connects to `Protein` and `Gene`) served live in containers. The work is written up as
a BioHackrXiv report. The whole build is now one script, `tools/full-build.sh` - see `BUILD.md`.

## Read these, in this order

1. `report/paper/paper.md` (needs `git submodule update --init report` first if empty) - the
   finished narrative: why, what was built, what it found, what's left. Its Future Work section
   is the authoritative forward-looking list.
2. `STATUS.md`'s **"Where this stands"** and **"Next steps"** sections (near the bottom) - the
   current state and priorities, in one screen. Don't read the whole file top to bottom; jump
   there first.
3. `LOAD-TRIAL.md` - the detailed, chronological technical record (one section per source's
   first real load, in the order they happened). This is where the paper's Table 3 and most of
   its Discussion came from. Search it rather than read it linearly; section headers name the
   finding.
4. `BUILD.md` - how to run `tools/full-build.sh`, the one script that now takes this from RDF to
   a running mine. Needed before you try to rebuild the demonstration mine, not before you read.
5. `CURATION_GUIDE.md` - only needed if you're about to curate a new source's mapping.

Do NOT trust any of the above blindly without checking `git log` first - if you're resuming
significant time after this file was written, someone (human or another session) may have moved
things forward since. Check `STATUS.md`'s date line and `git log -5` before assuming it's current.

## Facts about the environment, not the project

These won't be true of every sandbox this project is worked on in, but are worth checking for
early rather than rediscovering the hard way:

- **The live demo mine does not persist across sandbox sessions.** Docker containers, the
  Postgres and Solr volumes (`rdfc2im-pgdata`, `rdfc2im-solrdata`), and the userprofile-only
  templates all live inside whatever sandbox built them. A fresh session starts with none of it.
  Rebuilding it is now one command - `tools/full-build.sh`, documented in `BUILD.md` - not a walk
  through LOAD-TRIAL.md's scattered recipe. `pg_dump` of `humanmine-production` and
  `humanmine-userprofile` is the way to keep a loaded mine independently of the containers.
- **`report/` is a git submodule** pointing at a *different* GitHub repo
  (`ryneches/rdfc2im-report`), under active, separate human editing (Gos and Russell both push to
  it directly, sometimes from their own machines while a sandbox session is also working in it).
  Always `git fetch`/merge both the main repo and this submodule before assuming your local state
  is current, and re-point the main repo's submodule commit (`git add report && git commit`,
  message style "Point report/ at ...") after any change to it.
- **Several data sources need their own network policy allow-list entry** the first time they're
  fetched in a fresh sandbox: `rdfportal.org`, `grch38.togovar.org` (GWAS Catalog - note
  `togovar.org` itself redirects here), `www.humanmine.org`, `id.nlm.nih.gov` (MeSH), and others
  as new sources are added. A blocked fetch returns a `Blocked by network policy` 403 with the
  exact domain in the body - ask the user to run `sbx policy allow network <domain>` on their
  host, don't guess around it.
- **`git push` needs a GitHub token configured as a sandbox secret** - if it fails with
  `fatal: could not read Username for 'https://github.com'`, ask the user to run (on their host,
  using the real sandbox name from `$SANDBOX_NAME`): `sbx secret set github --sandbox
  <sandbox-name> -t "$(gh auth token)"`.
- **Plain `cp` has silently zeroed large files** on at least one virtiofs-backed workspace mount
  used for this project - not only crossing to a different filesystem, but also between two
  paths on the same mount. Use `cat src > dst` instead for anything of meaningful size; `make
  docs` already does this for its own STATUS.md/CURATION_GUIDE.md promotion step.

## Concrete open items, in priority order

(Kept brief here on purpose - `STATUS.md`'s own "Next steps" is the maintained version; if the
two disagree, trust `STATUS.md`, and update this file to match. One exception, as of
2026-09-21: STATUS.md still lists the scripted build as open item 1. It isn't - see item 1
below; STATUS.md is regenerated from `rdfc2im/docs.py`, which is where that text has to change.)

1. ~~Script the whole build end to end~~ **DONE (2026-09-21)** - `tools/full-build.sh`, 18
   phases from RDF Portal to a running, fully-configured mine, with `--dry-run`, `--from`,
   `--only` and a live dashboard (`tools/watch-build.sh`). First real execution fixed ~30
   genuine bugs; `make test-live` now checks the result against the running system. See
   `BUILD.md`. What remains here is breadth, not the script: it has only been run on one
   machine, and only for the 9-source demo panel.
2. Re-integrate `ncbigene` (with the D14 key-ambiguity fix) into a live mine and confirm the fix
   holds under a real load, not just in the regenerated items file.
3. Give `GWASResult` a real integration key; review the DRAFT keys on `Pathway` and `MeshTerm`.
4. Work out a safe way to retrofit `DataSource.url` onto an already-loaded mine (or accept that
   it only applies cleanly to fresh builds).
5. Extend beyond the 113-gene panel: full-scale loads of the panel-limited sources, more of
   HumanMine's ~40 datasets, the open mapping decisions (D1-D13 in `STATUS.md`).
6. Wire up a `gene_scope_field` resolver (`rdfc2im/scope.py`) for any source beyond the six that
   already have one (ncbigene, hgnc, ensembl, uniprot, clinvar, gwascatalog).

## Process lessons from this session, worth keeping

- **Verify "done" before believing it.** More than once this session, a subagent's own report of
  having completed and verified something (a database retrofit, a postprocessing step) turned out
  on direct inspection to be incomplete or wrong. Check the live system yourself - a query, a row
  count, a REST call - before passing a claim on as fact, especially for anything a future
  decision might depend on. `make test-live` (tests/test_live_mine.py) now does exactly this for
  a built mine, with each check anchored to a specific bug that once passed silently.
- **Multi-hour build/debug work goes in a background agent** (a fork, if continuing this exact
  conversation; a fresh subagent otherwise), not inline - it keeps verbose build logs and
  iterative debugging out of the main conversation's context, and lets you keep working (or the
  user keep talking to you) while it runs. Give it enough context to make judgment calls, and tell
  it explicitly when to stop and report back rather than grind through something risky.
- **Never push without asking**, to either remote - this project has live human collaborators
  pushing to both independently, and an unwanted push is much harder to undo than a delayed one.
- **Commit messages here carry real evidence**, not just a summary of the diff: what was wrong,
  how it was confirmed (a number, a live query, a reproduction), what changed, how the fix was
  verified. Match that style - `git log` is full of examples - rather than a generic "fix bug"
  message.
- **A root cause fixed generally beats a special case fixed narrowly.** Several of this session's
  best fixes (the multi-key object merge, the silent-truncation paging fix) were found while
  chasing one source's problem but written to fix the general mechanism, because the same
  underlying assumption was wrong everywhere, not just for that one source. When you find a bug,
  ask whether it's really source-specific before writing a source-specific fix.
