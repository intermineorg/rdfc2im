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


# -------------------------------------------------- collisions and cross products
def test_hand_made_collision_is_a_hard_problem(tmp_path):
    """translate splits collisions; one in columns.tsv means a table was set onto it by hand."""
    from rdfc2im.project import column_collisions
    m, out_root, _ = _setup(tmp_path, [
        _col(0, "Protein", "primaryAccession", required="yes"),
        _col(1, "Protein", "primaryAccession"),      # a second writer of the key's field
    ])
    msgs = column_collisions(m, out_root, "src")
    assert msgs and "only one value would survive" in msgs[0], msgs


def test_untyped_predicate_bound_twice_is_a_cross_product(tmp_path):
    from rdfc2im.project import cross_products
    from rdfc2im.sssom import write_sssom
    from rdfc2im.rdfconfig import RdfConfig
    out_root = tmp_path / "out"; (out_root / "pubmed").mkdir(parents=True)
    base = {"subject": "Pubmed", "predicate": "fabio:hasSubjectTerm", "table": "main", "status": "guess",
            "kind": "iri", "im_class": "MeshTerm", "im_field": "identifier", "filter": "", "value": ""}
    rows = [dict(base, column="mesh_checktag"), dict(base, column="mesh_topicaldescriptor_1"),
            dict(base, column="discriminated", filter="regex:^Q")]     # a filter tells this one apart
    cfg = RdfConfig(name="pubmed", dir=str(tmp_path), subjects=[], prefixes={"fabio": "http://purl.org/spar/fabio/"},
                    endpoint=None, graphs=[], sparql={})
    write_sssom(str(out_root / "pubmed" / "mapping_predicates.sssom.tsv"), rows, cfg)
    msgs = cross_products(str(out_root), "pubmed")
    assert len(msgs) == 1 and "N^2" in msgs[0], msgs
    assert "discriminated" not in msgs[0]


def test_approved_extensions_are_part_of_the_working_model(tmp_path):
    """project puts curation/extensions_additions.xml into the mine, so the model mappings are
    checked against must include it - or a link onto an approved field can never resolve."""
    from rdfc2im.model import load_model
    d = tmp_path / "bio" / "model"; d.mkdir(parents=True)
    (d / "core.xml").write_text('<model name="genomic" package="org.intermine.model.bio">'
        '<class name="Pathway" is-interface="true"><attribute name="identifier" type="java.lang.String"/></class>'
        '<class name="Organism" is-interface="true"><attribute name="taxonId" type="java.lang.String"/></class></model>')
    ext = tmp_path / "extensions_additions.xml"
    ext.write_text('<classes><class name="Pathway" is-interface="true">'
                   '<reference name="organism" referenced-type="Organism"/></class></classes>')
    assert load_model([str(tmp_path / "bio")]).field("Pathway", "organism") is None
    fd = load_model([str(tmp_path / "bio")], extra_additions=[str(ext)]).field("Pathway", "organism")
    assert fd is not None and fd.type == "Organism"


# ------------------------------------------------------------------ priorities
def test_priorities_merge_into_humanmines_own(tmp_path):
    """Two writers of one field with no priority naming both stop the build ("Conflicting values
    for field").  A replacement inherits the replaced source's slot; other writers of ours go in
    before `*`, since sources left to `*` share a rank and conflict with each other too."""
    from rdfc2im.project import merge_priorities
    base = tmp_path / "p.properties"
    base.write_text("# HumanMine\n"
                    "Gene.symbol = ncbi-gene, hgnc, depmap-expression, *\n"
                    "Disease.name = omim, *\n"
                    "Gene.organism = *, gtex\n")
    writers = {"Gene.symbol": ["humanmine-ncbigene", "humanmine-hgnc", "humanmine-clinvar"],
               "Gene.name": ["humanmine-hgnc", "humanmine-ncbigene"],
               "Gene.typeOfGene": ["humanmine-ensembl"]}
    out = merge_priorities(str(base), writers,
                           {"ncbi-gene": ["humanmine-ncbigene"], "hgnc": ["humanmine-hgnc"]}, {}, {})
    lines = out.splitlines()
    assert "# HumanMine" in lines
    assert "Gene.symbol = humanmine-ncbigene, humanmine-hgnc, depmap-expression, humanmine-clinvar, *" in lines
    assert "Disease.name = omim, *" in lines and "Gene.organism = *, gtex" in lines   # untouched
    assert "Gene.name = humanmine-hgnc, humanmine-ncbigene" in lines
    assert not any(l.startswith("Gene.typeOfGene") for l in lines)                   # one writer


