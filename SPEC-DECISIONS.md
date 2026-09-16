# Spec decisions D1-D13 - reconstructed index

`SPECIFICATION_15sep26.md` is **not in this repository**. It was an input to the conversation that
produced the first commit, and it was not included in the tarball. This file is *not* a
reconstruction of that document - it is an index of every surviving citation of it, gathered from
`knowledge.yaml`, `docs.py`, `CURATION_GUIDE.md`, `STATUS.md` and `curation/`, so that a reader who
meets "spec D7" in a mapping note can find out what is meant without the original.

Each row quotes or paraphrases what the in-repo citation actually says. Where a decision's content
does not survive anywhere in the repo, the row says so rather than guessing. **If you have the
original, replace this file with it** and delete the caveat in `README.md`.

| # | What the citations say it is | Where it is cited | Current state |
|---|---|---|---|
| D1 | No InterMine field for a record's modification date (`dct:modified`); and `Gene.typeOfGene` has no home in the stock `ncbi-gene`/`human-gene` additions. | `knowledge.yaml` (`basis: spec-D1`), `CURATION_GUIDE.md`, `curation/extensions_additions.xml` | **Side-stepped.** `typeOfGene` is declared in `curation/extensions_additions.xml`, so neither stock source is touched. `dct:modified` rows stay `todo`. |
| D2 | GO-slim / ontology subsets (`oboinowl:inSubset`): extend the model to hold them, or exclude them. | `knowledge.yaml` (`basis: spec-D2`), `CURATION_GUIDE.md` (go, mp) | **Open.** All `inSubset` rows are `todo`. |
| D3 | Sources with no rdf-config equivalent stay on their traditional loader. | `STATUS.md`, `docs.py` | Settled: `orphanet`, `hpo-annotation`, InterPro, IntAct, BioGRID, SIGNOR are out of scope. |
| D4 | UniProt `core:classifiedWith` carries GO terms, but InterMine models GO through `GOAnnotation` (which needs evidence codes UniProt RDF does not give here), so the link is not a true GO annotation. | `knowledge.yaml` (uniprot `classifiedWith`), `STATUS.md` | **Open, and live in the output** - see the `ext_via` note on that row. Flagged `sure` but explicitly marked a review candidate. |
| D5 | Disease identifier normalisation: ClinVar gives MedGen CUIs, `ClinvarConverter` keys `Disease` by OMIM; MONDO/TogoID would reconcile them. | `knowledge.yaml` (`basis: spec-D5`) | **Open.** Disease id rows are `todo` in clinvar and hgnc. |
| D6 | Interactions (IntAct etc.) stay with the traditional `psi` load. | `knowledge.yaml` (`basis: spec-D6`), `STATUS.md` | Settled: UniProt `core:interaction` is not mapped. |
| D7 | HomoloGene gives n-ary clusters (`orth:hasHomologousMember`); InterMine's `Homologue` is pairwise, so a cluster must be expanded to all member pairs. An `expand` transform is the planned mechanism. | `knowledge.yaml` (`basis: spec-D7`), `sources.yaml`, `CURATION_GUIDE.md`, `STATUS.md` | **Open, and blocking.** homologene translates but emits no table; no `expand` transform exists yet. |
| D8 | Sources whose rdf-config directory is empty or absent stay on their traditional loader. | `STATUS.md`, `docs.py` | Settled: `disgenet` has `endpoint.yaml`/`metadata.yaml` but no `model.yaml`; scope `blocked` in `sources.yaml`. |
| D9 | **No surviving citation.** | - | Unknown. |
| D10 | Expression Atlas needs the EBI experiment/assay/factor hierarchy flattened before it can be mapped. | `STATUS.md`, `docs.py` | **Open, not started.** Scope `structural`; only `DataSet.name`/`description` map today. |
| D11 | **No surviving citation.** | - | Unknown. |
| D12 | GWAS Catalog: the traditional converter denormalises the publication year onto `GWAS.year`, and uses the study title as `GWAS.name` where rdf-config offers `dct:identifier` (a GCST id) and a trait description. | `knowledge.yaml` (two notes), `CURATION_GUIDE.md`, `STATUS.md` | **Partly settled.** `GWAS.year` is a `guess` with a `regex` transform; the name/description split is still a `todo`. |
| D13 | ClinVar genomic coordinates hang off blank nodes. | `STATUS.md`, `docs.py` | **Open.** Those rows are `link` rows and are not mapped. |

## "Section 8"

Cited by `curation/extensions_additions.xml` as the place the schema extensions were approved. The
extensions themselves survive in that file, so the list is not lost: `Gene.typeOfGene`,
`Pathway.description`/`organism`/`reactions`, the `PathwayReaction` class, `Allele.reviewStatus`
and `.submissionCount`, `MeshTerm.identifier`, and `GWASResult.mappedTraitUri`.

`STATUS.md` also refers to spec "section 7.2" (which expected a `human-gene` source; the mine
actually runs `ncbi-gene` - see STATUS finding 2) and "finding 7.1" (weak `term=` coverage for
source-specific vocabularies, which `knowledge.yaml` fills with converter evidence).
