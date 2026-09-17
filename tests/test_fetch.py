import re
import rdfc2im.fetch as fetch_mod
from rdfc2im.fetch import _json_to_tsv, _csv_to_tsv, _term, with_limit, _order_by, _sniff
from rdfc2im.tsv import clean_term

def test_json_to_tsv_roundtrip():
    body = b'{"head":{"vars":["id","go"]},"results":{"bindings":[{"id":{"type":"literal","value":"a\\tb"},"go":{"type":"uri","value":"http://x/GO_1"}},{"id":{"type":"typed-literal","datatype":"http://www.w3.org/2001/XMLSchema#integer","value":"5"}}]}}'
    tsv = _json_to_tsv(body).decode()
    lines = tsv.splitlines()
    assert lines[0] == "?id\t?go"
    cells = lines[1].split("\t")
    assert clean_term(cells[0]) == "a b" and clean_term(cells[1]) == "http://x/GO_1"
    assert lines[2].split("\t") == ['"5"^^<http://www.w3.org/2001/XMLSchema#integer>', ""]

def test_csv_to_tsv():
    tsv = _csv_to_tsv(b'id,go\r\n"x, y",http://x/1\r\n').decode().splitlines()
    assert tsv[0] == "?id\t?go" and clean_term(tsv[1].split("\t")[0]) == "x, y"

def test_with_limit():
    q = "SELECT ?x WHERE { ?x ?p ?o }\nLIMIT 20\n"
    assert with_limit(q, None) == q
    assert with_limit(q, 0) == "SELECT ?x WHERE { ?x ?p ?o }\n"
    assert with_limit(q, 5).endswith("LIMIT 5\n") and "LIMIT 20" not in with_limit(q, 5)
    assert with_limit("SELECT ?x WHERE { }\n", 7) == "SELECT ?x WHERE { }\nLIMIT 7\n"

def test_order_by_and_sniff():
    q = "SELECT DISTINCT ?Gene ?id\nWHERE {\n  ?Gene ?p ?id .\n}\n"
    assert _order_by(q).endswith("}\nORDER BY ?Gene\n")
    assert _sniff(b'{"head":{}}', "text/tab-separated-values") == "application/sparql-results+json"
    assert _sniff(b'"a"\t"b"\n', "text/csv") == "text/tab-separated-values"

def test_paged_fetch_stops_loudly_at_the_sorted_top_cap_instead_of_truncating_silently(tmp_path):
    """Regression for the Virtuoso "Sorted TOP" cap: a table whose true size exceeds the cap
    used to have its last page's request simply fail and get swallowed as an ordinary "partial"
    outcome indistinguishable from a short final page, leaving a file that looked complete.
    Paging must instead recognise the cap *before* making a doomed request and report "partial"
    distinctly, with every row up to the cap still written (nothing already fetched is lost)."""
    rows = [(str(i), f"v{i}") for i in range(1, 11)]  # 10 rows, cap set to 6

    def fake_fetch_once(q, ep, timeout):
        assert "OFFSET" in q  # still the plain offset strategy - no comparison filters
        off_m = re.search(r"OFFSET (\d+)", q)
        lim_m = re.search(r"LIMIT (\d+)", q)
        offset, lim = int(off_m.group(1)), int(lim_m.group(1))
        page_rows = rows[offset: offset + lim]
        body = b"?id\t?val\n" + b"\n".join(f'"{i}"\t"{v}"'.encode() for i, v in page_rows)
        if page_rows:
            body += b"\n"
        return body, "text/tab-separated-values", None

    orig_once, orig_cap = fetch_mod._fetch_once, fetch_mod.SORTED_TOP_CAP
    fetch_mod._fetch_once = fake_fetch_once
    fetch_mod.SORTED_TOP_CAP = 6
    try:
        q = "SELECT DISTINCT ?id ?val\nWHERE {\n  ?s ?p ?id .\n  ?s ?q ?val .\n}\n"
        out = tmp_path / "out.tsv"
        status = fetch_mod._fetch_paged(q, "http://fake/sparql", str(out), 30, 0, None, 3, lambda *a, **k: None, 0)
    finally:
        fetch_mod._fetch_once = orig_once
        fetch_mod.SORTED_TOP_CAP = orig_cap

    assert status == "partial"
    lines = out.read_text().splitlines()
    assert lines[0] == "?id\t?val"
    # everything fetchable below the cap is present and correct - no silent gaps, no fabricated rows
    assert [tuple(l.split("\t")) for l in lines[1:]] == [(f'"{i}"', f'"v{i}"') for i in range(1, 7)]
