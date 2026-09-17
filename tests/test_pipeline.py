"""pipeline.py: fetch_source_by_keys - fetch-time VALUES-batched scoping (PubMed's PMID list)."""
import rdfc2im.fetch as fetch_mod
from rdfc2im.pipeline import fetch_source_by_keys


def test_fetch_source_by_keys_batches_every_table_over_the_given_key_terms(tmp_path):
    """Regression for the real reason this exists: a gene panel's cited-publication list runs to
    thousands of PMIDs, too many for a query-embedded FILTER (RDF Portal rejected a
    ~3881-condition one outright). fetch_source_by_keys must restrict every one of a source's
    tables via VALUES-batching instead - same mechanism _fetch_paged uses to page past Virtuoso's
    Sorted TOP cap, just invoked directly rather than triggered by a row-count check."""
    qdir = tmp_path / "queries"
    qdir.mkdir()
    (qdir / "main.sparql").write_text(
        "# Endpoint: http://fake/sparql\n"
        "SELECT DISTINCT ?identifier ?title WHERE {\n"
        "  ?Pubmed dct:identifier ?identifier .\n"
        "  OPTIONAL { ?Pubmed dct:title ?title . }\n"
        "}\n"
    )
    (qdir / "main_slot.sparql").write_text(
        "# Endpoint: http://fake/sparql\n"
        "SELECT DISTINCT ?identifier ?last_name WHERE {\n"
        "  ?Pubmed dct:identifier ?identifier .\n"
        "  OPTIONAL { ?Pubmed dct:creator ?c . OPTIONAL { ?c foaf:LastName ?last_name . } }\n"
        "}\n"
    )
    seen_batches = {"main": [], "main_slot": []}

    def fake_fetch_once(q, ep, timeout):
        table = "main_slot" if "last_name" in q else "main"
        import re
        m = re.search(r'VALUES \?identifier \{ (.*?) \}', q)
        assert m, f"expected a VALUES-batched query, got: {q!r}"
        ids = re.findall(r'"(\d+)"', m.group(1))
        seen_batches[table].append(ids)
        var2 = "last_name" if table == "main_slot" else "title"
        rows = "\n".join(f'"{i}"\t"T{i}"' for i in ids)
        body = f"?identifier\t?{var2}\n{rows}\n".encode()
        return body, "text/tab-separated-values", None

    orig = fetch_mod._fetch_once
    fetch_mod._fetch_once = fake_fetch_once
    try:
        res = fetch_source_by_keys(str(tmp_path), "identifier", ['"1"', '"2"', '"3"'], timeout=30, sleep=0,
                                   log=lambda *a, **k: None)
    finally:
        fetch_mod._fetch_once = orig

    assert res == {"main": "fetched", "main_slot": "fetched"}
    assert seen_batches["main"] == [["1", "2", "3"]]
    assert seen_batches["main_slot"] == [["1", "2", "3"]]
    main_lines = (tmp_path / "raw" / "main.tsv").read_text().splitlines()
    assert main_lines[0] == "?identifier\t?title"
    assert {l.split("\t")[0].strip('"') for l in main_lines[1:]} == {"1", "2", "3"}
