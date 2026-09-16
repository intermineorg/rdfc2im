"""`rdfc2im check` guards on the generated mine configuration."""
import os
import textwrap

from rdfc2im.model import InterMineModel
from rdfc2im.mapping import write_tsv
from rdfc2im.project import check_project
from rdfc2im.pipeline import COL_COLUMNS

MODEL = """<model name="genomic" package="org.intermine.model.bio">
<class name="OntologyTerm" is-interface="true">
  <attribute name="identifier" type="java.lang.String"/>
  <collection name="parents" referenced-type="OntologyTerm"/>
</class>
<class name="GOTerm" extends="OntologyTerm" is-interface="true"/>
<class name="Protein" is-interface="true">
  <attribute name="primaryAccession" type="java.lang.String"/>
  <collection name="keywords" referenced-type="OntologyTerm"/>
</class>
</model>"""
PROJECT = """<project type="bio"><sources>
  <source name="humanmine-src" type="humanmine-items"/>
</sources></project>"""


def _setup(tmp_path, cols):
    (tmp_path / "core.xml").write_text(MODEL)
    (tmp_path / "t_keys.properties").write_text(
        "OntologyTerm.key_identifier=identifier\nProtein.key_primaryaccession=primaryAccession\n")
    m = InterMineModel()
    m.load_xml(str(tmp_path / "core.xml"))
    m.load_keys(str(tmp_path / "t_keys.properties"))
    m.finalize()
    m.live = None
    out_root = tmp_path / "out"
    (out_root / "src").mkdir(parents=True)
    mine = tmp_path / "_mine"
    mine.mkdir()
    (mine / "project.xml").write_text(PROJECT)
    write_tsv(str(out_root / "src" / "columns.tsv"), cols, COL_COLUMNS)
    return m, str(out_root), str(mine)


def _col(pos, cls, field, via="", required="no"):
    d = {k: "" for k in COL_COLUMNS}
    d.update(table="main", position=pos, column=f"c{pos}", variable=f"v{pos}",
             im_class=cls, im_field=field, via=via, kind="iri", status="sure", required=required)
    return d


def _run(m, out_root, mine):
    msgs = []
    rc = check_project(out_root, mine, m, "humanmine-items", None, {}, log=msgs.append)
    return rc, msgs


def test_widening_via_is_flagged(tmp_path):
    """GOTerm into Protein.keywords type-checks (GOTerm is-a OntologyTerm) but is wrong.

    That is how spec D4 went unnoticed, so `check` must say something about a via whose
    declared range is a strict ancestor of what the column actually carries.
    """
    m, out_root, mine = _setup(tmp_path, [
        _col(0, "Protein", "primaryAccession", required="yes"),
        _col(1, "GOTerm", "identifier", via="Protein.keywords"),
    ])
    rc, msgs = _run(m, out_root, mine)
    assert rc == 0, "a widening via is a note, not a hard failure"
    assert any("widens" in x and "Protein.keywords" in x for x in msgs), msgs


def test_exact_range_via_is_not_flagged(tmp_path):
    """The ordinary case - OntologyTerm into an OntologyTerm collection - must stay quiet."""
    m, out_root, mine = _setup(tmp_path, [
        _col(0, "GOTerm", "identifier", required="yes"),
        _col(1, "OntologyTerm", "identifier", via="GOTerm.parents"),
    ])
    rc, msgs = _run(m, out_root, mine)
    assert rc == 0
    assert not any("widens" in x for x in msgs), msgs


def test_via_with_an_unrelated_range_is_a_hard_problem(tmp_path):
    m, out_root, mine = _setup(tmp_path, [
        _col(0, "GOTerm", "identifier", required="yes"),
        _col(1, "Protein", "primaryAccession", via="GOTerm.parents"),
    ])
    rc, msgs = _run(m, out_root, mine)
    assert rc == 1
    assert any("has range" in x for x in msgs), msgs
