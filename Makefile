# Four commands, in the order you need them: check -> install -> test -> update.
# `lint` is the developer gate that CI runs.
RUFF ?= ruff

.PHONY: check install update test lint hooks secrets dev-test move-house backup restore

check:          ## can this host run the stack? changes nothing
	sh tools/preflight.sh

install:        ## first run: writes .env, creates broker accounts, starts everything
	sh tools/install.sh

update:         ## after editing .env or git pull: re-derive secrets, rebuild, recreate what changed
	@cat config/mosquitto/dynamic-security.json 2>/dev/null | cksum > .broker-accounts.before || true
	sh tools/secrets.sh
	@# no BuildKit attestations: they change the image id at every build and so recreate containers that did not change
	BUILDX_NO_DEFAULT_ATTESTATIONS=1 docker compose up -d --build --remove-orphans
	@# the broker reads its accounts only at start (a HUP does not reload them): restart it when they changed
	@cat config/mosquitto/dynamic-security.json | cksum | cmp -s - .broker-accounts.before \
	  || { echo "broker accounts changed: restarting mosquitto"; docker compose restart mosquitto >/dev/null; }
	@rm -f .broker-accounts.before
	@# each plug-in's after-start step (a container whose profile went off is removed)
	sh -c '. tools/envlib.sh; plugin_stage update' || true
	docker compose ps

move-house:     ## move this checkout's house files into HOUSE_DIR (set it in .env first), then make update
	sh tools/move_house.sh

backup:         ## a backup now (the backend makes one every night): house, .env, zigbee2mqtt data, own plug-ins
	python3 tools/backup.py make

restore:        ## put a backup back: make restore FILE=backups/yashome-backup-....tar.gz (the current files are kept aside)
	sh tools/restore.sh "$(FILE)"

secrets:        ## (internal) broker accounts (dynamic-security.json), the plug-ins' files, COMPOSE_FILE
	sh tools/secrets.sh

test:           ## the core's unit tests inside the home image + a smoke test (plug-ins' tests: make dev-test)
	docker compose run --rm --no-deps -T -e HOME_STACK_ROOT=/app/tests/fixtures -e HOUSE_ROOT= home \
	    python -m unittest discover -s /app/tests -v
	sh tools/smoke.sh

lint:           ## ruff, py_compile, JS syntax, JSON, sh -n (what CI runs)
	$(RUFF) check app.py core helper tools/*.py tools/yashctl plugins tests
	python3 tools/core_source.py | $(RUFF) check --stdin-filename core_as_one.py -
	python3 -c 'import sys; compile(open("tools/yashctl").read(), "tools/yashctl", "exec")'
	python3 -m py_compile app.py core/*.py helper/helper.py tools/*.py
	python3 tools/check_plugins.py
	python3 tools/check_js.py index.html sw.js i18n.js
	for f in $$(git ls-files '*.json' 2>/dev/null || ls *.json examples/*.json); do python3 -m json.tool "$$f" >/dev/null || exit 1; done
	for f in tools/*.sh $$(find plugins -path '*/setup/*.sh'); do sh -n "$$f" || exit 1; done
	for f in $$(find plugins -path '*/host/*.sh'); do bash -n "$$f" || exit 1; done
	for f in $$(find plugins -path '*/server/*.sh'); do sh -n "$$f" || exit 1; done

hooks:          ## (developers) make lint before every commit, in this checkout (tools/githooks/pre-commit)
	git config core.hooksPath tools/githooks

dev-test:       ## (developers) every test outside docker: the core's and the plug-ins', in .venv
	@[ -x .venv/bin/python ] || python3 -m venv .venv
	.venv/bin/pip -q install -r requirements.txt -r requirements-dev.txt
	HOME_STACK_ROOT=tests/fixtures .venv/bin/python -m unittest discover -s tests
	.venv/bin/python -m pytest -q tests/plugins
