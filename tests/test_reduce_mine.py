"""reduce_mine.py: trimming a full-mine configuration down to a reduced build's real model.

The webconfig constraint parser is the focus here: a `constraints` entry is an expression
("organism.name=[Organism]", "primaryIdentifier != null"), not a bare path, and treating it as
one made every constraint-bearing widget look unresolvable. That over-flagged 10 widgets where
InterMine's own validator reports 5 - and the extras were working widgets that would have been
deleted from the UI. See CONSTRAINT_OP_RE's comment in tools/reduce_mine.py.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import reduce_mine  # noqa: E402


# ------------------------------------------------------------------ constraint_path

def test_constraint_path_strips_equals_value():
    assert reduce_mine.constraint_path("organism.name=[Organism]") == "organism.name"


def test_constraint_path_strips_spaced_operator():
    assert reduce_mine.constraint_path("organism.taxonId = [list]") == "organism.taxonId"


def test_constraint_path_strips_not_null():
    # "!=" must win over "=", or the path keeps a trailing "!" and never resolves.
    assert reduce_mine.constraint_path("primaryIdentifier != null") == "primaryIdentifier"


def test_constraint_path_strips_dotted_path_with_value():
    assert reduce_mine.constraint_path("pathways.dataSets.name = [DataSet]") == "pathways.dataSets.name"


def test_constraint_path_leaves_bare_path_alone():
    assert reduce_mine.constraint_path("goAnnotation.ontologyTerm.name") \
        == "goAnnotation.ontologyTerm.name"


def test_constraint_path_handles_comparison_operators():
    for expr, want in [("score >= 0.5", "score"), ("score<=1", "score"),
                       ("year > 2000", "year"), ("count < 10", "count")]:
        assert reduce_mine.constraint_path(expr) == want


# ------------------------------------------------------------------ path resolution

MODEL = """<?xml version="1.0"?>
<model name="genomic">
  <class name="Organism" is-interface="true">
    <attribute name="name" type="java.lang.String"/>
    <attribute name="taxonId" type="java.lang.String"/>
  </class>
  <class name="Pathway" is-interface="true">
    <attribute name="name" type="java.lang.String"/>
  </class>
  <class name="BioEntity" is-interface="true">
    <attribute name="primaryIdentifier" type="java.lang.String"/>
    <reference name="organism" referenced-type="Organism"/>
  </class>
  <class name="Gene" extends="BioEntity" is-interface="true">
    <attribute name="symbol" type="java.lang.String"/>
    <collection name="pathways" referenced-type="Pathway"/>
  </class>
</model>
"""


def _model(tmp_path):
    p = tmp_path / "genomic_model.xml"
    p.write_text(MODEL)
    return reduce_mine.load_model_classes(str(p))


def test_resolve_path_follows_references(tmp_path):
    classes, extends = _model(tmp_path)
    assert reduce_mine.resolve_path(classes, extends, "Gene", "organism.name")


def test_resolve_path_is_inheritance_aware(tmp_path):
    # primaryIdentifier is declared on BioEntity, not Gene.
    classes, extends = _model(tmp_path)
    assert reduce_mine.resolve_path(classes, extends, "Gene", "primaryIdentifier")


def test_resolve_path_rejects_missing_field(tmp_path):
    classes, extends = _model(tmp_path)
    assert not reduce_mine.resolve_path(classes, extends, "Gene", "organism.nosuchfield")


def test_resolve_path_rejects_unknown_start_class(tmp_path):
    classes, extends = _model(tmp_path)
    assert not reduce_mine.resolve_path(classes, extends, "ProteinDomain", "name")


def test_constraint_expression_resolves_once_parsed(tmp_path):
    """The regression this file exists for: the raw expression must not resolve, the parsed
    left-hand path must. Without constraint_path the widget looks broken and gets deleted."""
    classes, extends = _model(tmp_path)
    expr = "organism.name=[Organism]"
    assert not reduce_mine.resolve_path(classes, extends, "Gene", expr)
    assert reduce_mine.resolve_path(classes, extends, "Gene", reduce_mine.constraint_path(expr))


# ------------------------------------------------------------------ objectstoresummary

def test_objectstoresummary_keeps_config_keys_and_drops_absent_classes(tmp_path):
    """An earlier version split the key on the first '.', yielding "org" for every class line and
    "autocomplete"/"max" for the config ones - it would have emptied the file, including
    autocomplete.solrurl, which the whole feature depends on."""
    model = tmp_path / "genomic_model.xml"
    model.write_text(MODEL)
    cfg = tmp_path / "objectstoresummary.config.properties"
    cfg.write_text(
        "max.field.values = 200\n"
        "\n"
        "# a comment\n"
        "org.intermine.model.bio.Gene.autocomplete = symbol\n"
        "org.intermine.model.bio.Pathway.autocomplete = name\n"
        "org.intermine.model.bio.ProteinDomain.autocomplete = name shortName\n"
        "org.intermine.model.bio.Interaction.fields = role\n"
        "ignore.counts=org.intermine.model.bio.SequenceFeature.overlappingFeatures\n"
        "autocomplete.solrurl = http://localhost:8983/solr/humanmine-autocomplete\n"
    )
    reduce_mine.cmd_objectstoresummary([str(cfg), str(model)])
    out = cfg.read_text()
    # present in the model -> kept
    assert "Gene.autocomplete = symbol" in out
    assert "Pathway.autocomplete = name" in out
    # absent from the model -> dropped
    assert "ProteinDomain" not in out
    assert "Interaction.fields" not in out
    # non-class configuration -> untouched
    assert "max.field.values = 200" in out
    assert "autocomplete.solrurl = http://localhost:8983/solr/humanmine-autocomplete" in out
    assert "ignore.counts=" in out
    assert "# a comment" in out