def test_priorities_without_a_base_file_list_only_shared_fields(tmp_path):
    from rdfc2im.project import merge_priorities
    out = merge_priorities(None, {"Gene.name": ["humanmine-a", "humanmine-b"], "Gene.x": ["humanmine-a"]}, {}, {}, {})
    assert [l for l in out.splitlines() if "=" in l] == ["Gene.name = humanmine-a, humanmine-b"]


# ------------------------------------------------------------------ alongside
ALONGSIDE_PROJECT = """<project type="bio"><sources>
  <source name="ncbi-gene" type="ncbi-gene"/>
  <source name="reactome" type="reactome"/>
  <source name="panther" type="panther"/>
</sources></project>"""


def _alongside_setup(tmp_path):
    from rdfc2im.model import InterMineModel as IM
    (tmp_path / "core.xml").write_text(
        '<model name="genomic" package="org.intermine.model.bio">'
        '<class name="Organism" is-interface="true"><attribute name="taxonId" type="java.lang.String"/></class>'
        '<class name="Pathway" is-interface="true"><attribute name="identifier" type="java.lang.String"/>'
        '<attribute name="description" type="java.lang.String"/>'
        '<reference name="organism" referenced-type="Organism"/></class></model>')
    (tmp_path / "k_keys.properties").write_text("Pathway.key_identifier=identifier\nOrganism.key_taxonid=taxonId\n")
    m = IM(); m.load_xml(str(tmp_path / "core.xml")); m.load_keys(str(tmp_path / "k_keys.properties")); m.finalize()
    m.live = None
    out_root = tmp_path / "out"; (out_root / "reactome").mkdir(parents=True)
    write_tsv(str(out_root / "reactome" / "columns.tsv"), [
        _col(0, "Pathway", "identifier", required="yes"),
        _col(1, "Pathway", "description"),
        _col(2, "Organism", "taxonId", via="Pathway.organism"),
    ], COL_COLUMNS)
    (tmp_path / "project.xml").write_text(ALONGSIDE_PROJECT)
    return m, str(out_root), str(tmp_path / "_mine"), str(tmp_path / "project.xml")


def test_a_supplement_loads_after_its_stock_source_with_stock_values_first(tmp_path):
    """reactome's keys file has no Pathway key: loaded after ours it would duplicate every pathway."""
    from rdfc2im.project import gen_project
    m, out_root, mine, px = _alongside_setup(tmp_path)
    cfg = {"reactome": {"replaces": [], "alongside": ["reactome"]}}
    gen_project(out_root, mine, m, "humanmine-items", "/data", px, cfg, None, log=lambda *a: None)
    names = [s.get("name") for s in etree.parse(os.path.join(mine, "project.xml")).getroot().iter("source")]
    assert names == ["ncbi-gene", "reactome", "humanmine-reactome", "panther"], names
    prio = open(os.path.join(mine, "genomic_priorities.properties")).read().splitlines()
    for fld in ("Pathway.identifier", "Pathway.description", "Pathway.organism", "Organism.taxonId"):
        assert f"{fld} = reactome, humanmine-reactome, *" in prio, (fld, prio)

    rc, msgs = [], []
    assert check_project(out_root, mine, m, "humanmine-items", px, cfg, log=msgs.append) == 0, msgs


def test_a_supplement_before_its_stock_source_is_a_hard_problem(tmp_path):
    from rdfc2im.project import gen_project
    m, out_root, mine, px = _alongside_setup(tmp_path)
    cfg = {"reactome": {"replaces": [], "alongside": ["reactome"]}}
    gen_project(out_root, mine, m, "humanmine-items", "/data", px, cfg, None, log=lambda *a: None)
    p = os.path.join(mine, "project.xml")
    tree = etree.parse(p); srcs = tree.getroot().find("sources")
    ours = next(s for s in srcs if s.get("name") == "humanmine-reactome")
    srcs.remove(ours); srcs.insert(0, ours); tree.write(p)
    msgs = []
    assert check_project(out_root, mine, m, "humanmine-items", px, cfg, log=msgs.append) == 1
    assert any("loads before 'reactome'" in x for x in msgs), msgs

    msgs = []
    both = {"reactome": {"replaces": ["reactome"], "alongside": ["reactome"]}}
    check_project(out_root, mine, m, "humanmine-items", px, both, log=msgs.append)
    assert any("both replaces and loads alongside" in x for x in msgs), msgs


