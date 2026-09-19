import re
import urllib.parse
import urllib.request
import rdfc2im.fetch as fetch_mod
from rdfc2im.fetch import _json_to_tsv, _csv_to_tsv, _term, with_limit, _order_by, _sniff, _NoRedirectPost
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

def test_paged_fetch_recovers_all_rows_past_a_silent_per_request_truncation(tmp_path):
    """Regression for id.nlm.nih.gov/mesh/sparql: unlike Virtuoso's Sorted TOP cap, this endpoint
    never errors - a request for more rows than its own ceiling just silently returns fewer, with
    an ordinary HTTP 200. The old code read "got fewer than requested" as "reached the end of the
    table" unconditionally, so a genuinely larger table (here 20 rows, silently capped at 6 per
    request, first page asked for 10) was silently truncated to one short page and reported as
    fully fetched. Paging must instead probe past a short page before believing it, discover the
    true per-request ceiling, and keep going at that smaller page size until the table is really
    exhausted - recovering every row, not just the first truncated page."""
    rows = [(str(i), f"v{i}") for i in range(1, 21)]  # 20 real rows
    TRUNCATE_AT = 6  # this fake endpoint never returns more than this, no matter what's asked

    def fake_fetch_once(q, ep, timeout):
        if "COUNT(*)" in q:
            return None, None, "count unavailable in this fake endpoint"  # forces the OFFSET fallback
        off_m = re.search(r"OFFSET (\d+)", q)
        lim_m = re.search(r"LIMIT (\d+)", q)
        offset, lim = int(off_m.group(1)), int(lim_m.group(1))
        page_rows = rows[offset: offset + min(lim, TRUNCATE_AT)]  # silent truncation, no error
        body = b"?id\t?val\n" + b"\n".join(f'"{i}"\t"{v}"'.encode() for i, v in page_rows)
        if page_rows:
            body += b"\n"
        return body, "text/tab-separated-values", None

    orig_once = fetch_mod._fetch_once
    fetch_mod._fetch_once = fake_fetch_once
    try:
        q = "SELECT DISTINCT ?id ?val\nWHERE {\n  ?s ?p ?id .\n  ?s ?q ?val .\n}\n"
        out = tmp_path / "out.tsv"
        # page=10, bigger than TRUNCATE_AT=6, so the very first request already gets truncated
        status = fetch_mod._fetch_paged(q, "http://fake/sparql", str(out), 30, 0, None, 10, lambda *a, **k: None, 0)
    finally:
        fetch_mod._fetch_once = orig_once

    assert status == "fetched"
    lines = out.read_text().splitlines()
    assert lines[0] == "?id\t?val"
    # every real row recovered, in order, nothing missing and nothing fabricated
    assert [tuple(l.split("\t")) for l in lines[1:]] == [(f'"{i}"', f'"v{i}"') for i in range(1, 21)]

def test_paged_fetch_stops_loudly_at_the_sorted_top_cap_instead_of_truncating_silently(tmp_path):
    """Regression for the Virtuoso "Sorted TOP" cap: a table whose true size exceeds the cap
    used to have its last page's request simply fail and get swallowed as an ordinary "partial"
    outcome indistinguishable from a short final page, leaving a file that looked complete.
    Paging must instead recognise the cap *before* making a doomed request and report "partial"
    distinctly, with every row up to the cap still written (nothing already fetched is lost)."""
    rows = [(str(i), f"v{i}") for i in range(1, 11)]  # 10 rows, cap set to 6

    def fake_fetch_once(q, ep, timeout):
        if "COUNT(*)" in q:
            return None, None, "count unavailable in this fake endpoint"  # forces the OFFSET fallback
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

