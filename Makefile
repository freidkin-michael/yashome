# Four commands, in the order you need them: check -> install -> test -> update.
# `lint` is the developer gate that CI runs.
RUFF ?= ruff

.PHONY: check install update test lint secrets

check:          ## can this host run the stack? changes nothing
	sh tools/preflight.sh

install:        ## first run: writes .env, creates broker accounts, starts everything
	sh tools/install.sh

update:         ## after editing .env or git pull: re-derive secrets, rebuild, recreate what changed
	sh tools/secrets.sh
	docker compose up -d --build --remove-orphans
	grep -Eq '^COMPOSE_PROFILES=(.*,)?zigbee(,|$$)' .env || docker compose --profile zigbee rm -sf zigbee2mqtt >/dev/null 2>&1 || true
	docker compose exec -T mosquitto kill -HUP 1 >/dev/null 2>&1 || true
	docker compose ps

secrets:        ## (internal) config/mosquitto/passwd + zigbee2mqtt secret.yaml from .env
	sh tools/secrets.sh

test:           ## unit tests inside the home image + a smoke test against the running stack
	docker compose run --rm --no-deps -T -e HOME_STACK_ROOT=/app/tests/fixtures home \
	    python -m unittest discover -s /app/tests -v
	sh tools/smoke.sh

lint:           ## ruff, py_compile, JS syntax, JSON, sh -n (what CI runs)
	$(RUFF) check app.py tools/*.py
	python3 -m py_compile app.py tools/*.py
	python3 tools/check_js.py index.html sw.js i18n.js
	for f in $$(git ls-files '*.json' 2>/dev/null || ls *.json examples/*.json); do python3 -m json.tool "$$f" >/dev/null || exit 1; done
	for f in tools/*.sh; do sh -n "$$f" || exit 1; done
