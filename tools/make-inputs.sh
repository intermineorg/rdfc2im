#!/bin/sh
# Rebuild in/ from the upstream projects.  Idempotent; re-run to refresh.
#
#   in/config/<source>/            dbcls/rdf-config                 config/
#   in/intermine/bio/              intermine/intermine              bio/
#   in/humanmine-bio-sources/      intermine/humanmine-bio-sources
#   in/humanmine_project.xml       intermine/humanmine              project.xml
#   in/humanmine_priorities.properties  intermine/humanmine     dbmodel/resources/genomic_priorities.properties
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

# Trees are copied by tools/copy_tree.py, files by copy_file - never `cp -r` or `tar`. Both failed
# here on the virtiofs workspace mount, one after the other, on the first fresh run:
#   cp -r of a clone:  "failed to extend ... .git/objects/pack/pack-....pack: Permission denied",
#                      leaving a 0-byte pack (in a .git the script then deleted anyway)
#   tar | tar:         "tar: .: Directory renamed before its status could be extracted" (tar
#                      compares inode numbers, which that mount does not keep stable)
# copy_tree.py uses plain chunked read/write, re-reads every file it wrote and compares, leaves
# .git out, and keeps modes (gradlew) and symlinks. Plain `cp` has also been seen to write the
# right number of NULs on that mount without failing, hence copy_file's cmp.
TOOLS=$(cd "$(dirname "$0")" && pwd)     # next to this script, whatever the caller's cwd
copy_tree() { python3 "$TOOLS/copy_tree.py" "$@"; }

copy_file() {                   # copy_file SRC DST - a small file, checked
    cp "$1" "$2"
    cmp -s "$1" "$2" || { echo "ERROR: copying $1 to $2 did not produce an identical file" >&2; exit 1; }
}

echo "assembling in/"
rm -rf in/config in/intermine in/humanmine-bio-sources
copy_tree "$CACHE/rdf-config/config"       in/config             --exclude .git
copy_tree "$CACHE/intermine/bio"           in/intermine/bio      --exclude .git
copy_tree "$CACHE/humanmine-bio-sources"   in/humanmine-bio-sources --exclude .git
copy_file "$CACHE/humanmine/project.xml"   in/humanmine_project.xml
copy_file "$CACHE/humanmine/dbmodel/resources/genomic_priorities.properties" in/humanmine_priorities.properties

# The live model is the merged, deployed model - it has no upstream file.
for fmt in xml json; do
    echo "  humanmine_model.$fmt: $MINE/service/model?format=$fmt"
    curl -fsS -o "in/humanmine_model.$fmt" "$MINE/service/model?format=$fmt" \
        || echo "  WARNING could not fetch the live model as $fmt; keeping any existing copy"
done

echo "done.  in/ is $(du -sh in | cut -f1) (gitignored)."
