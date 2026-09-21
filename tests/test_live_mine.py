"""Live integration checks against a running demonstration mine.

Unlike every other file in tests/ - which runs offline against synthetic fixtures - these
assert on a mine that tools/full-build.sh has actually built and started. They exist because
essentially every bug this project hit during its first real end-to-end build was invisible
offline: a Gradle task that reports SUCCESS while silently indexing nothing, a postprocessor
whose failure the plugin swallows so the exit code stays 0, a priority rule that drops an
attribute only when two sources overlap. None of those can be caught by a unit test on a
fixture; all of them are caught by asking the running system a question and checking the answer.

Every check here is anchored to a real bug found on 2026-09-21, named in each test's docstring,
so a regression tells you not just "this broke" but "this specific thing broke again, here is
what it was last time".

Running them:
    make test-live                       # or:
    python3 tests/run.py                 # runs these too; skips cleanly with no mine
    python3 -m pytest tests/test_live_mine.py -v

With no mine reachable every test SKIPS rather than fails, so `make test` stays green on a
machine that has only ever checked out the repo. Point them somewhere else with:
    MINE_BASE=http://host:8090/humanmine SOLR_BASE=http://host:8983 make test-live
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

MINE_BASE = os.environ.get("MINE_BASE", "http://localhost:8090/humanmine")
SOLR_BASE = os.environ.get("SOLR_BASE", "http://localhost:8983")
TIMEOUT = int(os.environ.get("MINE_TIMEOUT", "30"))

# The demo panel's own worked example, used as the spot-check throughout LOAD-TRIAL.md: a
# drug-metabolising cytochrome P450 that every gene-centric source in the build carries.
EXAMPLE_GENE = "CYP2D6"
EXAMPLE_GENE_ID = "1565"          # NCBI Gene id; ncbigene is the source every other merges onto
EXAMPLE_PROTEIN = "P10635"        # its UniProt accession
EXAMPLE_PATHWAY = "R-HSA-9749641"  # "Aspirin ADME" - CYP2D6 is one of its participants


class SkipTest(Exception):
    """Raised when there is no mine to talk to - see _skip."""


def _skip(reason: str):
    """Skip under pytest if it is installed, otherwise degrade to a no-op for tests/run.py.

    run.py treats any exception as a failure, so it must NOT see SkipTest propagate; pytest
    has a real skip mechanism and should use it. Returning a sentinel the caller `return`s
    keeps both runners honest without run.py needing to know about skipping at all.
    """
    try:
        import pytest  # noqa: PLC0415 - optional dependency, deliberately imported lazily
        pytest.skip(reason, allow_module_level=False)
    except ImportError:
        print(f"  SKIP: {reason}")


def _get(url: str, timeout: int = TIMEOUT):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _get_json(url: str, timeout: int = TIMEOUT):
    return json.loads(_get(url, timeout))


def mine_up() -> bool:
    try:
        _get(f"{MINE_BASE}/service/version", timeout=5)
        return True
    except Exception:
        return False


def solr_up() -> bool:
    try:
        _get(f"{SOLR_BASE}/solr/admin/cores?action=STATUS", timeout=5)
        return True
    except Exception:
        return False


def query_rows(view: str, path: str, op: str, value: str):
    """Run a real PathQuery through the REST API and return its rows.

    Deliberately goes through /service/query/results rather than straight to Postgres: it
    exercises the model, the webapp and the object store together, which is where several of
    this project's bugs actually lived (a collection present in the database but absent from
    the served model still breaks every user-facing query).
    """
    q = (f'<query name="" model="genomic" view="{view}">'
         f'<constraint path="{path}" op="{op}" value="{value}"/></query>')
    url = f"{MINE_BASE}/service/query/results?query={urllib.parse.quote(q)}&format=json"
    payload = _get_json(url)
    if not payload.get("wasSuccessful", False):
        raise AssertionError(f"query failed: {payload.get('error')!r}\n  view={view} {path}{op}{value}")
    return payload["results"]


def solr_count(core: str, q: str = "*:*") -> int:
    url = f"{SOLR_BASE}/solr/{core}/select?q={urllib.parse.quote(q)}&rows=0&wt=json"
    return _get_json(url)["response"]["numFound"]


def autocomplete_classes() -> dict:
    """className -> document count in the autocomplete core, via a Solr facet."""
    url = (f"{SOLR_BASE}/solr/humanmine-autocomplete/select"
           f"?q=*:*&rows=0&facet=true&facet.field=className&wt=json")
    f = _get_json(url)["facet_counts"]["facet_fields"]["className"]
    return {k: v for k, v in zip(f[::2], f[1::2]) if v}


# ----------------------------------------------------------------- service reachability
def test_mine_serves_version():
    """The webapp answers at all.

    Regression guard for the 2026-09-21 'war built with no WEB-INF/web.xml' bug: Tomcat
    deployed it with no error logged anywhere and every request 404'd, and for the follow-on
    'no userprofile schema' bug, where every request 500'd with ContextNotInitialisedException.
    """
    if not mine_up():
        return _skip(f"no mine at {MINE_BASE}")
    assert _get(f"{MINE_BASE}/service/version").strip(), "version endpoint returned nothing"


def test_mine_serves_model():
    if not mine_up():
        return _skip(f"no mine at {MINE_BASE}")
    _get(f"{MINE_BASE}/service/model")


# ----------------------------------------------------------------- autocomplete
def test_autocomplete_index_is_populated():
    """create-autocomplete-index actually indexed something.

    Regression guard for the 2026-09-21 bug where objectstoresummary.config.properties named
    classes this reduced build does not load (HPOTerm/InteractionTerm/ProteinDomain);
    AutoCompleter.buildIndex threw on the first absent class and the Gradle plugin swallowed
    the exception, so the task printed FAILED, left an EMPTY index, and still exited 0. It had
    been broken in five consecutive builds without anyone noticing, which is precisely why this
    asserts on document count rather than on the absence of an error message.
    """
    if not solr_up():
        return _skip(f"no solr at {SOLR_BASE}")
    n = solr_count("humanmine-autocomplete")
    assert n > 0, "humanmine-autocomplete core is empty - create-autocomplete-index did nothing"


def test_autocomplete_covers_gene():
    """Gene is autocompleted.

    Gene is the demo panel's organising class (113 food/drug-metabolism genes), so a user
    typing a gene symbol into a form is the single most likely autocomplete interaction.
    HumanMine's stock objectstoresummary.config.properties does NOT include Gene, so this is
    an addition this project makes deliberately, not an upstream default to fall back on.
    """
    if not solr_up():
        return _skip(f"no solr at {SOLR_BASE}")
    classes = autocomplete_classes()
    assert "gene" in {c.lower() for c in classes}, (
        f"Gene missing from the autocomplete index; indexed classes were {sorted(classes)}. "
        "Check Gene.autocomplete in objectstoresummary.config.properties, and that the "
        "objectstoresummary trim did not strip it.")


def test_autocomplete_covers_protein():
    """Protein is autocompleted - same reasoning as Gene; also not an upstream default."""
    if not solr_up():
        return _skip(f"no solr at {SOLR_BASE}")
    classes = autocomplete_classes()
    assert "protein" in {c.lower() for c in classes}, (
        f"Protein missing from the autocomplete index; indexed classes were {sorted(classes)}.")


def test_autocomplete_covers_configured_ontology_classes():
    """The classes HumanMine's own config asks for are all present.

    These come from upstream objectstoresummary.config.properties. Pathway in particular is a
    good canary: it is populated by two different sources in this build, so it disappearing
    would point at the source-priority machinery rather than at the indexer.
    """
    if not solr_up():
        return _skip(f"no solr at {SOLR_BASE}")
    classes = {c.lower() for c in autocomplete_classes()}
    for expected in ("goterm", "pathway", "disease"):
        assert expected in classes, f"{expected} missing from autocomplete index ({sorted(classes)})"


def test_autocomplete_finds_a_real_gene_symbol():
    """A real panel gene symbol is actually retrievable, not merely 'some gene rows exist'.

    Asserting on a known symbol rather than a count catches the case where the index is full
    of the right class but the wrong field - e.g. indexing Gene.primaryIdentifier only, so
    typing 'CYP2D6' finds nothing while the doc count still looks healthy.
    """
    if not solr_up():
        return _skip(f"no solr at {SOLR_BASE}")
    if "gene" not in {c.lower() for c in autocomplete_classes()}:
        return _skip("Gene not yet in the autocomplete index (see test_autocomplete_covers_gene)")
    n = solr_count("humanmine-autocomplete", f'"{EXAMPLE_GENE}"')
    assert n > 0, f"{EXAMPLE_GENE} not found in the autocomplete index"


# ----------------------------------------------------------------- keyword search
def test_keyword_search_endpoint_responds():
    """/service/search answers rather than 500ing.

    Separate machinery from autocomplete (humanmine-search core, not humanmine-autocomplete),
    and separately broken on 2026-09-21: the core held 176,794 documents that carried only
    id/Category/facet_Category - no content fields at all - so quicksearch matched nothing and
    the REST endpoint returned 500 'Service failed'.
    """
    if not mine_up():
        return _skip(f"no mine at {MINE_BASE}")
    try:
        payload = _get_json(f"{MINE_BASE}/service/search?q={EXAMPLE_GENE}&size=5")
    except urllib.error.HTTPError as e:
        raise AssertionError(f"/service/search returned HTTP {e.code} for '{EXAMPLE_GENE}'") from e
    assert payload.get("wasSuccessful", False), (
        f"/service/search failed: {payload.get('error')!r}")


def test_keyword_search_finds_the_example_gene():
    """Searching a panel gene symbol returns at least one hit."""
    if not mine_up():
        return _skip(f"no mine at {MINE_BASE}")
    try:
        payload = _get_json(f"{MINE_BASE}/service/search?q={EXAMPLE_GENE}&size=5")
    except urllib.error.HTTPError as e:
        raise AssertionError(f"/service/search returned HTTP {e.code}") from e
    if not payload.get("wasSuccessful", False):
        raise AssertionError(f"/service/search failed: {payload.get('error')!r}")
    assert payload.get("results"), f"no search hits for {EXAMPLE_GENE}"


# ----------------------------------------------------------------- loaded data invariants
def test_gene_panel_is_panel_sized():
    """The build is gene-panel scoped, not a full genome load.

    Regression guard for the scope bug fixed on 2026-09-21: ncbigene was configured `full`
    rather than `panel`, so the first build loaded every human gene (~193,000) instead of the
    113-gene demo panel. The upper bound is what matters here; the exact count is asserted
    loosely because a source legitimately contributing a few extra Gene stubs is not a bug.
    """
    if not mine_up():
        return _skip(f"no mine at {MINE_BASE}")
    rows = query_rows("Gene.primaryIdentifier", "Gene.primaryIdentifier", "!=", "__none__")
    assert 0 < len(rows) < 1000, (
        f"expected a panel-sized Gene table, got {len(rows)} - is a source unscoped?")


def test_example_gene_resolves_with_cross_source_identifiers():
    """One gene carries identifiers merged from several sources.

    ncbigene supplies primaryIdentifier, ensembl supplies secondaryIdentifier, hgnc supplies
    the symbol - so this failing means the cross-source merge by integration key broke, not
    that one source failed to load.
    """
    if not mine_up():
        return _skip(f"no mine at {MINE_BASE}")
    rows = query_rows("Gene.primaryIdentifier Gene.symbol Gene.organism.taxonId",
                      "Gene.symbol", "=", EXAMPLE_GENE)
    assert rows, f"{EXAMPLE_GENE} not found"
    assert rows[0][0] == EXAMPLE_GENE_ID, f"expected NCBI id {EXAMPLE_GENE_ID}, got {rows[0][0]}"
    assert rows[0][2] == "9606", f"expected taxon 9606, got {rows[0][2]}"


# ----------------------------------------------------------------- reactome connectivity
def test_pathways_have_protein_participants():
    """Pathway connects to Protein.

    rdfc2im's own reactome mapping only ever writes Pathway description/name/organism - the
    pathwayComponent branch that would carry participants is unmapped and pruned. Protein
    links come from the stock reactome source loaded alongside it, so this failing most
    likely means that source stopped being built, staged, or integrated.
    """
    if not mine_up():
        return _skip(f"no mine at {MINE_BASE}")
    rows = query_rows("Pathway.name Pathway.proteins.primaryAccession",
                      "Pathway.identifier", "=", EXAMPLE_PATHWAY)
    assert rows, f"{EXAMPLE_PATHWAY} has no protein participants"
    accessions = {r[1] for r in rows}
    assert EXAMPLE_PROTEIN in accessions, (
        f"{EXAMPLE_PROTEIN} ({EXAMPLE_GENE}'s protein) missing from {EXAMPLE_PATHWAY}")


def test_genes_have_pathways():
    """Gene.pathways is populated.

    Regression guard for the 2026-09-21 gap: Gene.pathways is not loaded by any converter -
    it is derived by reactome's own ReactomePostProcess ("Copy over Protein.pathways to
    Gene.pathways"), which was never run, leaving the collection empty while Protein.pathways
    was fully populated. A gene-centric mine wants the gene-level link most of all.
    """
    if not mine_up():
        return _skip(f"no mine at {MINE_BASE}")
    rows = query_rows("Gene.symbol Gene.pathways.identifier Gene.pathways.name",
                      "Gene.symbol", "=", EXAMPLE_GENE)
    assert rows, (f"{EXAMPLE_GENE} has no pathways - has the reactome source postprocess "
                  "(-Pprocess=do-sources -Psource=reactome) run?")
    assert any(r[1] == EXAMPLE_PATHWAY for r in rows), (
        f"expected {EXAMPLE_PATHWAY} among {EXAMPLE_GENE}'s pathways, got {[r[1] for r in rows]}")


def test_pathway_descriptions_are_not_starved_by_source_priority():
    """A pathway carrying protein participants also carries its description.

    Regression guard for the source-priority bug fixed on 2026-09-21: genomic_priorities
    ranked the stock reactome source first for Pathway.description, but that source's
    converter never sets the field at all, so every pathway the stock source also touched
    silently lost the description that only rdfc2im's source provides - 2344 of 2883 pathways
    had none. Checking a pathway that BOTH sources contribute to is the whole point; a
    pathway only one source knows about would pass even with the bug present.
    """
    if not mine_up():
        return _skip(f"no mine at {MINE_BASE}")
    rows = query_rows("Pathway.identifier Pathway.name Pathway.description",
                      "Pathway.identifier", "=", EXAMPLE_PATHWAY)
    assert rows, f"{EXAMPLE_PATHWAY} not found"
    assert rows[0][2], (
        f"{EXAMPLE_PATHWAY} has protein participants but no description - "
        "check Pathway.description ordering in curation/priorities_override.properties")


# ----------------------------------------------------------------- classic webapp UI
def test_classic_ui_webconfig_is_valid():
    """The classic webapp renders without the webconfig validation error.

    Not cosmetic: on 2026-09-21 this error threw a full Struts/Tiles stack trace on EVERY
    classic-UI page load, growing Tomcat's localhost log to 2.3GB, filling the Docker disk,
    and crashing Postgres mid-WAL-redo so it could not restart. A reduced mine must have its
    webconfig-model.xml trimmed of widgets whose data it does not load.
    """
    if not mine_up():
        return _skip(f"no mine at {MINE_BASE}")
    try:
        body = _get(f"{MINE_BASE}/begin.do")
    except urllib.error.HTTPError as e:
        raise AssertionError(f"begin.do returned HTTP {e.code}") from e
    assert "webconfig-model.xml file is not valid" not in body, (
        "classic UI reports an invalid webconfig-model.xml - trim the widgets referencing "
        "classes/paths this build does not load (tools/reduce_mine.py webconfig)")
