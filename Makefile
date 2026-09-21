# rdfc2im workspace Makefile - every target is `python3 -m rdfc2im <cmd>` with the flags spelled out.
PY      ?= python3
LIMIT   ?= 20        # total rows per query; 0 = all
ITERATE ?= 5000      # page size for fetch; 0 = single request per query
SLEEP   ?= 1         # seconds between requests
FETCH   ?=           # set to 1 to include fetch in `make all`
SRC     ?=
SRCFLAG  = $(foreach s,$(SRC),--source $(s))
GUESS   ?=            # set to --no-guess to exclude guess rows

.PHONY: inputs up down restart status logs trial-stage trial-destroy all allow translate fetch fetch-dry tsv items project check linkml docs test fork-sync clean

inputs:     ; sh tools/make-inputs.sh

# ---- local trial stack (postgres + InterMine webapp + BlueGenes), all in containers.
# Everything is published to 127.0.0.1 only; reach it with `ssh -L` (see LOAD-TRIAL.md).
# `down` keeps the database volume; only `trial-destroy` throws it away.
COMPOSE  ?= podman compose -f trial/docker-compose.yml
trial-stage:   ; sh tools/trial-stage.sh
up:            ; $(COMPOSE) up -d
down:          ; $(COMPOSE) down
# the webapp reads the database once, at startup - restart it after (re)loading data
restart:       ; $(COMPOSE) restart $(SVC)
status:        ; $(COMPOSE) ps
logs:          ; $(COMPOSE) logs --tail 40 $(SVC)
trial-destroy: ; $(COMPOSE) down -v
all:        ; $(PY) -m rdfc2im all --limit $(LIMIT) --iterate $(ITERATE) --sleep $(SLEEP) $(SRCFLAG) $(GUESS) $(if $(FETCH),--fetch,)
allow:      ; $(PY) -m rdfc2im allow
translate:  ; $(PY) -m rdfc2im translate --limit $(LIMIT) $(SRCFLAG) $(GUESS)
	$(PY) tools/gen_intermine_model_yaml.py $(SRCFLAG)
fetch:      ; $(PY) -m rdfc2im fetch --limit $(LIMIT) --iterate $(ITERATE) --sleep $(SLEEP) $(SRCFLAG)
fetch-dry:  ; $(PY) -m rdfc2im fetch --dry-run --limit $(LIMIT) --iterate $(ITERATE) $(SRCFLAG)
tsv:        ; $(PY) -m rdfc2im tsv $(SRCFLAG)
items:      ; $(PY) -m rdfc2im items $(SRCFLAG)
linkml:     ; $(PY) -m rdfc2im linkml
project:    ; $(PY) -m rdfc2im project
check:      ; $(PY) -m rdfc2im check

# Plain `cp` has silently zeroed large files on this workspace's virtiofs mount (confirmed - not
# just the cross-filesystem case first found copying into ~/intermine-build); `cat >` is the
# proven-safe alternative. STATUS.md/CURATION_GUIDE.md at the repo root are committed snapshots
# of out/_docs/ (itself gitignored) - this target is the only thing that refreshes them.
docs:       ; $(PY) -m rdfc2im docs
	cat out/_docs/STATUS.md > STATUS.md
	cat out/_docs/CURATION_GUIDE.md > CURATION_GUIDE.md
# pytest when it is importable, else the bare runner in tests/run.py. Chosen by an import, never
# by `||`: that would re-run everything under the bare runner whenever pytest reports failures.
HAVE_PYTEST := $(shell $(PY) -c 'import pytest' 2>/dev/null && echo yes)
test:       ; $(if $(HAVE_PYTEST),$(PY) -m pytest -q tests,$(PY) tests/run.py)
# Live checks against a mine tools/full-build.sh has actually built and started (tests/
# test_live_mine.py). Every other test here runs offline on synthetic fixtures; these ask the
# running system real questions, because that is the only thing that catches the failure mode
# this project keeps hitting - a step that reports success while silently doing nothing. They
# SKIP unless RDFC2IM_LIVE=1 (set here) and a mine is reachable, so plain `make test` stays green
# offline and ignores a stale mine left running from an earlier session.
# Override the endpoints with MINE_BASE=... SOLR_BASE=... for a mine on another host.
test-live:  ; RDFC2IM_LIVE=1 $(if $(HAVE_PYTEST),$(PY) -m pytest -q tests/test_live_mine.py -v,$(PY) tests/run.py)
# humanmine-items/build.gradle declares `resources { srcDirs = ['resources'] }` (the layout every
# Java-less bio-source uses), so the keys/additions must land in resources/, not src/main/resources/.
# `cat src > dst`, not `cp`: plain `cp` has silently zeroed files on this virtiofs-backed
# workspace mount (confirmed live - a 4097-byte file copied to 4097 bytes of pure NUL), same
# issue `make docs` was already patched for below; fork-sync wasn't, until it hit it for real.
fork-sync:  ; mkdir -p humanmine-items/resources && rm -f humanmine-items/resources/humanmine-items_keys.properties humanmine-items/resources/humanmine-items_additions.xml && cat out/_mine/humanmine-items_keys.properties > humanmine-items/resources/humanmine-items_keys.properties && cat out/_mine/humanmine-items_additions.xml > humanmine-items/resources/humanmine-items_additions.xml
clean:      ; rm -rf out/_mine out/_docs
