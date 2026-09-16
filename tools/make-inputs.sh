#!/bin/sh
# Rebuild in/ from the upstream projects.  Idempotent; re-run to refresh.
#
#   in/config/<source>/            dbcls/rdf-config                 config/
#   in/intermine/bio/              intermine/intermine              bio/
#   in/humanmine-bio-sources/      intermine/humanmine-bio-sources
#   in/humanmine_project.xml       intermine/humanmine              project.xml
#   in/humanmine_model.xml         humanmine.org  /service/model?format=xml
#   in/humanmine_model.json        humanmine.org  /service/model?format=json
#
# Clones land in $CACHE (default in/.upstream) so a refresh is a `git pull`,
# not a 150 MB re-download.  Set CACHE to share one checkout between workspaces.
set -e

CACHE=${CACHE:-in/.upstream}
MINE=${MINE:-https://www.humanmine.org/humanmine}

mkdir -p "$CACHE" in

clone() {                       # clone <url> <dir> [extra git flags]
    if [ -d "$CACHE/$2/.git" ]; then
        echo "  $2: refreshing"
        git -C "$CACHE/$2" pull --quiet --ff-only || echo "  $2: pull failed, keeping the cached copy"
    else
        echo "  $2: cloning"
        # shellcheck disable=SC2086
        git clone --depth 1 --quiet $3 "$1" "$CACHE/$2"
    fi
}

echo "upstream sources ->  $CACHE"
clone https://github.com/dbcls/rdf-config.git                rdf-config
clone https://github.com/intermine/intermine.git             intermine --filter=blob:none
clone https://github.com/intermine/humanmine.git             humanmine
clone https://github.com/intermine/humanmine-bio-sources.git humanmine-bio-sources

echo "assembling in/"
rm -rf in/config in/intermine in/humanmine-bio-sources
cp -r "$CACHE/rdf-config/config"                in/config
mkdir -p in/intermine
cp -r "$CACHE/intermine/bio"                    in/intermine/bio
cp -r "$CACHE/humanmine-bio-sources"            in/humanmine-bio-sources
rm -rf in/humanmine-bio-sources/.git
cp    "$CACHE/humanmine/project.xml"            in/humanmine_project.xml

# The live model is the merged, deployed model - it has no upstream file.
for fmt in xml json; do
    echo "  humanmine_model.$fmt: $MINE/service/model?format=$fmt"
    curl -fsS -o "in/humanmine_model.$fmt" "$MINE/service/model?format=$fmt" \
        || echo "  WARNING could not fetch the live model as $fmt; keeping any existing copy"
done

echo "done.  in/ is $(du -sh in | cut -f1) (gitignored)."
