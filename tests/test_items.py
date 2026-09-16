import os, textwrap
from rdfc2im.model import InterMineModel
from rdfc2im.items import emit_items
from test_mapping import make_model

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
