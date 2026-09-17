from types import SimpleNamespace
from rdfc2im.cli import _resolve_genes


def test_resolve_genes_splits_whitespace_separated_symbols_per_line(tmp_path):
    """A curated gene panel reads better grouped a dozen symbols to a line under a category
    comment than strictly one per line - demo_gene_panel.txt is written that way. @file must
    split each line on whitespace, not treat the whole line as one token."""
    p = tmp_path / "panel.txt"
    p.write_text(
        "# Phase I drug metabolism - 3\n"
        "CYP1A1 CYP1A2 CYP1B1\n"
        "\n"
        "# Transporters - 2\n"
        "ABCB1 ABCG2\n"
    )
    genes = _resolve_genes(SimpleNamespace(genes=f"@{p}"))
    assert genes == ["CYP1A1", "CYP1A2", "CYP1B1", "ABCB1", "ABCG2"]


def test_resolve_genes_comma_form_unaffected():
    genes = _resolve_genes(SimpleNamespace(genes="BRCA1, TP53 ,CYP2D6"))
    assert genes == ["BRCA1", "TP53", "CYP2D6"]


def test_resolve_genes_no_flag_is_empty():
    assert _resolve_genes(SimpleNamespace(genes=None)) == []
