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
fetch:      ; $(PY) -m rdfc2im fetch --limit $(LIMIT) --iterate $(ITERATE) --sleep $(SLEEP) $(SRCFLAG)
fetch-dry:  ; $(PY) -m rdfc2im fetch --dry-run --limit $(LIMIT) --iterate $(ITERATE) $(SRCFLAG)
tsv:        ; $(PY) -m rdfc2im tsv $(SRCFLAG)
items:      ; $(PY) -m rdfc2im items $(SRCFLAG)
linkml:     ; $(PY) -m rdfc2im linkml
project:    ; $(PY) -m rdfc2im project
check:      ; $(PY) -m rdfc2im check
docs:       ; $(PY) -m rdfc2im docs
test:       ; $(PY) -m pytest -q tests 2>/dev/null || $(PY) tests/run.py
# humanmine-items/build.gradle declares `resources { srcDirs = ['resources'] }` (the layout every
# Java-less bio-source uses), so the keys/additions must land in resources/, not src/main/resources/.
fork-sync:  ; mkdir -p humanmine-items/resources && rm -f humanmine-items/resources/humanmine-items_keys.properties humanmine-items/resources/humanmine-items_additions.xml && cp out/_mine/humanmine-items_keys.properties out/_mine/humanmine-items_additions.xml humanmine-items/resources/
clean:      ; rm -rf out/_mine out/_docs