NCBIGENE_SHAPED = (
    "PREFIX dct: <http://purl.org/dc/terms/>\n"
    "PREFIX insdc: <http://ddbj.nig.ac.jp/ontologies/nucleotide/>\n"
    "PREFIX taxid: <http://identifiers.org/taxonomy/>\n"
    "SELECT DISTINCT ?id ?taxid ?syn\n"
    "FROM <http://example.org/dataset>\n"
    "WHERE {\n"
    "  ?Gene a insdc:Gene .\n"
    "  ?Gene dct:identifier ?id .\n"
    "  ?Gene taxid:has ?taxid .\n"
    "  VALUES ?taxid { taxid:9606 }\n"
    "  OPTIONAL { ?Gene insdc:synonym ?syn . }\n"
    "}\n"
)

def test_default_batch_size_is_the_benchmarked_sweet_spot():
    """Pins the value, not the live benchmark: 1000 measured ~34% faster end-to-end than 500
    against the real ncbigene endpoint (0.68 vs 0.54 ms/gene, but half as many batches), and
    per-gene cost rises further above that - 5000 hits a hard Virtuoso argument-count ceiling
    (HTTP 400, SP030). See the comment above DEFAULT_BATCH_SIZE for the full measurement."""
    assert fetch_mod.DEFAULT_BATCH_SIZE == 1000

def test_required_only_strips_optionals_but_keeps_the_required_key():
    stripped = fetch_mod._required_only(NCBIGENE_SHAPED)
    assert "OPTIONAL" not in stripped
    assert "?id" in stripped.split("WHERE", 1)[1]
    assert stripped.count("{") == stripped.count("}")

def test_required_only_handles_nested_optionals():
    nested = ("SELECT DISTINCT ?id ?a ?b\nWHERE {\n  ?s ?p ?id .\n"
              "  OPTIONAL {\n    ?s ?q ?x .\n    OPTIONAL { ?x ?r ?a . }\n    OPTIONAL { ?x ?t ?b . }\n  }\n}\n")
    stripped = fetch_mod._required_only(nested)
    assert stripped is not None and "OPTIONAL" not in stripped
    assert stripped.count("{") == stripped.count("}")

def test_required_only_refuses_a_key_only_reachable_through_optional():
    # ?syn only exists inside the OPTIONAL - stripping it removes ?syn's only occurrence in WHERE,
    # which is fine for _required_only itself (it still balances), but the caller's own "is this
    # variable still present" check is what must catch it; confirm that check would actually fail.
    stripped = fetch_mod._required_only(NCBIGENE_SHAPED)
    assert "?syn" not in stripped.split("WHERE", 1)[1]

def test_inject_values_targets_the_outer_where_close_only():
    q = fetch_mod._inject_values(NCBIGENE_SHAPED, "id", ['"1"', '"2"'])
    assert 'VALUES ?id { "1" "2" }' in q
    # inserted once, right before the outer WHERE's closing brace, not inside the OPTIONAL
    assert q.index('VALUES ?id') < q.rindex("}")
    assert q.count("VALUES") == 2  # the original ?taxid VALUES plus the new one

def test_count_rows_wraps_the_query_and_parses_the_result(tmp_path):
    def fake_fetch_once(q, ep, timeout):
        assert "COUNT(*)" in q and "SELECT DISTINCT ?id ?taxid ?syn" in q
        # regression: an earlier version rebuilt the query from fragments and dropped the PREFIX
        # declarations, which is invalid SPARQL and fails as an ordinary request error - the two
        # look identical unless a test pins the prefixes actually surviving into the count query.
        assert "PREFIX dct:" in q and "PREFIX taxid:" in q
        return b'?rdfc2im_c\n"239974"^^<http://www.w3.org/2001/XMLSchema#integer>\n', "text/tab-separated-values", None

    orig = fetch_mod._fetch_once
    fetch_mod._fetch_once = fake_fetch_once
    try:
        assert fetch_mod._count_rows(NCBIGENE_SHAPED, "http://fake/sparql", 30) == 239974
    finally:
        fetch_mod._fetch_once = orig

