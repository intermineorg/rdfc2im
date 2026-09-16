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
