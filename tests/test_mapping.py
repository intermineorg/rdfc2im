import os, textwrap
from rdfc2im.model import InterMineModel
from rdfc2im.mapping import translate, Knowledge, read_tsv, write_tsv, three_way_merge, CW_COLUMNS
from rdfc2im.sssom import read_sssom, write_sssom
from rdfc2im.sparql import QueryBuilder, query_rows, table_names

MODEL = """<model name="genomic" package="org.intermine.model.bio">
<class name="BioEntity" is-interface="true">
  <attribute name="primaryIdentifier" type="java.lang.String" term="http://purl.org/dc/terms/identifier"/>
  <attribute name="symbol" type="java.lang.String" term="http://www.w3.org/2004/02/skos/core#prefLabel"/>
  <attribute name="name" type="java.lang.String" term="http://www.w3.org/2000/01/rdf-schema#label"/>
  <reference name="organism" referenced-type="Organism"/>
  <collection name="synonyms" referenced-type="Synonym" reverse-reference="subject"/>
</class>
<class name="Gene" extends="BioEntity" is-interface="true" term="http://purl.obolibrary.org/obo/SO:0000704">
  <attribute name="description" type="java.lang.String" term="http://purl.org/dc/terms/description"/>
</class>
<class name="Organism" is-interface="true"><attribute name="taxonId" type="java.lang.String"/></class>
<class name="Synonym" is-interface="true"><attribute name="value" type="java.lang.String"/><reference name="subject" referenced-type="BioEntity"/></class>
</model>"""
KEYS = "Gene.key_primaryidentifier=primaryIdentifier\nOrganism.key=taxonId\nSynonym.key_synonym=subject, value\n"

def make_model(tmp_path):
    (tmp_path / "core.xml").write_text(MODEL)
    (tmp_path / "x_keys.properties").write_text(KEYS)
    m = InterMineModel(); m.load_xml(str(tmp_path / "core.xml")); m.load_keys(str(tmp_path / "x_keys.properties")); m.finalize(); m.live = None
    return m

def make_cfg(tmp_path):
    d = tmp_path / "cfg" / "mysrc"; d.mkdir(parents=True)
    (d / "model.yaml").write_text(textwrap.dedent("""\
    - Gene ex:1:
      - a: obo:SO_0000704
      - dct:identifier:
        - id: "1"
      - skos:prefLabel:
        - sym: "ABC"
      - dct:description:
        - desc: "a gene"
      - ex:alias*:
        - alias: "x"
      - ex:mystery:
        - myst: "?"
      - ex:org:
        - org: Org
    - Org tax:9606:
      - a: ex:Organism
      - ex:taxid:
        - taxid: 9606
    """))
    (d / "prefix.yaml").write_text("ex: <http://ex/>\nobo: <http://purl.obolibrary.org/obo/>\ndct: <http://purl.org/dc/terms/>\nskos: <http://www.w3.org/2004/02/skos/core#>\ntax: <http://identifiers.org/taxonomy/>\n")
    (d / "endpoint.yaml").write_text("endpoint:\n  - https://ep/sparql\n")
    return str(d)

def make_knowledge(tmp_path):
    p = tmp_path / "kn.yaml"
    p.write_text(textwrap.dedent("""\
    prefixes: {ex: "http://ex/"}
    subjects:
      - {type: ex:Organism, im_class: Organism, status: sure, basis: test}
    predicates:
      - {pred: ex:alias, class: BioEntity, im: Synonym.value, status: guess, basis: test}
      - {pred: ex:taxid, class: Organism, im: Organism.taxonId, status: sure, basis: test, required: "yes", value: "9606"}
    """))
    return Knowledge(str(p))

