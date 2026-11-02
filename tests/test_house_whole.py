"""Temperatures show as whole numbers everywhere: an older house's tenths choice (temp_precision) is dropped on load and
the house setting has no such field."""
import atexit
import os
import shutil
import sys
import tempfile

if "app" not in sys.modules:                 # alone: the core on a private copy of the fixtures, as test_pure does
    _FIX = tempfile.mkdtemp(prefix="yashome-test-")
    atexit.register(shutil.rmtree, _FIX, ignore_errors=True)
    shutil.copytree(os.path.join(os.path.dirname(__file__), "fixtures"), _FIX, dirs_exist_ok=True)
    os.environ["HOME_STACK_ROOT"] = _FIX
    os.environ.pop("HOUSE_ROOT", None)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app  # noqa: E402


def test_an_older_tenths_choice_is_dropped_and_no_field_carries_it():
    assert "temp_precision" not in app._clean_house({"lang": "en", "temp_precision": 0.1})
    assert "temp_precision" not in app.HouseBody.model_fields
    assert not hasattr(app, "house_temp_precision")
