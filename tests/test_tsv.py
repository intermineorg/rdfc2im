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


def test_filter_runs_before_transform_and_blanks_optional_columns(tmp_path):
    """A blank-node parent must be dropped before iri_localname invents an identifier.

    GO's rdfs:subClassOf also points at anonymous OWL class expressions; at full scale
    16,178 of 92,208 rows were blank nodes.  The filter sees the raw term, so it can
    reject `_:nodeID://b123` that `iri_localname` would otherwise turn into `b123`.
    """
    raw = tmp_path / "raw.tsv"
    raw.write_text(
        "?id\t?superclass\n"
        '"GO:0000001"\t<http://purl.obolibrary.org/obo/GO_0044237>\n'
        '"GO:0000002"\t_:nodeID://b2225461165\n'
        '"GO:0000003"\t<http://www.w3.org/2002/07/owl#Thing>\n'
        '"GO:0000004"\t<http://purl.obolibrary.org/obo/BFO_0000050>\n')
    cols = [
        {"column": "id", "variable": "id", "im_class": "GOTerm", "im_field": "identifier",
         "transform": "", "filter": "", "value": "", "kind": "iri", "required": "yes", "position": "0"},
        {"column": "superclass", "variable": "superclass", "im_class": "OntologyTerm",
         "im_field": "identifier", "transform": "iri_localname,replace:_:\\:",
         "filter": "regex:^https?://purl\\.obolibrary\\.org/obo/", "value": "", "kind": "iri",
         "required": "no", "position": "1"},
    ]
    out = tmp_path / "clean.tsv"
    st = clean_table(str(raw), str(out), cols)
    rows = [l.split("\t") for l in out.read_text().rstrip("\n").split("\n")[1:]]
    got = {r[0]: r[1] for r in rows}
    assert got["GO:0000001"] == "GO:0044237"
    assert got["GO:0000004"] == "BFO:0000050", "cross-ontology OBO parents are legitimate"
    # rejected: blanked, not dropped - the term itself still loads
    assert got["GO:0000002"] == "", "blank node must not become an identifier"
    assert got["GO:0000003"] == "", "owl:Thing is not a parent term"
    assert st["rows_out"] == 4 and st["filtered"] == 0


def test_required_column_filter_drops_owl_property_pseudo_terms(tmp_path):
    """OBO ontologies give oboinowl:id to object properties, not just classes.

    A full GO extract yields 11 such rows (part_of, occurs_in, regulates, ...) which
    loaded as GOTerms.  The identifier column is required=yes, so a failing filter must
    drop the whole row, not blank it.
    """
    raw = tmp_path / "raw.tsv"
    raw.write_text(
        "?id\t?label\n"
        '"GO:0000016"\t"lactase activity"\n'
        '"part_of"\t"part of"\n'
        '"UBERON:0000955"\t"brain"\n'
        '"term_tracker_item"\t"term tracker item"\n'
        '"HP:0000001"\t"All"\n')
    cols = [
        {"column": "id", "variable": "id", "im_class": "OntologyTerm", "im_field": "identifier",
         "transform": "", "filter": "regex:^[A-Za-z][A-Za-z0-9]*:[0-9]+$", "value": "",
         "kind": "iri", "required": "yes", "position": "0"},
        {"column": "label", "variable": "label", "im_class": "OntologyTerm", "im_field": "name",
         "transform": "", "filter": "", "value": "", "kind": "literal", "required": "no",
         "position": "1"},
    ]
    out = tmp_path / "clean.tsv"
    st = clean_table(str(raw), str(out), cols)
    ids = [l.split("\t")[0] for l in out.read_text().rstrip("\n").split("\n")[1:]]
    assert ids == ["GO:0000016", "UBERON:0000955", "HP:0000001"], ids
    assert st["rows_out"] == 3 and st["filtered"] == 2
