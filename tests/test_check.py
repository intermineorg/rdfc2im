"""`rdfc2im check` guards on the generated mine configuration."""
import os
import textwrap

from lxml import etree

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


# ------------------------------------------------------- gen_project outputs
LIVE_MODEL = """<model name="genomic" package="org.intermine.model.bio">
<class name="OntologyTerm" is-interface="true">
  <attribute name="identifier" type="java.lang.String"/>
</class>
<class name="Protein" is-interface="true">
  <attribute name="primaryAccession" type="java.lang.String"/>
</class>
</model>"""
# What rdfc2im works against is wider: extra_allow pulls in AnatomyTerm, which the mine lacks.
WORKING_MODEL = LIVE_MODEL.replace("</model>",
    '<class name="AnatomyTerm" extends="OntologyTerm" is-interface="true">'
    '<attribute name="namespace" type="java.lang.String"/></class></model>')


def _project_setup(tmp_path, cols):
    from rdfc2im.model import InterMineModel as IM
    (tmp_path / "core.xml").write_text(WORKING_MODEL)
    (tmp_path / "live.xml").write_text(LIVE_MODEL)
    (tmp_path / "k_keys.properties").write_text("OntologyTerm.key_identifier=identifier\n")
    m = IM(); m.load_xml(str(tmp_path / "core.xml")); m.load_keys(str(tmp_path / "k_keys.properties")); m.finalize()
    m.live = IM(); m.live.load_xml(str(tmp_path / "live.xml")); m.live.finalize()
    out_root = tmp_path / "out"; (out_root / "uberon").mkdir(parents=True)
    write_tsv(str(out_root / "uberon" / "columns.tsv"), cols, COL_COLUMNS)
    return m, str(out_root), str(tmp_path / "_mine")


def test_classes_absent_from_the_live_model_are_carried_into_additions(tmp_path):
    """AnatomyTerm exists only in uberon_additions.xml, which HumanMine does not run.

    `check` was happy because rdfc2im loads it via curation/extra_allow.txt, but the
    deployed mine has no such class and the load would die with "class does not exist".
    """
    from rdfc2im.project import gen_project
    m, out_root, mine = _project_setup(tmp_path, [
        _col(0, "AnatomyTerm", "identifier", required="yes"),
        _col(1, "AnatomyTerm", "namespace"),
        _col(2, "Protein", "primaryAccession"),
    ])
    gen_project(out_root, mine, m, "humanmine-items", "/data", None, {}, None,
                source_version="4.3.0", log=lambda *a: None)
    add = etree.parse(os.path.join(mine, "humanmine-items_additions.xml")).getroot()
    classes = {c.get("name"): c for c in add.iter("class")}
    assert "AnatomyTerm" in classes, "class missing from the live model must be carried"
    assert classes["AnatomyTerm"].get("extends") == "OntologyTerm"
    assert {f.get("name") for f in classes["AnatomyTerm"]} >= {"namespace"}
    # Protein.primaryAccession is already in the live model - do not re-declare it
    assert "Protein" not in classes


def test_sources_carry_the_bio_sources_project_version(tmp_path):
    """Without version=, addSourceDependencies asks for org.intermine:humanmine-items:5.0.+
    and never finds the 4.3.0 jar that humanmine-bio-sources installs."""
    from rdfc2im.project import gen_project
    m, out_root, mine = _project_setup(tmp_path, [_col(0, "Protein", "primaryAccession", required="yes")])
    gen_project(out_root, mine, m, "humanmine-items", "/data", None, {}, None,
                source_version="4.3.0", log=lambda *a: None)
    src = etree.parse(os.path.join(mine, "project.xml")).getroot().find(".//source")
    assert src.get("version") == "4.3.0"
    assert src.get("type") == "humanmine-items"


