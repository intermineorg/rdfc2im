"""scope.py: taxon and gene-list build restrictions."""
from rdfc2im.scope import (DEFAULT_TAXA, apply_taxon_scope, apply_gene_scope, restrict_field,
                           apply_publication_scope, extract_publication_pmids)
from rdfc2im.sparql import _constraint


def _row(table, im_class, im_field, value="", required="yes", kind="literal", transform=""):
    return dict(table=table, subject="Gene", predicate="p", column="c", im_class=im_class,
                im_field=im_field, status="sure", basis="x", value=value, required=required,
                kind=kind, via="", transform=transform)


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


def test_gene_scope_promotes_an_optional_gene_link_without_touching_the_input():
    """ClinVar/GWAS Catalog/UniProt map their sole Gene-linked field as OPTIONAL - correct for a
    full load (most records have no gene link), wrong for "restrict to this gene list", which
    implies the link must exist. apply_gene_scope must promote it in the copy it returns, and
    must not mutate the row taxon scoping (or a default run) would otherwise see."""
    rows = [_row("main", "Gene", "primaryIdentifier", "", required="no")]
    out, skip = apply_gene_scope(rows, "primaryIdentifier", ["1565"])
    assert skip is None
    assert out[0]["required"] == "yes" and out[0]["value"] == '"1565"'
    assert rows[0]["required"] == "no"  # input untouched


def test_gene_scope_field_not_mapped_is_skipped():
    rows = [_row("main", "Gene", "primaryIdentifier", "")]
    out, skip = apply_gene_scope(rows, "secondaryIdentifier", ["ENSG1"])
    assert skip is not None


def test_gene_scope_suffix_matches_a_field_whose_raw_value_is_an_iri():
    """GWAS Catalog's snp_gene_ids is a full IRI (http://identifiers.org/ensembl/ENSG...) even
    though items.py's iri_localname transform later truncates it to a bare Ensembl id for the
    InterMine attribute - confirmed live against TogoVar, which returns the IRI, not a literal.
    Plain STR(?x) = "ENSG..." equality (the ordinary literal path) never matches an IRI, so a
    row with this transform must render as a suffix match instead."""
    rows = [_row("main", "Gene", "secondaryIdentifier", "", transform="iri_localname")]
    out, skip = apply_gene_scope(rows, "secondaryIdentifier", ["ENSG00000165841", "ENSG00000100197"])
    assert skip is None
    assert out[0]["value"] == '~"ENSG00000165841" ~"ENSG00000100197"'


def test_constraint_iri_localname_marker_renders_as_strends():
    q = _constraint("snp_gene_ids", '~"ENSG00000165841" ~"ENSG00000100197"')
    assert q == ('FILTER(STRENDS(STR(?snp_gene_ids), "ENSG00000165841") || '
                 'STRENDS(STR(?snp_gene_ids), "ENSG00000100197"))')


def test_publication_scope_restricts_required_pubmedid_row():
    rows = [_row("main", "Publication", "pubMedId", "")]
    out, skip = apply_publication_scope(rows, ["23696881", "32042192"])
    assert skip is None
    assert out[0]["value"] == '"23696881" "32042192"'


def test_publication_scope_empty_list_is_noop():
    rows = [_row("main", "Publication", "pubMedId", "")]
    out, skip = apply_publication_scope(rows, [])
    assert out == rows and skip is None


def test_publication_scope_field_not_mapped_is_skipped():
    rows = [_row("main", "Gene", "primaryIdentifier", "")]
    out, skip = apply_publication_scope(rows, ["23696881"])
    assert skip is not None


def test_extract_publication_pmids_scans_items_xml_and_dedups(tmp_path):
    f1 = tmp_path / "hgnc.xml"
    f1.write_text(
        '<items>\n'
        '  <item id="0_1" class="Publication"><attribute name="pubMedId" value="23696881"/></item>\n'
        '  <item id="0_2" class="Publication"><attribute name="pubMedId" value="32042192"/></item>\n'
        '</items>\n')
    f2 = tmp_path / "uniprot.xml"
    f2.write_text(
        '<items>\n'
        '  <item id="0_1" class="Publication"><attribute name="pubMedId" value="23696881"/></item>\n'
        '</items>\n')
    missing = tmp_path / "clinvar.xml"  # source not built yet - skipped, not an error
    pmids = extract_publication_pmids([str(f1), str(f2), str(missing)])
    assert pmids == ["23696881", "32042192"]  # deduped, sorted numerically


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
