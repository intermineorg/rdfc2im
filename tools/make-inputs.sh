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

# copy_tree SRC DST: copy SRC's contents into DST, leaving out .git, and check the result.
# in/ never needs a .git (the clones stay in $CACHE), and copying one is what broke this script:
# a plain `cp -r` of a clone failed on its read-only pack file on the virtiofs workspace mount
# ("failed to extend ... pack: Permission denied", a 0-byte pack left behind) - for a directory
# the script then deleted anyway. tar streams through read/write rather than cp's fast path, and
# the checksum comparison is because plain `cp` has also been seen to write the right number of
# NULs on that mount without failing.
copy_tree() {
    mkdir -p "$2"
    (cd "$1" && tar --exclude=.git -cf - .) | (cd "$2" && tar -xf -)
    want=$(cd "$1" && find . -name .git -prune -o -type f -exec md5sum {} + | sort -k2)
    got=$(cd "$2" && find . -type f -exec md5sum {} + | sort -k2)
    if [ "$want" != "$got" ]; then
        echo "ERROR: copying $1 to $2 did not produce identical files" >&2
        exit 1
    fi
}

copy_file() {                   # copy_file SRC DST - a small file, checked
    cp "$1" "$2"
    cmp -s "$1" "$2" || { echo "ERROR: copying $1 to $2 did not produce an identical file" >&2; exit 1; }
}

echo "assembling in/"
rm -rf in/config in/intermine in/humanmine-bio-sources
copy_tree "$CACHE/rdf-config/config"       in/config
copy_tree "$CACHE/intermine/bio"           in/intermine/bio
copy_tree "$CACHE/humanmine-bio-sources"   in/humanmine-bio-sources
copy_file "$CACHE/humanmine/project.xml"   in/humanmine_project.xml
copy_file "$CACHE/humanmine/dbmodel/resources/genomic_priorities.properties" in/humanmine_priorities.properties

# The live model is the merged, deployed model - it has no upstream file.
for fmt in xml json; do
    echo "  humanmine_model.$fmt: $MINE/service/model?format=$fmt"
    curl -fsS -o "in/humanmine_model.$fmt" "$MINE/service/model?format=$fmt" \
        || echo "  WARNING could not fetch the live model as $fmt; keeping any existing copy"
done

echo "done.  in/ is $(du -sh in | cut -f1) (gitignored)."