def test_a_field_written_on_a_subclass_covers_the_declared_one(tmp_path):
    """hpo declares OntologyTerm.crossReferences; stock and ours both fill it on HPOTerms."""
    from rdfc2im.model import InterMineModel as IM
    from rdfc2im.project import replaced_coverage
    (tmp_path / "core.xml").write_text(
        '<model name="genomic" package="org.intermine.model.bio">'
        '<class name="OntologyTerm" is-interface="true"><attribute name="identifier" type="java.lang.String"/></class>'
        '</model>')
    stock_dir = tmp_path / "sources" / "hpo"; stock_dir.mkdir(parents=True)
    (stock_dir / "hpo_additions.xml").write_text(
        '<classes><class name="OntologyTerm" is-interface="true">'
        '<collection name="crossReferences" referenced-type="OntologyTerm"/></class>'
        '<class name="HPOTerm" extends="OntologyTerm" is-interface="true"/></classes>')
    m = IM(); m.load_xml(str(tmp_path / "core.xml")); m.load_xml(str(stock_dir / "hpo_additions.xml")); m.finalize()
    out_root = tmp_path / "out"; (out_root / "hpo").mkdir(parents=True)
    write_tsv(str(out_root / "hpo" / "columns.tsv"), [
        _col(0, "HPOTerm", "identifier", required="yes"),
        _col(1, "OntologyTerm", "identifier", via="HPOTerm.crossReferences"),
    ], COL_COLUMNS)
    assert replaced_coverage(m, str(out_root), "hpo", "hpo") == []


def test_a_replacement_gap_is_hard_unless_accepted(tmp_path):
    """The coverage check can only see declarations, so each gap needs a decision, not a shrug."""
    from rdfc2im.model import InterMineModel as IM
    core = ('<model name="genomic" package="org.intermine.model.bio">'
            '<class name="GWAS" is-interface="true"><attribute name="name" type="java.lang.String"/></class></model>')
    stock_dir = tmp_path / "sources" / "huge-gwas"; stock_dir.mkdir(parents=True)
    (stock_dir / "huge-gwas_additions.xml").write_text(
        '<classes><class name="GWAS" is-interface="true">'
        '<attribute name="firstAuthor" type="java.lang.String"/><attribute name="year" type="java.lang.Integer"/></class>'
        '<class name="Source" is-interface="true"><attribute name="name" type="java.lang.String"/></class></classes>')
    (tmp_path / "core.xml").write_text(core)
    (tmp_path / "k_keys.properties").write_text("GWAS.key_name=name\n")
    m = IM(); m.load_xml(str(tmp_path / "core.xml")); m.load_xml(str(stock_dir / "huge-gwas_additions.xml"))
    m.load_keys(str(tmp_path / "k_keys.properties")); m.finalize(); m.live = None
    out_root = tmp_path / "out"; (out_root / "src").mkdir(parents=True)
    write_tsv(str(out_root / "src" / "columns.tsv"), [_col(0, "GWAS", "name", required="yes")], COL_COLUMNS)
    mine = tmp_path / "_mine"; mine.mkdir()
    (mine / "project.xml").write_text(PROJECT)

    def run(cfg):
        msgs = []
        rc = check_project(str(out_root), str(mine), m, "humanmine-items", None, {"src": cfg}, log=msgs.append)
        return rc, [x for x in msgs if "huge-gwas" in x or "accepted_gaps" in x]

    rc, msgs = run({"replaces": ["huge-gwas"]})
    assert rc == 1 and sum(x.startswith("HARD") for x in msgs) == 2, msgs

    rc, msgs = run({"replaces": ["huge-gwas"], "accepted_gaps": {
        "GWAS.firstAuthor": {"basis": "data:x", "reason": "not in the RDF"},
        "Source": {"basis": "java:X", "reason": "never created"}}})
    assert rc == 1, msgs                                    # GWAS.year is still open
    assert any(x.startswith("HARD") and "GWAS.{year}" in x for x in msgs), msgs
    assert any("without GWAS.firstAuthor - accepted (data:x): not in the RDF" in x for x in msgs), msgs

    rc, msgs = run({"replaces": ["huge-gwas"], "accepted_gaps": {
        "GWAS": {"basis": "b", "reason": "r"}, "Source": {"basis": "b", "reason": "r"},
        "Gene.symbol": {"basis": "b", "reason": "stale"}}})
    assert rc == 0, msgs
    assert any("accepted_gaps entry Gene.symbol matches no gap" in x for x in msgs), msgs


# ---- reverse references must resolve inside the isolated mergeModels

def _additions(tmp_path, m_setup, body):
    m, out_root, mine = m_setup
    (tmp_path / "_mine" / "humanmine-items_additions.xml").write_text(
        f'<?xml version="1.0"?>\n<classes>\n{textwrap.dedent(body)}\n</classes>\n')
    msgs = []
    rc = check_project(out_root, mine, m, "humanmine-items", None, {}, log=msgs.append)
    return rc, [x for x in msgs if x.startswith("HARD")]


