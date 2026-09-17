-- Six public templates for the 113-gene food/drug-metabolism demo panel, added on top of the
-- 3 stock HumanMine templates that survive "reducing a mine" (see LOAD-TRIAL.md) because they
-- only reference classes/fields this build actually loads (Gene, Protein, Organism).
--
-- These live ONLY in the running mine's userprofile database (savedtemplatequery + tag tables,
-- owned by the superuser account, same as every stock template) - there is no InterMine template
-- config file this build writes them into, so they do NOT survive a fresh `dbmodel:builddb` or
-- a userprofile volume wipe (they DO survive `docker compose down`/`up`, which keeps volumes).
-- Re-apply after a fresh build with:
--
--   docker exec -i rdfc2im-postgres psql -U intermine -d humanmine-userprofile \
--       < curation/demo_public_templates.sql
--   docker restart rdfc2im-mine   # the webapp caches its template list at startup
--
-- Ids are hardcoded (2000001-2000006 templates, 2000101-2000106 tags) clear of the existing
-- stock rows (max id 1000084 at the time this was written) and of objectstore_unique_integer's
-- own low range - re-running this file twice will fail on the primary key, not silently
-- duplicate; DELETE the matching ids first (or bump this file's range) if you need to re-run it
-- against a mine that already has an earlier version of these templates.

INSERT INTO savedtemplatequery (id, templatequery, userprofileid) VALUES (2000001, '<template name="GOTerm_Structure" title="GO term hierarchy: term to parent terms" comment="" dataTypes="java.lang.String java.lang.String java.lang.String java.lang.String java.lang.String"><query name="GOTerm_Structure" model="genomic" view="GOTerm.identifier GOTerm.name GOTerm.namespace GOTerm.parents.identifier GOTerm.parents.name" longDescription="" sortOrder="GOTerm.identifier asc"><constraint path="GOTerm.identifier" editable="true" description="GOTerm.identifier" op="=" value="GO:0000016"/></query></template>', 1000001);
INSERT INTO tag (id, objectidentifier, type, tagname, userprofileid) VALUES (2000101, 'GOTerm_Structure', 'template', 'im:public', 1000001);

INSERT INTO savedtemplatequery (id, templatequery, userprofileid) VALUES (2000002, '<template name="Gene_MultiSource_Identifiers" title="Gene identifiers across NCBI, Ensembl and HGNC" comment="" dataTypes="java.lang.String java.lang.String java.lang.String java.lang.String java.lang.String java.lang.String"><query name="Gene_MultiSource_Identifiers" model="genomic" view="Gene.primaryIdentifier Gene.secondaryIdentifier Gene.symbol Gene.name Gene.chromosome.primaryIdentifier Gene.organism.taxonId" longDescription="" sortOrder="Gene.primaryIdentifier asc"><constraint path="Gene.symbol" editable="true" description="Gene.symbol" op="=" value="CYP2D6"/></query></template>', 1000001);
INSERT INTO tag (id, objectidentifier, type, tagname, userprofileid) VALUES (2000102, 'Gene_MultiSource_Identifiers', 'template', 'im:public', 1000001);

INSERT INTO savedtemplatequery (id, templatequery, userprofileid) VALUES (2000003, '<template name="Gene_UniProt_Proteins" title="Gene to UniProt protein records" comment="" dataTypes="java.lang.String java.lang.String java.lang.Double"><query name="Gene_UniProt_Proteins" model="genomic" view="Gene.symbol Gene.proteins.primaryAccession Gene.proteins.molecularWeight" longDescription="" sortOrder="Gene.symbol asc"><constraint path="Gene.symbol" editable="true" description="Gene.symbol" op="=" value="VKORC1"/></query></template>', 1000001);
INSERT INTO tag (id, objectidentifier, type, tagname, userprofileid) VALUES (2000103, 'Gene_UniProt_Proteins', 'template', 'im:public', 1000001);

INSERT INTO savedtemplatequery (id, templatequery, userprofileid) VALUES (2000004, '<template name="Gene_ClinVar_Alleles" title="Gene to ClinVar allele records" comment="" dataTypes="java.lang.String java.lang.String java.lang.String java.lang.String"><query name="Gene_ClinVar_Alleles" model="genomic" view="Gene.symbol Gene.alleles.primaryIdentifier Gene.alleles.type Gene.alleles.clinicalSignificance" longDescription="" sortOrder="Gene.symbol asc"><constraint path="Gene.symbol" editable="true" description="Gene.symbol" op="=" value="DPYD"/></query></template>', 1000001);
INSERT INTO tag (id, objectidentifier, type, tagname, userprofileid) VALUES (2000104, 'Gene_ClinVar_Alleles', 'template', 'im:public', 1000001);

INSERT INTO savedtemplatequery (id, templatequery, userprofileid) VALUES (2000005, '<template name="GWASResult_AssociatedGene" title="GWAS associations for a gene" comment="" dataTypes="java.lang.String java.lang.String java.lang.Double java.lang.String"><query name="GWASResult_AssociatedGene" model="genomic" view="GWASResult.associatedGenes.symbol GWASResult.phenotype GWASResult.pValue GWASResult.SNP.primaryIdentifier" longDescription="" sortOrder="GWASResult.associatedGenes.symbol asc"><constraint path="GWASResult.associatedGenes.symbol" editable="true" description="GWASResult.associatedGenes.symbol" op="=" value="ABCB1"/></query></template>', 1000001);
INSERT INTO tag (id, objectidentifier, type, tagname, userprofileid) VALUES (2000105, 'GWASResult_AssociatedGene', 'template', 'im:public', 1000001);

INSERT INTO savedtemplatequery (id, templatequery, userprofileid) VALUES (2000006, '<template name="Gene_Publications" title="Gene to cited publications" comment="" dataTypes="java.lang.String java.lang.String java.lang.String"><query name="Gene_Publications" model="genomic" view="Gene.symbol Gene.publications.pubMedId Gene.publications.title" longDescription="" sortOrder="Gene.symbol asc"><constraint path="Gene.symbol" editable="true" description="Gene.symbol" op="=" value="TPMT"/></query></template>', 1000001);
INSERT INTO tag (id, objectidentifier, type, tagname, userprofileid) VALUES (2000106, 'Gene_Publications', 'template', 'im:public', 1000001);

-- Two of HumanMine's own stock templates (ids 1000082/1000084, both from before this session,
-- not the 6 new ones above) defaulted their editable Protein.organism.name constraint to
-- "Plasmodium falciparum 3D7" - a leftover from HumanMine's full stock template set, meaningless
-- once "reducing a mine" (see LOAD-TRIAL.md) leaves only human data. Running either with no
-- override returned zero results, which reads as broken rather than "wrong default organism"
-- to anyone trying the demo. Repointed at this mine's own Organism.name string (verified live:
-- `select distinct name from organism` returns exactly "Homo sapiens", not e.g. "H. sapiens").
UPDATE savedtemplatequery SET templatequery = replace(templatequery, 'Plasmodium falciparum 3D7', 'Homo sapiens')
    WHERE id IN (1000082, 1000084);
