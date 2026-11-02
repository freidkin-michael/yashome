"""Two harnesses in one tree: the core's tests (tests/) need a bare core, and the plug-ins' conftest
(tests/plugins) loads every shipped plug-in into the same app (zigbee takes z2m, the contract test the
example). So `pytest tests` runs the core's only; name tests/plugins to run the plug-ins' (make dev-test
and the push gate run both, apart). A run that collects both stops with the reason."""
import pathlib

import pytest

PLUGINS = pathlib.Path(__file__).resolve().parent / "plugins"


def _named(config) -> bool:
    return any(PLUGINS == p or PLUGINS in p.parents
               for p in (pathlib.Path(a.split("::")[0]).resolve() for a in config.args))


def pytest_ignore_collect(collection_path, config):
    if pathlib.Path(collection_path).resolve() == PLUGINS and not _named(config):
        return True
    return None


def pytest_collection_modifyitems(config, items):
    inside = {PLUGINS in pathlib.Path(i.path).resolve().parents for i in items}
    if inside == {True, False}:
        raise pytest.UsageError("run tests/ and tests/plugins apart: the plug-ins' harness loads every plug-in "
                                "into the app the core's tests need bare (make dev-test runs both)")


def pytest_report_header(config):
    if not _named(config):
        return "tests/plugins not collected: run pytest tests/plugins for the plug-ins (make dev-test runs both)"
