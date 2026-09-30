"""Test harness of the shipped plug-ins: the core of this checkout on a private copy of its fixtures,
every plug-in of plugins/ loaded once. A plug-in's own tests live in tests/plugins/test_<name>.py, use
`import app` (the core) and take the plug-in module from sys.modules (see test_NAME.py.example)."""
import atexit
import os
import pathlib
import shutil
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
CORE = HERE.parent.parent
_FIX = tempfile.mkdtemp(prefix="yashome-test-")
atexit.register(shutil.rmtree, _FIX, ignore_errors=True)
shutil.copytree(CORE / "tests" / "fixtures", _FIX, dirs_exist_ok=True)
if (HERE / "data").is_dir():                              # the plug-ins' data files, test copies
    shutil.copytree(HERE / "data", _FIX, dirs_exist_ok=True)
os.environ["HOME_STACK_ROOT"] = _FIX
# settings plug-ins read from the environment at import: test values only (tests/plugins/test.env)
for _line in (HERE / "test.env").read_text().splitlines():
    if _line.strip() and not _line.startswith("#"):
        os.environ.setdefault(*_line.split("=", 1))
sys.path.insert(0, str(CORE))
import app  # noqa: E402

LOADED = app._load_plugins(CORE / "plugins", top="yashome_plugins")