def test_a_reverse_reference_whose_other_end_is_declared_nowhere_is_a_hard_problem(tmp_path):
    """The first fresh full-build.sh run died in phase 5: bio-source-humanmine-items' own
    mergeModels failed with "Unable to find named reverse reference 'genes' in class Pathway while
    processing Gene.pathways". curation/extensions_additions.xml had copied Gene.pathways
    (reverse-reference="genes") from stock reactome_additions.xml but not the Pathway.genes end it
    points at - fine in the full mine, where the stock reactome additions supply it, fatal in that
    isolated merge, which sees only bio-model plus our own additions. `check` passed."""
    rc, hard = _additions(tmp_path, _setup(tmp_path, []), """\
        <class name="Gene" is-interface="true">
          <collection name="pathways" referenced-type="Pathway" reverse-reference="genes"/>
        </class>
        <class name="Pathway" is-interface="true">
          <attribute name="name" type="java.lang.String"/>
        </class>""")
    assert rc == 1
    assert len(hard) == 1 and "Gene.pathways" in hard[0] and "Pathway.genes" in hard[0], hard


def test_a_reverse_reference_whose_target_class_is_absent_is_a_hard_problem(tmp_path):
    rc, hard = _additions(tmp_path, _setup(tmp_path, []), """\
        <class name="Gene" is-interface="true">
          <collection name="pathways" referenced-type="Pathway" reverse-reference="genes"/>
        </class>""")
    assert rc == 1 and "Pathway.genes" in hard[0], hard


def test_both_ends_declared_in_the_additions_is_fine(tmp_path):
    rc, hard = _additions(tmp_path, _setup(tmp_path, []), """\
        <class name="Gene" is-interface="true">
          <collection name="pathways" referenced-type="Pathway" reverse-reference="genes"/>
        </class>
        <class name="Pathway" is-interface="true">
          <collection name="genes" referenced-type="Gene" reverse-reference="pathways"/>
        </class>""")
    assert rc == 0 and not hard, hard


def test_a_reverse_end_supplied_by_the_core_model_is_fine(tmp_path):
    """Protein.keywords is in core.xml, so pointing a reverse-reference at it needs no redeclaration."""
    rc, hard = _additions(tmp_path, _setup(tmp_path, []), """\
        <class name="Term" is-interface="true">
          <collection name="proteins" referenced-type="Protein" reverse-reference="keywords"/>
        </class>""")
    assert rc == 0 and not hard, hard


def test_a_one_way_collection_needs_no_reverse_end(tmp_path):
    rc, hard = _additions(tmp_path, _setup(tmp_path, []), """\
        <class name="Pathway" is-interface="true">
          <collection name="dataSets" referenced-type="DataSet"/>
        </class>""")
    assert rc == 0 and not hard, hard


# ---- `project` honours the source list it is given

def test_project_covers_only_the_requested_sources_not_every_stale_directory(tmp_path):
    """The second fresh full-build.sh run died in phase 11 loading humanmine-expressionatlas - a
    source nobody asked for. `rdfc2im project` took every out/<dir> that had a columns.tsv, and this
    workspace still held the translate outputs of six sources from an earlier hand session
    (expressionatlas, homologene, hpo, mesh, mp, uberon). full-build.sh never told `project` which
    sources it was building, so its `--sources` list scoped the fetch but not the mine: project.xml
    listed all twelve, and so did the additions (AnatomyTerm, HPOTerm, ...)."""
    from rdfc2im.project import gen_project
    m, out_root, mine = _project_setup(tmp_path, [
        _col(0, "AnatomyTerm", "identifier", required="yes"),
        _col(1, "AnatomyTerm", "namespace"),
    ])
    (tmp_path / "out" / "hgnc").mkdir()
    write_tsv(str(tmp_path / "out" / "hgnc" / "columns.tsv"),
              [_col(0, "Protein", "primaryAccession", required="yes")], COL_COLUMNS)

    def run(only):
        gen_project(out_root, mine, m, "humanmine-items", "/data", None, {}, None,
                    source_version="4.3.0", only=only, log=lambda *a: None)
        px = etree.parse(os.path.join(mine, "project.xml")).getroot()
        names = [s.get("name") for s in px.iter("source")]
        add = etree.parse(os.path.join(mine, "humanmine-items_additions.xml")).getroot()
        return names, {c.get("name") for c in add.iter("class")}

    names, classes = run(["hgnc"])
    assert names == ["humanmine-hgnc"], names
    assert "AnatomyTerm" not in classes, "a class only the unrequested source needs leaked into the mine"
    names, classes = run(None)                 # no restriction: everything translated, as before
    assert names == ["humanmine-hgnc", "humanmine-uberon"] and "AnatomyTerm" in classes
