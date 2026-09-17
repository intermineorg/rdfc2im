"""Runtime build-scope restrictions - taxon and gene list - applied to a source's mapping rows
in memory, after `translate()` has already written mapping_predicates.sssom.tsv to disk.

This ordering is the whole design: the committed mapping files stay exactly as curated (always
defaulting to human, 9606) regardless of what scope a given run asks for, and a run with no
override produces byte-identical output to today's committed queries. Scope is a property of
one invocation, not of the mapping.

Motivating bug (see LOAD-TRIAL.md): `ensembl`'s queries had no taxon filter at all, silently
spanning 348 species because the restriction lived only as a hardcoded `consts` value with
nothing to enforce it against the actual data. Centralising the mechanism here - one function
every source's Organism.taxonId rows flow through - makes that class of bug structural rather
than a per-source oversight.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from .fetch import _fetch_once, _normalise

DEFAULT_TAXA = ["9606"]


def _curie_prefix(value: str) -> Optional[str]:
    """'taxid:9606' -> 'taxid'; None for a quoted-literal value ('"9606"') or an empty one."""
    v = (value or "").strip()
    if not v or v.startswith('"'):
        return None
    m = re.match(r"^([A-Za-z][\w.-]*):", v)
    return m.group(1) if m else None


def restrict_field(rows: List[dict], im_class: str, im_field: str, terms: List[str],
                    prefer_prefix: Optional[str] = None) -> Tuple[List[dict], bool]:
    """Restrict every query-level (non-const, required) row mapping onto <im_class>.<im_field>
    to exactly `terms`, in every table it appears in - never mutates the input list.

    Returns (new_rows, restrictable).  restrictable=False means no such query-level row exists
    for this field on this source (either it only ever appears as a `consts` constant - a fixed
    value with no filter to restrict - or the field is not mapped by this source at all); the
    input rows are returned unchanged in that case, and the caller decides what "not
    restrictable" means for it.
    """
    out = [dict(r) for r in rows]
    hard = [r for r in out if r.get("im_class") == im_class and r.get("im_field") == im_field
            and r.get("kind") != "const" and r.get("required") == "yes"]
    if not hard:
        return out, False
    for r in hard:
        prefix = _curie_prefix(r.get("value", "")) or prefer_prefix
        if prefix:
            r["value"] = " ".join(f"{prefix}:{t}" for t in terms)
        else:
            # quoted-literal style (e.g. reactome's FILTER(STR(?x) = "9606")) - sparql.py's
            # _constraint() OR's multiple space-joined quoted terms together.
            r["value"] = " ".join(t if t.startswith('"') else f'"{t}"' for t in terms)
    # A `consts` row for the same field can only hold one value - it stamps every item with a
    # single fixed attribute, not a per-record filter. With exactly one term, rewrite it to match
    # (keeps today's shape). With more than one, drop it: the hard row(s) above already carry the
    # real per-record value straight from the query results (every restrictable source's own
    # query selects the field it's filtering on as a real column, not just as a constant), so
    # dropping the redundant constant loses nothing and avoids stamping every item with one
    # arbitrary taxon out of several.
    const_idx = [i for i, r in enumerate(out) if r.get("im_class") == im_class
                 and r.get("im_field") == im_field and r.get("kind") == "const"]
    if len(terms) == 1:
        for i in const_idx:
            out[i] = dict(out[i], value=terms[0])
    elif const_idx:
        keep = set(range(len(out))) - set(const_idx)
        out = [out[i] for i in sorted(keep)]
    return out, True


def apply_taxon_scope(rows: List[dict], taxa: List[str]) -> Tuple[List[dict], Optional[str]]:
    """Apply a taxon restriction to one source's rows.

    Returns (rows, skip_reason). skip_reason is None unless the requested taxa are non-default
    and this source cannot honour them: it declares Organism.taxonId only as a fixed `consts`
    value (human-only by definition - HGNC, ClinVar, GWAS Catalog have no other species in their
    upstream data at all, so there is nothing to filter and pointing the constant at a different
    number would actively mislabel every item). The caller should skip generating this source's
    queries entirely in that case rather than produce data with a false organism.

    A source with no Organism.taxonId row at all (an ontology, publications - not
    organism-specific data) is left untouched with no skip: taxon scope simply does not apply.
    """
    taxa = list(taxa)
    if taxa == DEFAULT_TAXA:
        return rows, None  # identity - the default-output-unchanged guarantee
    new_rows, restrictable = restrict_field(rows, "Organism", "taxonId", taxa, prefer_prefix="taxid")
    if restrictable:
        return new_rows, None
    has_const = any(r.get("im_class") == "Organism" and r.get("im_field") == "taxonId"
                     and r.get("kind") == "const" for r in rows)
    if has_const:
        return rows, (f"declares Organism.taxonId only as a fixed constant (human-only by "
                       f"definition, no query-level filter to restrict) - cannot be scoped to "
                       f"taxon {','.join(taxa)}")
    return rows, None  # not organism-specific data - no-op, no skip


def apply_gene_scope(rows: List[dict], im_field: str, identifiers: List[str]) -> Tuple[List[dict], Optional[str]]:
    """Restrict a source to a specific gene list, given identifiers already resolved to the
    form this source's own root-key field uses (its own `im_field`, e.g. Gene.primaryIdentifier
    for ncbigene) - resolution from a human-readable gene symbol into that native scheme is the
    caller's job (see cli.py's `resolve_gene_identifiers`), because every source keys genes
    differently and there is no single symbol->id mapping that is correct for all of them.

    Uses the identifier field rather than a source's incidental symbol/label column
    deliberately: the identifier is the source's REQUIRED join key, so it is present - and can
    be restricted - in every one of a multi-table source's queries, not just the one table that
    happens to carry a human-readable label. Same mechanism and same return shape as
    apply_taxon_scope; a source with no row mapping onto `im_field` at all yields
    restrictable=False and a skip_reason, since silently ignoring a requested gene restriction
    would be worse than refusing it.
    """
    if not identifiers:
        return rows, None
    new_rows, restrictable = restrict_field(rows, "Gene", im_field, identifiers)
    if restrictable:
        return new_rows, None
    return rows, (f"has no query-level Gene.{im_field} row to restrict a gene list against")


NCBIGENE_ENDPOINT = "https://rdfportal.org/ncbi/sparql"
NCBIGENE_GRAPH = "http://rdfportal.org/dataset/ncbigene"


def resolve_ncbigene_symbols(symbols: List[str], taxon: str = "9606", timeout: int = 60) -> List[str]:
    """Resolve gene SYMBOLS to NCBI Gene ids (ncbigene's own Gene.primaryIdentifier scheme) via
    a single live query against the same endpoint ncbigene itself fetches from.

    This is the reference gene-scope resolver: ncbigene is the source every other gene-centric
    source ultimately merges onto (see STATUS.md finding 2), so restricting IT by native id and
    letting the other sources restrict by their own scheme (symbol for HGNC, gene_name for
    UniProt, an Ensembl gene id for Ensembl, ...) is future work each needs its own resolver for
    - documented as an extension point (`gene_scope_field` in sources.yaml) rather than
    implemented here for every source under this task's time, per the "reasoned choice,
    documented" allowance in the brief. Symbols that don't resolve are silently dropped (a typo
    or a symbol RDF Portal doesn't carry a label for should not fail the whole build) - callers
    that care can diff the input against the result.
    """
    if not symbols:
        return []
    values = " ".join(f'"{s}"' for s in symbols)
    q = (
        "PREFIX dct: <http://purl.org/dc/terms/>\n"
        "PREFIX insdc: <http://ddbj.nig.ac.jp/ontologies/nucleotide/>\n"
        "PREFIX ncbio: <https://dbcls.github.io/ncbigene-rdf/ontology.ttl#>\n"
        "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
        "PREFIX taxid: <http://identifiers.org/taxonomy/>\n\n"
        "SELECT DISTINCT ?id ?label\n"
        f"FROM <{NCBIGENE_GRAPH}>\n"
        "WHERE {\n"
        "  ?Gene a insdc:Gene .\n"
        "  ?Gene dct:identifier ?id .\n"
        f"  ?Gene ncbio:taxid taxid:{taxon} .\n"
        "  ?Gene rdfs:label ?label .\n"
        f"  VALUES ?label {{ {values} }}\n"
        "}\n"
    )
    body, fmt, err = _fetch_once(q, NCBIGENE_ENDPOINT, timeout)
    if body is None:
        raise RuntimeError(f"resolve_ncbigene_symbols: {err}")
    lines = _normalise(body, fmt).decode("utf-8").splitlines()
    ids = []
    for line in lines[1:]:
        if not line.strip():
            continue
        cell = line.split("\t", 1)[0]
        ids.append(cell.strip().strip('"'))
    return ids
