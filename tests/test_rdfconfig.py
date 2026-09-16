import os, textwrap
from rdfc2im.rdfconfig import parse_model, load_config, parse_prefixes

def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(textwrap.dedent(text))
    return str(p)

def test_parse_model_cardinality_subnodes_and_bnodes(tmp_path):
    p = write(tmp_path, "model.yaml", """\
    - Thing ex:1:
      - a: ex:Thing
      - ex:id:
        - id: "1"
      - ex:syn*:
        - syn: "alias"
      - ex:link?:
        - other: Other
      - ex:union+:
        - u: [Other, Third]
      - ex:nested:
        - []:
          - ex:deep:
            - deep: 5
      - ex:bare*: Other

    - Other ex:2:
      - a: ex:Other
    - Third <http://x/3>:
      - a: [ex:A, ex:B]
    """)
    subs = parse_model(p)
    assert [s.name for s in subs] == ["Thing", "Other", "Third"]
    t = subs[0]
    preds = {q.curie: q for q in t.predicates}
    assert preds["ex:id"].cardinality == "" and preds["ex:syn"].multi and preds["ex:link"].optional
    assert preds["ex:link"].objects[0].sub_subjects == ["Other"]
    assert preds["ex:union"].objects[0].sub_subjects == ["Other", "Third"]
    bn = preds["ex:nested"].objects[0]
    assert bn.inline is not None and bn.inline.predicates[0].curie == "ex:deep"
    assert preds["ex:bare"].objects[0].sub_subjects == ["Other"]
    assert subs[2].types == ["ex:A", "ex:B"] and subs[2].example == "<http://x/3>"

def test_crlf_and_tabs(tmp_path):
    p = tmp_path / "model.yaml"
    p.write_bytes(b"- S ex:1:\r\n  - a: ex:S\r\n\t- ex:p:\r\n\t\t- v: \"x\"\r\n")
    subs = parse_model(str(p))
    assert subs[0].predicates[0].objects[0].name == "v"

def test_prefix_expand(tmp_path):
    write(tmp_path, "model.yaml", "- S ex:1:\n  - a: ex:S\n")
    write(tmp_path, "prefix.yaml", "ex: <http://example.org/>\nobo: <http://purl.obolibrary.org/obo/>\n")
    write(tmp_path, "endpoint.yaml", "endpoint:\n  - https://ep/sparql\n  - graph:\n    - http://g1\n")
    c = load_config(str(tmp_path))
    assert c.expand("ex:S") == "http://example.org/S"
    assert c.expand("<http://a/b>") == "http://a/b"
    assert c.local_name("obo:GO_1") == "GO_1"
    assert c.endpoint == "https://ep/sparql" and c.graphs == ["http://g1"]
