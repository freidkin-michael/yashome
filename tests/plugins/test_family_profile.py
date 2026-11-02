"""make secrets, family: the "family" compose profile follows the Firebase key; a helper job that cannot see the key
(outside its mounts) keeps the profile as it is, so turning another plug-in off never drops the bridge."""
import pathlib
import shutil
import subprocess
import tempfile

import pytest

CORE = pathlib.Path(__file__).resolve().parents[2]
SETUP = "plugins/tools/family/setup"


@pytest.fixture
def tree():
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "tools").mkdir()
    shutil.copy(CORE / "tools" / "envlib.sh", d / "tools")
    (d / SETUP).mkdir(parents=True)
    for f in ("secrets.sh", "state.example.json"):
        shutil.copy(CORE / SETUP / f, d / SETUP)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _run(d, key, job):
    (d / ".env").write_text(f"HOUSE_DIR=./house\nFAMILY_BRIDGE_URL=\nFAMILY_FIREBASE_KEY={key}\nCOMPOSE_PROFILES=voice,family,zigbee\n")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(d)} | ({"YASHOME_JOB": "1"} if job else {})
    out = subprocess.run(["sh", f"{SETUP}/secrets.sh"], cwd=d, capture_output=True, text=True, env=env, check=True).stdout
    prof = next(l.split("=", 1)[1] for l in (d / ".env").read_text().splitlines() if l.startswith("COMPOSE_PROFILES="))
    return set(prof.split(",")), out


@pytest.mark.skipif(not shutil.which("sh"), reason="needs sh")
def test_a_job_that_cannot_see_the_key_keeps_the_profile_the_host_drops_it_a_present_key_keeps_it(tree):
    prof, out = _run(tree, "/nowhere/firebase-key.json", job=True)
    assert prof == {"voice", "family", "zigbee"} and "kept as it is" in out
    prof, out = _run(tree, "/nowhere/firebase-key.json", job=False)
    assert prof == {"voice", "zigbee"} and "off" in out                       # on the host a missing key is missing
    (tree / "key.json").write_text("{}")
    prof, _ = _run(tree, str(tree / "key.json"), job=True)
    assert prof == {"voice", "family", "zigbee"}
