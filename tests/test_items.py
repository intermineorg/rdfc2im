import os, textwrap
from rdfc2im.model import InterMineModel
from rdfc2im.items import emit_items, to_ascii
from test_mapping import make_model

def test_to_ascii_transliterates_what_the_intermine_loader_cannot_take():
    """Regression for a confirmed InterMine bug: have.large.file.xml.tgt's postgres COPY BINARY
    writer fails the whole retrieve ("invalid byte sequence for encoding UTF8: 0x00") on a
    single non-ASCII character in an attribute value - reproduced on HGNC's own alt_label data
    (a real Greek letter in a real synonym). Known Greek letters and "smart" punctuation get a
    specific ASCII rendering; general Unicode (accents, ligatures) goes through NFKD; anything
    left with no ASCII form at all becomes "?" rather than silently vanishing or crashing."""
    assert to_ascii("ERα-regulated") == "ERalpha-regulated"
    assert to_ascii("TNF-α/β") == "TNF-alpha/beta"
    assert to_ascii("café") == "cafe"        # e-acute, NFKD path
    assert to_ascii("ﬁsh") == "fish"          # fi-ligature, NFKD path
    assert to_ascii("‘quoted’") == "'quoted'"
    assert to_ascii("plain ascii") == "plain ascii"
    assert to_ascii("中") == "?"               # no ASCII form at all

def test_items_links_and_merging(tmp_path):
    m = make_model(tmp_path)
    out = tmp_path / "out"; (out / "tsv").mkdir(parents=True)
    (out / "columns.tsv").write_text(textwrap.dedent("""\
    table\tposition\tcolumn\tvariable\tim_class\tim_field\ttransform\tfilter\tvalue\tkind\tvia\tstatus\trequired
    main\t0\tid\tid\tGene\tprimaryIdentifier\t\t\t\tliteral\t\tsure\tyes
    main\t1\tsym\tsym\tGene\tsymbol\t\t\t\tliteral\t\tsure\tno
    main\t2\tc\t\tOrganism\ttaxonId\t\t\t9606\tconst\t\tsure\tno
    main_alias\t0\tid\tid\tGene\tprimaryIdentifier\t\t\t\tliteral\t\tsure\tyes
    main_alias\t1\talias\talias\tSynonym\tvalue\t\t\t\tliteral\tGene.synonyms\tguess\tno
    main_alias\t2\tc\t\tOrganism\ttaxonId\t\t\t9606\tconst\t\tsure\tno
    """))
    (out / "tsv" / "main.tsv").write_text("Gene.primaryIdentifier\tGene.symbol\tOrganism.taxonId\n1\tABC\t9606\n2\tDEF\t9606\n")
    (out / "tsv" / "main_alias.tsv").write_text("Gene.primaryIdentifier\tSynonym.value\tOrganism.taxonId\n1\tx\t9606\n1\ty\t9606\n2\t\t9606\n")
    st = emit_items(m, str(out), "t", {"data_source_name": "T", "data_set_title": "T set"}, log=lambda *a: None)
    xml = (out / "items" / "t.xml").read_text()
    assert st["items"] == 7                      # 2 genes, 1 organism, 2 synonyms, DataSource, DataSet
    assert xml.count('class="Gene"') == 2 and xml.count('class="Organism"') == 1 and xml.count('class="Synonym"') == 2
    g1 = xml[xml.index('value="1"'):]
    assert '<reference name="organism"' in g1 and '<collection name="synonyms"><reference ref_id=' in g1
    assert '<reference name="subject"' in xml       # reverse reference set on Synonym


