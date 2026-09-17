"""scope.py: taxon and gene-list build restrictions."""
from rdfc2im.scope import DEFAULT_TAXA, apply_taxon_scope, apply_gene_scope, restrict_field
from rdfc2im.sparql import _constraint


def _row(table, im_class, im_field, value="", required="yes", kind="literal"):
    return dict(table=table, subject="Gene", predicate="p", column="c", im_class=im_class,
                im_field=im_field, status="sure", basis="x", value=value, required=required,
                kind=kind, via="")


def test_default_taxon_is_identity():
    rows = [_row("main", "Organism", "taxonId", "taxid:9606")]
    out, skip = apply_taxon_scope(rows, DEFAULT_TAXA)
    assert out == rows
    assert skip is None


def test_restrictable_source_gets_new_taxon():
    rows = [_row("main", "Organism", "taxonId", "taxid:9606")]
    out, skip = apply_taxon_scope(rows, ["10090"])
    assert skip is None
    assert out[0]["value"] == "taxid:10090"
    assert rows[0]["value"] == "taxid:9606"  # input untouched


def test_multi_taxon_values_and_const_dropped():
    rows = [_row("main", "Organism", "taxonId", "taxid:9606", required="yes"),
            _row("*", "Organism", "taxonId", "9606", required="no", kind="const")]
    out, skip = apply_taxon_scope(rows, ["9606", "10090"])
    assert skip is None
    hard = [r for r in out if r["kind"] != "const"]
    assert hard[0]["value"] == "taxid:9606 taxid:10090"
    assert not [r for r in out if r["kind"] == "const"]  # dropped, can't hold two values


def test_single_taxon_const_rewritten_not_dropped():
    rows = [_row("main", "Organism", "taxonId", "taxid:9606"),
            _row("*", "Organism", "taxonId", "9606", required="no", kind="const")]
    out, skip = apply_taxon_scope(rows, ["10090"])
    consts = [r for r in out if r["kind"] == "const"]
    assert len(consts) == 1 and consts[0]["value"] == "10090"


def test_human_only_source_is_skipped_not_mislabelled():
    rows = [_row("*", "Organism", "taxonId", "9606", required="no", kind="const")]
    out, skip = apply_taxon_scope(rows, ["10090"])
    assert skip is not None and "constant" in skip
    assert out == rows  # unchanged - caller must not use it, but nothing is corrupted either


def test_non_organism_source_is_untouched_and_not_skipped():
    rows = [_row("main", "GOTerm", "identifier", "")]
    out, skip = apply_taxon_scope(rows, ["10090"])
    assert skip is None
    assert out == rows


def test_gene_scope_restricts_required_key_row():
    rows = [_row("main", "Gene", "primaryIdentifier", "")]
    out, skip = apply_gene_scope(rows, "primaryIdentifier", ["1565", "672"])
    assert skip is None
    assert out[0]["value"] == '"1565" "672"'


def test_gene_scope_empty_list_is_noop():
    rows = [_row("main", "Gene", "primaryIdentifier", "")]
    out, skip = apply_gene_scope(rows, "primaryIdentifier", [])
    assert out == rows and skip is None


def test_gene_scope_field_not_mapped_is_skipped():
    rows = [_row("main", "Gene", "primaryIdentifier", "")]
    out, skip = apply_gene_scope(rows, "secondaryIdentifier", ["ENSG1"])
    assert skip is not None


def test_restrict_field_never_mutates_input():
    rows = [_row("main", "Organism", "taxonId", "taxid:9606")]
    snapshot = dict(rows[0])
    restrict_field(rows, "Organism", "taxonId", ["10090"])
    assert rows[0] == snapshot


def test_constraint_single_quoted_value_unchanged():
    assert _constraint("x", '"9606"') == 'FILTER(STR(?x) = "9606")'


def test_constraint_multi_quoted_values_or_together():
    assert (_constraint("x", '"9606" "10090"')
            == 'FILTER(STR(?x) = "9606" || STR(?x) = "10090")')


def test_constraint_curie_values_unchanged():
    assert _constraint("x", "taxid:9606 taxid:10090") == "VALUES ?x { taxid:9606 taxid:10090 }"
