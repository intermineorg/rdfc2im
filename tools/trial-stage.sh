#!/bin/sh
# Collect the build outputs the trial stack mounts.  Idempotent; re-run after rebuilding
# the war or changing the mapping.
#
#   TRIAL_HOME  where the mine was built (contains trialmine/ and bgdeps/)
#
# Produces, all gitignored:
#   trial/artifacts/humanmine/       the webapp, exploded, with its DB host patched
#   trial/artifacts/bluegenes-lib/   BlueGenes and its 178 runtime jars
set -eu

HERE=$(cd "$(dirname "$0")/.." && pwd)
ART=$HERE/trial/artifacts
TRIAL_HOME=${TRIAL_HOME:-}
[ -n "$TRIAL_HOME" ] || { [ -f "$HERE/.trial-home" ] && TRIAL_HOME=$(cat "$HERE/.trial-home"); }
[ -n "$TRIAL_HOME" ] || { echo "trial-stage: set TRIAL_HOME (or write it to .trial-home)" >&2; exit 1; }

WAR=$TRIAL_HOME/trialmine/webapp/build/libs/webapp.war
CP=$TRIAL_HOME/bgdeps/classpath.txt
[ -f "$WAR" ] || { echo "trial-stage: no war at $WAR - run ./gradlew :webapp:war first" >&2; exit 1; }
[ -f "$CP" ]  || { echo "trial-stage: no classpath at $CP - run the bgdeps resolve first" >&2; exit 1; }

# ---- webapp ------------------------------------------------------------------
echo "staging webapp from $WAR"
rm -rf "$ART/humanmine"
mkdir -p "$ART/humanmine"
( cd "$ART/humanmine" && unzip -oq "$WAR" )   # -o: the war has duplicate entries

# The war bakes ~/.intermine/humanmine.properties in at build time, pointing at the
# host's published port.  Inside the compose network the database is the `postgres`
# service on its own port, so patch the two files that carry it.
for f in WEB-INF/classes/intermine.properties WEB-INF/web.properties; do
    if [ -f "$ART/humanmine/$f" ]; then
        sed -i 's|serverName=localhost:15432|serverName=postgres:5432|g; s|localhost:15432|postgres:5432|g' \
            "$ART/humanmine/$f"
        echo "  patched $f -> postgres:5432"
    fi
done
if grep -rq "localhost:15432" "$ART/humanmine/WEB-INF/classes/intermine.properties" 2>/dev/null; then
    echo "trial-stage: WARNING host DB address still present in intermine.properties" >&2
fi

# The war also bakes in a generic project.title at build time. This trial's own display
# name is specific to whatever this run happens to be demoing (currently the 113-gene
# food/drug-metabolism panel) - not something to bake into the war build itself, since
# that source is shared with other contributors demoing other builds from the same repo.
for f in WEB-INF/classes/intermine.properties WEB-INF/web.properties; do
    if [ -f "$ART/humanmine/$f" ]; then
        sed -i 's|^project.title=.*|project.title=rdfc2im: 113 gene food/drug-metabolism panel|' \
            "$ART/humanmine/$f"
        echo "  patched $f project.title"
    fi
done

# ---- bluegenes ---------------------------------------------------------------
echo "staging BlueGenes jars"
rm -rf "$ART/bluegenes-lib"
mkdir -p "$ART/bluegenes-lib"
n=0
# classpath.txt is a ':'-separated list of absolute jar paths
echo "$(cat "$CP")" | tr ':' '\n' | while read -r jar; do
    [ -n "$jar" ] && [ -f "$jar" ] || continue
    cp -n "$jar" "$ART/bluegenes-lib/" 2>/dev/null || true
done
n=$(ls -1 "$ART/bluegenes-lib" | wc -l)
echo "  $n jars"

echo "staged into $ART"
