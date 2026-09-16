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
