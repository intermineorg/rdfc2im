import os
from rdfc2im.tsv import clean_term, apply_transform, clean_table, read_sparql_tsv

def test_clean_term():
    assert clean_term('<http://x/y>') == "http://x/y"
    assert clean_term('"abc"') == "abc"
    assert clean_term('"12"^^<http://www.w3.org/2001/XMLSchema#integer>') == "12"
    assert clean_term('"1.5"^^xsd:decimal') == "1.5"
    assert clean_term('"hi"@en') == "hi"
    assert clean_term('"a\\tb\\nc \\"q\\""') == 'a b c "q"'
    assert clean_term("") == ""

def test_transforms():
    assert apply_transform("http://purl.obolibrary.org/obo/GO_0008150", "iri_localname,replace:_:\\:") == ["GO:0008150"]
    assert apply_transform("11998", "prefix:HGNC:") == ["HGNC:11998"]
    assert apply_transform("HGNC:11998", "prefix:HGNC:") == ["HGNC:11998"]
    assert apply_transform("2017-08-10", "regex:^(\\d{4})") == ["2017"]
    assert apply_transform("A, B ,C", "split:,") == ["A", "B", "C"]
    assert apply_transform("KW-0001", "strip_prefix:KW-,int") == ["0001"]

def test_clean_table(tmp_path):
    raw = tmp_path / "raw.tsv"
    raw.write_text('?id\t?syn\t?go\n'
                   '"1"\t"a"\t<http://purl.obolibrary.org/obo/GO_0000001>\n'
                   '"1"\t"a"\t<http://purl.obolibrary.org/obo/GO_0000001>\n'
                   '"2"\t"x\\ty"\t\n'
                   '"3"\t"b"\t<http://other/NOPE>\n')
    cols = [dict(column="id", variable="id", im_class="Gene", im_field="primaryIdentifier", transform="", filter="", value="", kind="literal", required="yes"),
            dict(column="syn", variable="syn", im_class="Synonym", im_field="value", transform="", filter="", value="", kind="literal", required="no"),
            dict(column="go", variable="go", im_class="GOTerm", im_field="identifier", transform="iri_localname,replace:_:\\:", filter="regex:^http://purl", value="", kind="iri", required="no"),
            dict(column="c", variable="", im_class="Organism", im_field="taxonId", transform="", filter="", value="9606", kind="const", required="no")]
    out = tmp_path / "out.tsv"
    st = clean_table(str(raw), str(out), cols)
    lines = out.read_text().splitlines()
    assert lines[0] == "Gene.primaryIdentifier\tSynonym.value\tGOTerm.identifier\tOrganism.taxonId"
    assert lines[1] == "1\ta\tGO:0000001\t9606"
    assert lines[2] == "2\tx y\t\t9606"
    assert lines[3] == "3\tb\t\t9606"       # filter on a non-required column blanks, does not drop
    assert st["rows_in"] == 4 and st["rows_out"] == 3   # duplicate removed

def test_virtuoso_header_and_crlf(tmp_path):
    raw = tmp_path / "v.tsv"
    raw.write_bytes(b'"id"\t"label"\r\n"GO:1"\t"x"\r\n')
    rows = read_sparql_tsv(str(raw))
    assert rows[0] == ["id", "label"] and rows[1] == ["GO:1", "x"]