def _rooted_table(tmp_path, with_root):
    """UniProt's shape: a taxon constraint column comes before the root's own key."""
    m = make_model(tmp_path)
    out = tmp_path / ("new" if with_root else "old")
    (out / "tsv").mkdir(parents=True)
    root = "\tGene" if with_root else ""
    hdr = "table\tposition\tcolumn\tvariable\tim_class\tim_field\ttransform\tfilter\tvalue\tkind\tvia\tstatus\trequired" + ("\troot" if with_root else "")
    (out / "columns.tsv").write_text("\n".join([
        hdr,
        f"main\t0\ttaxon\ttaxon\tOrganism\ttaxonId\t\t\t\tliteral\tGene.organism\tsure\tyes{root}",
        f"main\t1\tid\tid\tGene\tprimaryIdentifier\t\t\t\tliteral\t\tsure\tyes{root}",
        f"main\t2\talias\talias\tSynonym\tvalue\t\t\t\tliteral\t\tsure\tno{root}",
    ]) + "\n")
    (out / "tsv" / "main.tsv").write_text("Organism.taxonId\tGene.primaryIdentifier\tSynonym.value\n9606\t1\tx\n")
    notes = []
    emit_items(m, str(out), "t", {}, log=notes.append)
    return (out / "items" / "t.xml").read_text(), notes


def test_items_link_from_the_designed_root_not_the_first_column(tmp_path):
    """A table may be designed around any class; linking must follow the design.

    Here the root is Gene, but its first required column is an Organism constraint - exactly
    UniProt's layout.  Inferring the root from column order picked Organism, found no link from
    Organism to Synonym, and left the synonym unlinked.
    """
    xml, notes = _rooted_table(tmp_path, with_root=True)
    assert '<collection name="synonyms">' in xml, "Synonym must hang off the designed root, Gene"
    assert '<reference name="subject"' in xml
    assert not any("WARNING" in n for n in notes), notes


def test_unlinked_items_are_reported_not_discarded(tmp_path):
    """Old columns.tsv files (no root column) keep the old inference - and now say so loudly."""
    xml, notes = _rooted_table(tmp_path, with_root=False)
    assert '<collection name="synonyms">' not in xml
    assert any("no link from Organism to Synonym" in n for n in notes), notes


def test_a_constant_attaches_through_its_declared_link(tmp_path):
    """HGNC's xref DataSource is a constant; it belongs on CrossReference.source, not the Gene."""
    model = """<model name="genomic" package="org.intermine.model.bio">
    <class name="Gene" is-interface="true"><attribute name="primaryIdentifier" type="java.lang.String"/>
      <collection name="crossReferences" referenced-type="CrossReference" reverse-reference="subject"/></class>
    <class name="CrossReference" is-interface="true"><attribute name="identifier" type="java.lang.String"/>
      <reference name="subject" referenced-type="Gene" reverse-reference="crossReferences"/>
      <reference name="source" referenced-type="DataSource"/></class>
    <class name="DataSource" is-interface="true"><attribute name="name" type="java.lang.String"/></class>
    </model>"""
    (tmp_path / "core.xml").write_text(model)
    m = InterMineModel(); m.load_xml(str(tmp_path / "core.xml")); m.finalize(); m.live = None
    out = tmp_path / "out"; (out / "tsv").mkdir(parents=True)
    hdr = "table\tposition\tcolumn\tvariable\tim_class\tim_field\ttransform\tfilter\tvalue\tkind\tvia\tstatus\trequired\troot"
    (out / "columns.tsv").write_text("\n".join([hdr,
        "main\t0\tid\tid\tGene\tprimaryIdentifier\t\t\t\tliteral\t\tsure\tyes\tGene",
        "main\t1\txref\txref\tCrossReference\tidentifier\t\t\t\tliteral\t\tsure\tno\tGene",
        "main\t2\tsrc\t\tDataSource\tname\t\t\tENA\tconst\tCrossReference.source\tsure\tno\tGene",
    ]) + "\n")
    (out / "tsv" / "main.tsv").write_text("Gene.primaryIdentifier\tCrossReference.identifier\tDataSource.name\n1\tAB001\tENA\n")
    notes = []
    emit_items(m, str(out), "t", {"data_source_name": "HGNC"}, log=notes.append)
    xml = (out / "items" / "t.xml").read_text()
    xref = xml[xml.index('class="CrossReference"'):]
    xref = xref[:xref.index("</item>")]
    assert '<reference name="source"' in xref, "the DataSource must hang off the CrossReference"
    assert not any("no link" in n for n in notes), notes