def test_translate_tiers_and_merge(tmp_path):
    m = make_model(tmp_path); cfg = make_cfg(tmp_path); kn = make_knowledge(tmp_path)
    out = str(tmp_path / "out")
    r = translate(m, cfg, out, kn, {"consts": {"Organism.taxonId": "9606"}})
    rows = {(x["predicate"], x["column"]): x for x in r["rows"]}
    assert rows[("dct:identifier", "id")]["im_field"] == "primaryIdentifier" and rows[("dct:identifier", "id")]["basis"] == "term"
    assert rows[("dct:identifier", "id")]["required"] == "yes"
    assert rows[("skos:prefLabel", "sym")]["im_field"] == "symbol" and rows[("skos:prefLabel", "sym")]["status"] == "sure"
    assert rows[("dct:description", "desc")]["im_field"] == "description" and rows[("dct:description", "desc")]["im_class"] == "Gene"
    a = rows[("ex:alias", "alias")]
    assert a["im_class"] == "Synonym" and a["status"] == "guess" and a["via"] == "Gene.synonyms" and a["table"] == "main_alias"
    assert rows[("ex:mystery", "myst")]["status"] == "todo"
    assert rows[("ex:org", "org")]["status"] == "link"
    assert rows[("ex:taxid", "taxid")]["im_class"] == "Organism" and rows[("ex:taxid", "taxid")]["required"] == "yes"
    subj = {s["subject"]: s for s in r["subjects"]}
    assert subj["Gene"]["im_class"] == "Gene" and subj["Gene"]["role"] == "root"
    assert subj["Org"]["im_class"] == "Organism"

    # ---- human edits survive regeneration
    cw = os.path.join(out, "mapping_predicates.sssom.tsv")
    meta, disk = read_sssom(cw)
    assert meta["curie_map"]["intermine"].startswith("https://") and any(e["slot_name"] == "ext_status" for e in meta["extension_definitions"])
    for d in disk:
        if d["column"] == "myst":
            d["im_class"], d["im_field"] = "Gene", "name"
        if d["column"] == "alias":
            d["status"] = "drop"
    write_sssom(cw, disk, r["cfg"])
    r2 = translate(m, cfg, out, kn, {})
    rows2 = {(x["predicate"], x["column"]): x for x in r2["rows"]}
    assert rows2[("ex:mystery", "myst")]["im_field"] == "name" and rows2[("ex:mystery", "myst")]["status"] == "human"
    assert rows2[("ex:alias", "alias")]["status"] == "drop"
    assert r2["preserved"] == 2
    # untouched cells took fresh values (consts removed -> const row gone, not stale-protected)
    assert not any(x["kind"] == "const" and "STALE" not in x["note"] for x in r2["rows"])

    # ---- query
    qb = QueryBuilder(r2["cfg"], r2["node_by_key"])
    q, select, params = qb.build("main", query_rows(r2["rows"], "main", True))
    assert "?Gene a obo:SO_0000704 ." in q and "?Gene dct:identifier ?id ." in q
    assert "OPTIONAL { ?Gene skos:prefLabel ?sym . }" in q
    assert "?Gene ex:org ?org ." in q and "?org ex:taxid ?taxid ." in q and "VALUES ?taxid { 9606 }" in q
    assert "PREFIX skos:" in q and params == {"taxid": "9606"}
    assert "alias" not in q
    # rdfs:Resource is "untyped": no type triple
    r2["cfg"].subjects[0].types = ["rdfs:Resource"]
    r2["cfg"].prefixes["rdfs"] = "http://www.w3.org/2000/01/rdf-schema#"
    q3, _, _ = QueryBuilder(r2["cfg"], r2["node_by_key"]).build("main", query_rows(r2["rows"], "main", True))
    assert " a " not in q3

def test_three_way_merge_stale_and_no_base():
    fresh = [{"subject": "S", "predicate": "p", "column": "c", "status": "todo", "im_field": ""}]
    disk = [{"subject": "S", "predicate": "p", "column": "c", "status": "todo", "im_field": "x"},
            {"subject": "S", "predicate": "gone", "column": "g", "status": "human", "im_field": "y", "note": ""}]
    merged, n, stale = three_way_merge(fresh, disk, [], lambda r: (r["subject"], r["predicate"], r["column"]), ["status", "im_field"])
    assert n == 1 and merged[0]["im_field"] == "x" and merged[0]["status"] == "human"
    assert len(stale) == 1 and merged[1]["note"].startswith("STALE")


# ---------------------------------------------------------------- cyclic links
CYCLE_MODEL = """<model name="genomic" package="org.intermine.model.bio">
<class name="OntologyTerm" is-interface="true">
  <attribute name="identifier" type="java.lang.String"/>
  <attribute name="name" type="java.lang.String" term="http://www.w3.org/2000/01/rdf-schema#label"/>
  <collection name="parents" referenced-type="OntologyTerm"/>
</class>
<class name="GOTerm" extends="OntologyTerm" is-interface="true"/>
</model>"""


