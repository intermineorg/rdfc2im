#!/bin/sh
# One-time fix for the humanmine-search Solr core, needed after every `solr create_core
# -c humanmine-search` (a fresh core, or one recreated after `trial-destroy`/a volume wipe -
# see trial/docker-compose.yml's solr service comment).
#
# Solr's `_default` schemaless configset (the only one this trial stack uses - see that same
# comment) auto-guesses field types from the first value written to each field. The
# "analyzed_string" type it guessed for every InterMine content field (gene_symbol, gene_name,
# publication_title, ...) came out as plain whitespace-tokenize + lowercase, with no partial-word
# matching at all - so a bare quicksearch term like "cyp" could never match a token like
# "cyp4f2" without an explicit trailing wildcard (`cyp*`), which InterMine's own quicksearch
# never adds (confirmed reading SolrKeywordSearchHandler.java: the raw query string is passed to
# Solr unmodified). Real symptom: `/service/search?q=cyp` returned ~1 near-random low-relevance
# Gene hit instead of the ~121 real CYP-family genes.
#
# Fixed by giving "analyzed_string" an EdgeNGramFilter on the INDEX side only (minGram 3, maxGram
# 10 - small enough not to blow up memory indexing long free-text fields like publication
# abstracts and pathway descriptions; a larger maxGram of 25 reproducibly OOM'd the indexing JVM
# on this build's ~4000 publications). The query-side analyzer stays plain whitespace+lowercase,
# so a search term matches any indexed n-gram without itself needing a wildcard.
#
# After running this, EXISTING documents must be re-indexed (the schema change is not
# retroactive) - clear the core and re-run create-search-index:
#   curl -s "http://localhost:8983/solr/humanmine-search/update?commit=true" \
#     -H "Content-Type: application/json" -d '{"delete": {"query": "*:*"}}'
#   (cd <mine checkout> && JAVA_HOME=... ./gradlew :dbmodel:postProcess -Pprocess=create-search-index)
#   docker restart rdfc2im-mine
set -eu
SOLR_URL=${SOLR_URL:-http://localhost:8983}
curl -sf -X POST "$SOLR_URL/solr/humanmine-search/schema" -H "Content-Type: application/json" -d '{
  "replace-field-type": {
    "name": "analyzed_string",
    "class": "solr.TextField",
    "positionIncrementGap": "100",
    "multiValued": true,
    "indexAnalyzer": {
      "tokenizer": { "class": "solr.WhitespaceTokenizerFactory" },
      "filters": [
        { "class": "solr.LowerCaseFilterFactory" },
        { "class": "solr.EdgeNGramFilterFactory", "minGramSize": "3", "maxGramSize": "10" }
      ]
    },
    "queryAnalyzer": {
      "tokenizer": { "class": "solr.WhitespaceTokenizerFactory" },
      "filters": [
        { "class": "solr.LowerCaseFilterFactory" }
      ]
    }
  }
}'
echo
echo "solr-search-schema-fix: analyzed_string replaced - now re-run create-search-index (see header comment)"