def test_key_ambiguity_keeps_only_the_disambiguated_holder(tmp_path):
    """STATUS.md D14: NCBI's own cross-reference data gives two distinct genes the same Ensembl
    id - a real overlapping-transcript/antisense annotation (e.g. FOXL3 and FOXL3-OT1), not an
    rdfc2im mapping error.  secondaryIdentifier is not ncbigene's own merge key (primaryIdentifier
    is - see ItemStore.key_for), so both genes load as distinct items; but InterMine's own
    key_secondaryidentifier_org assumes the field is unique across a source's output, and an
    ambiguous value reaching it either fails the load or merges the wrong two Genes.
    """
    m = make_model(tmp_path)
    out = tmp_path / "out"; (out / "tsv").mkdir(parents=True)
    (out / "columns.tsv").write_text(textwrap.dedent("""\
    table\tposition\tcolumn\tvariable\tim_class\tim_field\ttransform\tfilter\tvalue\tkind\tvia\tstatus\trequired
    main\t0\tid\tid\tGene\tprimaryIdentifier\t\t\t\tliteral\t\tsure\tyes
    main\t1\ttype\ttype\tGene\ttypeOfGene\t\t\t\tliteral\t\tsure\tno
    main\t2\tc\t\tOrganism\ttaxonId\t\t\t9606\tconst\t\tsure\tno
    main_dblink\t0\tid\tid\tGene\tprimaryIdentifier\t\t\t\tliteral\t\tsure\tyes
    main_dblink\t1\tens\tens\tGene\tsecondaryIdentifier\t\t\t\tliteral\t\tsure\tno
    main_dblink\t2\tc\t\tOrganism\ttaxonId\t\t\t9606\tconst\t\tsure\tno
    """))
    (out / "tsv" / "main.tsv").write_text(
        "Gene.primaryIdentifier\tGene.typeOfGene\tOrganism.taxonId\n"
        "1\tprotein-coding\t9606\n"    # FOXL3
        "2\tncRNA\t9606\n"             # FOXL3-OT1 - shares FOXL3's Ensembl id
        "3\tncRNA\t9606\n"             # a second ambiguous pair, neither side protein-coding
        "4\tncRNA\t9606\n")
    (out / "tsv" / "main_dblink.tsv").write_text(
        "Gene.primaryIdentifier\tGene.secondaryIdentifier\tOrganism.taxonId\n"
        "1\tENSG_A\t9606\n"
        "2\tENSG_A\t9606\n"
        "3\tENSG_B\t9606\n"
        "4\tENSG_B\t9606\n")
    cfg = {"key_ambiguity": {"Gene": {"field": "secondaryIdentifier",
                                       "prefer_field": "typeOfGene", "prefer_value": "protein-coding"}}}
    notes = []
    emit_items(m, str(out), "t", cfg, log=notes.append)
    xml = (out / "items" / "t.xml").read_text()

    def gene(pid):
        chunk = xml[xml.index(f'primaryIdentifier" value="{pid}"'):]
        return chunk[:chunk.index("</item>")]

    assert 'secondaryIdentifier" value="ENSG_A"' in gene("1"), "the protein-coding member keeps it"
    assert "secondaryIdentifier" not in gene("2"), "its ncRNA partner does not"
    assert "secondaryIdentifier" not in gene("3") and "secondaryIdentifier" not in gene("4"), \
        "no principled winner between two ncRNAs - drop from both, not a coin flip"
    assert any("blanked on 3 items" in n for n in notes), notes