def test_definitions_from_replaced_sources_are_carried(tmp_path):
    """Replacing a source removes its _additions.xml, and with it any class it declared.

    GOTerm is not in the bio core model - it exists only in go/go-annotation/interpro-go/
    uniprot/psi-complexes additions.  `rdfc2im project` drops the stock `go` and `uniprot`
    sources, so a mine running only our sources has no GOTerm and the load dies in
    ItemToObjectTranslator with `class "GOTerm" does not exist`.  Being present in the live
    model is not enough: the live model is built with those sources still in place.
    """
    from rdfc2im.model import InterMineModel as IM
    from rdfc2im.project import gen_project
    core = ('<model name="genomic" package="org.intermine.model.bio">'
            '<class name="OntologyTerm" is-interface="true">'
            '<attribute name="identifier" type="java.lang.String"/></class></model>')
    go_add = ('<classes><class name="GOTerm" extends="OntologyTerm" is-interface="true">'
              '<attribute name="namespace" type="java.lang.String"/></class></classes>')
    (tmp_path / "core.xml").write_text(core)
    (tmp_path / "go_additions.xml").write_text(go_add)
    (tmp_path / "k_keys.properties").write_text("OntologyTerm.key_identifier=identifier\n")
    m = IM()
    m.load_xml(str(tmp_path / "core.xml"))
    m.load_xml(str(tmp_path / "go_additions.xml"))
    m.load_keys(str(tmp_path / "k_keys.properties"))
    m.finalize()
    # the live mine HAS GOTerm - it still runs the go source we are about to replace
    m.live = IM()
    m.live.load_xml(str(tmp_path / "core.xml"))
    m.live.load_xml(str(tmp_path / "go_additions.xml"))
    m.live.finalize()

    out_root = tmp_path / "out"
    (out_root / "go").mkdir(parents=True)
    write_tsv(str(out_root / "go" / "columns.tsv"), [
        _col(0, "GOTerm", "identifier", required="yes"),
        _col(1, "GOTerm", "namespace"),
    ], COL_COLUMNS)
    mine = str(tmp_path / "_mine")
    gen_project(str(out_root), mine, m, "humanmine-items", "/data", None,
                {"go": {"replaces": ["go"]}}, None, source_version="4.3.0", log=lambda *a: None)
    add = etree.parse(os.path.join(mine, "humanmine-items_additions.xml")).getroot()
    classes = {c.get("name"): c for c in add.iter("class")}
    assert "GOTerm" in classes, "a class from a replaced source must be carried"
    assert classes["GOTerm"].get("extends") == "OntologyTerm"
    assert {f.get("name") for f in classes["GOTerm"]} == {"identifier", "namespace"}
    # OntologyTerm is core - every mine has it, so do not re-declare it
    assert "OntologyTerm" not in classes


# ------------------------------------------------ replaced-source coverage
def test_replacing_a_source_flags_what_only_it_declared(tmp_path):
    """reactome's whole value is gene/pathway membership; a replacement without it deletes it.

    `rdfc2im project` drops every source named in `replaces`, so a class or field only that
    source declared - and ours never writes - is flagged.  Inferred links and their reverses
    count as written, so a correctly linked replacement stays quiet.
    """
    from rdfc2im.model import InterMineModel as IM
    from rdfc2im.project import replaced_coverage
    core = ('<model name="genomic" package="org.intermine.model.bio">'
            '<class name="Gene" is-interface="true"><attribute name="primaryIdentifier" type="java.lang.String"/></class>'
            '<class name="Pathway" is-interface="true"><attribute name="identifier" type="java.lang.String"/>'
            '<attribute name="name" type="java.lang.String"/></class></model>')
    stock_dir = tmp_path / "sources" / "reactome"
    stock_dir.mkdir(parents=True)
    stock = stock_dir / "reactome_additions.xml"
    stock.write_text(
        '<classes>'
        '<class name="Pathway" is-interface="true">'
        '<attribute name="identifier" type="java.lang.String"/>'
        '<collection name="genes" referenced-type="Gene" reverse-reference="pathways"/></class>'
        '<class name="Gene" is-interface="true">'
        '<collection name="pathways" referenced-type="Pathway" reverse-reference="genes"/></class>'
        '</classes>')
    (tmp_path / "core.xml").write_text(core)
    m = IM()
    m.load_xml(str(tmp_path / "core.xml"))
    m.load_xml(str(stock))
    m.finalize()

    out_root = tmp_path / "out"
    (out_root / "reactome").mkdir(parents=True)

    # 1. ours loads pathway identity only: the membership links are lost
    write_tsv(str(out_root / "reactome" / "columns.tsv"), [
        _col(0, "Pathway", "identifier", required="yes"),
        _col(1, "Pathway", "name"),
    ], COL_COLUMNS)
    got = {cls: (missing, whole) for cls, missing, whole in replaced_coverage(m, str(out_root), "reactome", "reactome")}
    assert got["Gene"] == (["pathways"], True), got
    assert got["Pathway"] == (["genes"], False), got

    # 2. ours links pathways to genes: the link AND its reverse count as written
    write_tsv(str(out_root / "reactome" / "columns.tsv"), [
        _col(0, "Pathway", "identifier", required="yes"),
        _col(1, "Gene", "primaryIdentifier", via="Pathway.genes"),
    ], COL_COLUMNS)
    assert replaced_coverage(m, str(out_root), "reactome", "reactome") == []


def test_replacing_a_source_with_no_additions_is_silent(tmp_path):
    from rdfc2im.model import InterMineModel as IM
    from rdfc2im.project import replaced_coverage
    (tmp_path / "core.xml").write_text(
        '<model name="genomic" package="org.intermine.model.bio">'
        '<class name="Gene" is-interface="true"><attribute name="symbol" type="java.lang.String"/></class></model>')
    m = IM(); m.load_xml(str(tmp_path / "core.xml")); m.finalize()
    out_root = tmp_path / "out"; (out_root / "hgnc").mkdir(parents=True)
    write_tsv(str(out_root / "hgnc" / "columns.tsv"), [_col(0, "Gene", "symbol", required="yes")], COL_COLUMNS)
    assert replaced_coverage(m, str(out_root), "hgnc", "hgnc") == []