def test_fetch_paged_recovers_past_the_cap_by_batching_the_required_key_domain(tmp_path):
    """End-to-end regression for the real fix: a table whose true count exceeds the cap (10 rows,
    fake cap 6) must come back COMPLETE via VALUES-batching over ?id's required-only domain - not
    truncated the way plain OFFSET paging would leave it. Batches of size 4 exercise more than
    one batch. Genes without a synonym (row for id "5") must still appear, with a blank last cell,
    since OPTIONAL rows are exactly what the required-only domain query must not lose."""
    genes = [str(i) for i in range(1, 11)]  # true domain: 10 genes
    synonyms = {"1": ["A1", "A2"], "3": ["C1"], "5": [], "9": ["I1", "I2", "I3"]}  # rest: one, unnamed

    def fake_fetch_once(q, ep, timeout):
        if "COUNT(*)" in q:
            return (b'?rdfc2im_c\n"14"^^<http://www.w3.org/2001/XMLSchema#integer>\n',
                    "text/tab-separated-values", None)  # 10 genes, 4 with >1 or 0 synonym rows -> 14 total
        if "SELECT DISTINCT ?id\n" in q:
            assert "OPTIONAL" not in q  # this must be the required-only domain query
            body = b"?id\n" + b"\n".join(f'"{g}"'.encode() for g in genes) + b"\n"
            return body, "text/tab-separated-values", None
        m = re.search(r'VALUES \?id \{ (.*?) \}', q)
        assert m, f"expected a VALUES-batched query, got: {q!r}"
        batch_ids = re.findall(r'"(\d+)"', m.group(1))
        rows = []
        for g in batch_ids:
            syns = synonyms.get(g, [f"S{g}"])
            if not syns:
                rows.append((g, ""))
            for s in syns:
                rows.append((g, s))
        out_lines = []
        for g, s in rows:
            syn_term = f'"{s}"' if s else ""
            out_lines.append(f'"{g}"\t{syn_term}')
        body = ("?id\t?syn\n" + "\n".join(out_lines)).encode()
        if rows:
            body += b"\n"
        return body, "text/tab-separated-values", None

    orig_once, orig_cap = fetch_mod._fetch_once, fetch_mod.SORTED_TOP_CAP
    fetch_mod._fetch_once = fake_fetch_once
    fetch_mod.SORTED_TOP_CAP = 6
    try:
        out = tmp_path / "out.tsv"
        status = fetch_mod._fetch_paged(NCBIGENE_SHAPED.replace("?taxid ?syn", "?syn"), "http://fake/sparql",
                                        str(out), 30, 0, None, 3, lambda *a, **k: None, 0)
    finally:
        fetch_mod._fetch_once = orig_once
        fetch_mod.SORTED_TOP_CAP = orig_cap

    assert status == "fetched"  # complete, not "partial" - batching got past the cap entirely
    lines = out.read_text().splitlines()
    assert lines[0] == "?id\t?syn"
    got = {}
    for l in lines[1:]:
        gid, syn = l.split("\t")
        got.setdefault(gid.strip('"'), []).append(syn.strip('"'))
    assert set(got.keys()) == set(genes)  # every gene present, including id "5" with no synonym
    assert got["1"] == ["A1", "A2"] and got["9"] == ["I1", "I2", "I3"] and got["5"] == [""]


