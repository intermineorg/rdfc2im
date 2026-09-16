# rdfc2im workspace Makefile - every target is `python3 -m rdfc2im <cmd>` with the flags spelled out.
PY      ?= python3
LIMIT   ?= 20        # total rows per query; 0 = all
ITERATE ?= 5000      # page size for fetch; 0 = single request per query
SLEEP   ?= 1         # seconds between requests
FETCH   ?=           # set to 1 to include fetch in `make all`
SRC     ?=
SRCFLAG  = $(foreach s,$(SRC),--source $(s))
GUESS   ?=            # set to --no-guess to exclude guess rows

.PHONY: inputs all allow translate fetch fetch-dry tsv items project check linkml docs test fork-sync clean

inputs:     ; sh tools/make-inputs.sh
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
fork-sync:  ; mkdir -p humanmine-items/resources && cp out/_mine/humanmine-items_keys.properties out/_mine/humanmine-items_additions.xml humanmine-items/resources/
clean:      ; rm -rf out/_mine out/_docs