def _cycle_setup(tmp_path, object_value):
    """A one-subject config whose `rdfs:subClassOf` object is `object_value`.

    rdf-config sources write this two ways.  GO gives an example CURIE
    (`superclass: obo:GO_0044237`); MP and Uberon name the subject itself
    (`subclass_of: Class`), which is a back-edge in the subject graph.  Both must
    end up as an ordinary column bound to OntologyTerm.parents.
    """
    (tmp_path / "core.xml").write_text(CYCLE_MODEL)
    (tmp_path / "k_keys.properties").write_text("OntologyTerm.key_identifier=identifier\n")
    m = InterMineModel()
    m.load_xml(str(tmp_path / "core.xml"))
    m.load_keys(str(tmp_path / "k_keys.properties"))
    m.finalize()
    m.live = None
    d = tmp_path / "cfg" / "onto"
    d.mkdir(parents=True)
    (d / "model.yaml").write_text(textwrap.dedent(f"""\
    - Class obo:GO_0000016:
      - a: owl:Class
      - oboinowl:id:
        - id: "GO:0000016"
      - rdfs:label:
        - label: "lactase activity"
      - rdfs:subClassOf*:
        - parent: {object_value}
    """))
    (d / "prefix.yaml").write_text(
        "obo: <http://purl.obolibrary.org/obo/>\n"
        "oboinowl: <http://www.geneontology.org/formats/oboInOwl#>\n"
        "owl: <http://www.w3.org/2002/07/owl#>\n"
        "rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n")
    (d / "endpoint.yaml").write_text("endpoint:\n  - https://ep/sparql\n")
    p = tmp_path / "kn_cycle.yaml"
    p.write_text(textwrap.dedent("""\
    prefixes: {owl: "http://www.w3.org/2002/07/owl#"}
    subjects:
      - {type: owl:Class, im_class: GOTerm, status: sure, basis: test}
    predicates:
      - {pred: oboinowl:id, class: OntologyTerm, im: OntologyTerm.identifier, status: sure, basis: test, required: "yes"}
      - {pred: rdfs:subClassOf, class: OntologyTerm, im: OntologyTerm.parents, status: guess, basis: test, transform: "iri_localname"}
    """))
    return m, str(d), Knowledge(str(p))


def test_self_referential_link_becomes_a_column(tmp_path):
    """MP/Uberon write `subclass_of: Class` - a link back to the root subject.

    No child node can be created for it (the subject is already placed), so the row
    must degrade to an ordinary IRI column and pick up the OntologyTerm.parents
    knowledge, exactly as GO's CURIE-example form does.
    """
    m, cfg, kn = _cycle_setup(tmp_path, "Class")
    r = translate(m, cfg, str(tmp_path / "out_cycle"), kn, {})
    row = next(x for x in r["rows"] if x["predicate"] == "rdfs:subClassOf")
    assert row["kind"] == "iri", f"back-edge should be a column, got kind={row['kind']!r}"
    # A collection target is rewritten to the target class's key attribute, with the
    # collection itself recorded in `via` - see CURATION_GUIDE.md, "What you edit".
    assert (row["im_class"], row["im_field"]) == ("OntologyTerm", "identifier")
    assert row["via"] == "GOTerm.parents"
    assert row["transform"] == "iri_localname"
    assert row["status"] == "guess"

    # ...and it must reach the generated query, not be dropped as a dangling link.
    table = row["table"]
    qb = QueryBuilder(r["cfg"], r["node_by_key"])
    q, select, _ = qb.build(table, query_rows(r["rows"], table, True))
    assert "rdfs:subClassOf ?parent" in q, q
    assert "parent" in select
    assert not any("cannot be a column" in msg for msg in qb.messages), qb.messages


def test_cycle_and_curie_forms_agree(tmp_path):
    """The two spellings must produce the same mapping - only the example differs."""
    (tmp_path / "a").mkdir(exist_ok=True)
    (tmp_path / "b").mkdir(exist_ok=True)
    m1, c1, k1 = _cycle_setup(tmp_path / "a", "Class")
    m2, c2, k2 = _cycle_setup(tmp_path / "b", "obo:GO_0044237")
    a = translate(m1, c1, str(tmp_path / "a" / "out"), k1, {})
    b = translate(m2, c2, str(tmp_path / "b" / "out"), k2, {})
    ra = next(x for x in a["rows"] if x["predicate"] == "rdfs:subClassOf")
    rb = next(x for x in b["rows"] if x["predicate"] == "rdfs:subClassOf")
    for field in ("kind", "im_class", "im_field", "via", "status", "table", "transform"):
        assert ra[field] == rb[field], f"{field}: cycle={ra[field]!r} curie={rb[field]!r}"


def test_table_with_only_key_columns_is_not_emitted(tmp_path):
    """A satellite table left with nothing but the root key carries no information."""
    m, cfg, kn = _cycle_setup(tmp_path, "Class")
    r = translate(m, cfg, str(tmp_path / "out_empty"), kn, {})
    rows = [dict(x) for x in r["rows"]]
    for x in rows:                      # drop the only non-key column of the satellite
        if x["predicate"] == "rdfs:subClassOf":
            x["status"] = "todo"
    tables = [t for t in table_names(rows) if t != "main"]
    for t in tables:
        assert query_rows(rows, t, True) == [], f"{t} should be dropped, not queried"
