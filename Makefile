# Four commands, in the order you need them: check -> install -> test -> update.
# `lint` is the developer gate that CI runs.
RUFF ?= ruff

.PHONY: check install update test lint secrets dev-test

check:          ## can this host run the stack? changes nothing
	sh tools/preflight.sh

install:        ## first run: writes .env, creates broker accounts, starts everything
	sh tools/install.sh

update:         ## after editing .env or git pull: re-derive secrets, rebuild, recreate what changed
	@cat config/mosquitto/dynamic-security.json 2>/dev/null | cksum > .broker-accounts.before || true
	sh tools/secrets.sh
	docker compose up -d --build --remove-orphans
	@# the broker reads its accounts only at start (a HUP does not reload them): restart it when they changed
	@cat config/mosquitto/dynamic-security.json | cksum | cmp -s - .broker-accounts.before \
	  || { echo "broker accounts changed: restarting mosquitto"; docker compose restart mosquitto >/dev/null; }
	@rm -f .broker-accounts.before
	@# each plug-in's after-start step (a container whose profile went off is removed)
	sh -c '. tools/envlib.sh; plugin_stage update' || true
	docker compose ps

secrets:        ## (internal) broker accounts (dynamic-security.json), the plug-ins' files, COMPOSE_FILE
	sh tools/secrets.sh

test:           ## unit tests inside the home image + a smoke test against the running stack
	docker compose run --rm --no-deps -T -e HOME_STACK_ROOT=/app/tests/fixtures home \
	    python -m unittest discover -s /app/tests -v
	sh tools/smoke.sh

lint:           ## ruff, py_compile, JS syntax, JSON, sh -n (what CI runs)
	$(RUFF) check app.py tools/*.py plugins tests
	python3 -m py_compile app.py tools/*.py
	python3 tools/check_plugins.py
	python3 tools/check_js.py index.html sw.js i18n.js
	for f in $$(git ls-files '*.json' 2>/dev/null || ls *.json examples/*.json); do python3 -m json.tool "$$f" >/dev/null || exit 1; done
	for f in tools/*.sh plugins/*/setup/*.sh; do sh -n "$$f" || exit 1; done

dev-test:       ## (developers) every test outside docker: the core's and the plug-ins', in .venv
	@[ -x .venv/bin/python ] || python3 -m venv .venv
	.venv/bin/pip -q install -r requirements.txt -r requirements-dev.txt
	HOME_STACK_ROOT=tests/fixtures .venv/bin/python -m unittest discover -s tests
	.venv/bin/python -m pytest -q tests/plugins