def test_fetch_paged_reacts_to_a_smaller_cap_discovered_only_once_paging_starts(tmp_path):
    """TogoVar's Virtuoso allows only 10000 rows (offset+limit) even though the upfront COUNT
    pre-check only distrusts a table past SORTED_TOP_CAP (RDF Portal's own, much larger, cap) -
    confirmed live. A cap hit discovered mid-paging, not just the upfront estimate, must also
    trigger the batching fallback, discarding whatever partial pages this run already wrote."""
    genes = [str(i) for i in range(1, 11)]
    synonyms = {"1": ["A1", "A2"], "3": ["C1"], "5": [], "9": ["I1", "I2", "I3"]}
    pages = {"n": 0}

    def fake_fetch_once(q, ep, timeout):
        if "COUNT(*)" in q:
            return (b'?rdfc2im_c\n"14"^^<http://www.w3.org/2001/XMLSchema#integer>\n',
                    "text/tab-separated-values", None)  # under SORTED_TOP_CAP - upfront check passes
        if "SELECT DISTINCT ?id\n" in q:
            body = b"?id\n" + b"\n".join(f'"{g}"'.encode() for g in genes) + b"\n"
            return body, "text/tab-separated-values", None
        if "VALUES ?id" in q:
            m = re.search(r'VALUES \?id \{ (.*?) \}', q)
            batch_ids = re.findall(r'"(\d+)"', m.group(1))
            rows = []
            for g in batch_ids:
                syns = synonyms.get(g, [f"S{g}"])
                if not syns:
                    rows.append((g, ""))
                for s in syns:
                    rows.append((g, s))
            lines = [f'"{g}"\t' + (f'"{s}"' if s else "") for g, s in rows]
            body = ("?id\t?syn\n" + "\n".join(lines)).encode()
            if rows:
                body += b"\n"
            return body, "text/tab-separated-values", None
        pages["n"] += 1
        if pages["n"] == 1:
            body = b"?id\t?syn\n" + b"\n".join(f'"{g}"\t""'.encode() for g in genes[:3]) + b"\n"
            return body, "text/tab-separated-values", None
        return None, None, ("HTTP 500 Internal Server Error (POST, Accept: application/sparql-results+json): "
                            "Virtuoso 22023 Error SR353: Sorted TOP clause specifies more then 6 rows "
                            "to sort. Only 3 are allowed")

    orig_once, orig_cap = fetch_mod._fetch_once, fetch_mod.SORTED_TOP_CAP
    fetch_mod._fetch_once = fake_fetch_once
    fetch_mod.SORTED_TOP_CAP = 200  # far above this fake endpoint's real (smaller) cap
    try:
        out = tmp_path / "out.tsv"
        status = fetch_mod._fetch_paged(NCBIGENE_SHAPED.replace("?taxid ?syn", "?syn"), "http://fake/sparql",
                                        str(out), 30, 0, None, 3, lambda *a, **k: None, 0)
    finally:
        fetch_mod._fetch_once = orig_once
        fetch_mod.SORTED_TOP_CAP = orig_cap

    assert status == "fetched"  # complete via batching, not stuck with only page 1's 3 rows
    got = {}
    for l in out.read_text().splitlines()[1:]:
        gid, syn = l.split("\t")
        got.setdefault(gid.strip('"'), []).append(syn.strip('"'))
    assert set(got.keys()) == set(genes)
    assert got["9"] == ["I1", "I2", "I3"]


def _post_request(query):
    return urllib.request.Request(
        "http://example.org/sparql",
        data=urllib.parse.urlencode({"query": query}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "text/tab-separated-values"},
    )


def test_no_redirect_post_converts_short_query_to_get():
    handler = _NoRedirectPost()
    req = handler.redirect_request(_post_request("SELECT * WHERE { ?s ?p ?o }"), None, 302, "Found",
                                    {}, "http://example.org/redirected/sparql")
    assert req.get_method() == "GET"
    assert req.full_url.startswith("http://example.org/redirected/sparql?")
    assert urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)["query"][0] == "SELECT * WHERE { ?s ?p ?o }"


def test_no_redirect_post_retries_as_post_when_get_url_would_be_too_long():
    long_query = "SELECT * WHERE { " + " || ".join(f'?x = "{i}"' for i in range(500)) + " }"
    handler = _NoRedirectPost()
    req = handler.redirect_request(_post_request(long_query), None, 302, "Found",
                                    {}, "http://example.org/redirected/sparql")
    assert req.get_method() == "POST"
    assert req.full_url == "http://example.org/redirected/sparql"
    assert urllib.parse.parse_qs(req.data.decode())["query"][0] == long_query
