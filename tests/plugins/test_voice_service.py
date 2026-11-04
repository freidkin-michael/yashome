"""voice: the one voice container (the plug-in's README, "One voice container"): its compose service, run.py that keeps both
parts running, and the recognizer service's settings that come from Settings -> Voice instead of the env."""
import os
import sys
import time
from unittest import mock

import pytest
import yaml

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
VOICE = os.path.join(ROOT, "plugins", "tools", "voice")
VAD = os.path.join(VOICE, "service", "vad")


@pytest.fixture(scope="module")
def vad():
    """voice_vad imported with its network modules stubbed: no thread, no socket (the service starts only as __main__)."""
    stubs = {"webrtcvad": mock.MagicMock(), "paho": mock.MagicMock(), "paho.mqtt": mock.MagicMock(), "paho.mqtt.client": mock.MagicMock()}
    try:
        import requests  # noqa: F401
    except ImportError:                                   # the image has it, a test host may not
        stubs["requests"] = mock.MagicMock()
    env = {"VA_LLM_URL": "", "VA_BIG_URL": "", "DASHBOARD_TOKEN": "t", "VA_PHRASELOG_DIR": "", "VA_COMMANDS": "/nonexistent/voice_commands.json"}
    sys.path.insert(0, VAD)
    try:
        with mock.patch.dict(sys.modules, stubs), mock.patch.dict(os.environ, env):
            sys.modules.pop("voice_vad", None)
            import voice_vad
            yield voice_vad
    finally:
        sys.path.remove(VAD)


def test_one_compose_service_runs_both_parts_under_their_old_names():
    with open(os.path.join(VOICE, "setup", "compose.yml"), encoding="utf-8") as f:
        c = yaml.safe_load(f)
    assert list(c["services"]) == ["voice"]
    s = c["services"]["voice"]
    assert s["profiles"] == ["voice"] and s["build"] == {"context": "plugins/tools/voice", "dockerfile": "service/Dockerfile"}
    assert sorted(s["networks"]["default"]["aliases"]) == ["tts", "voice-vad"]                # the names the plug-in calls
    assert s["ports"] == ["${BIND_ADDRESS:-0.0.0.0}:8091:8091"]                               # 8092 and 8093 stay inside
    assert "${HOUSE_DIR:-.}/plugin-data/voice:/house" in s["volumes"]
    env = {e.split("=", 1)[0] for e in s["environment"]}
    assert not {e for e in env if e.startswith("VA_")} - {"VA_SAMPLES_UID"}                    # the house's settings are no env lines
    assert "env_file" not in s                                                                # only the keys it needs, not all of .env
    with open(os.path.join(VOICE, "service", "Dockerfile"), encoding="utf-8") as f:
        df = f.read()
    for part in ("service/run.py", "service/tts/server.py", "service/vad/*.py"):
        assert part in df


def _supervisor(tmp_path):
    sys.path.insert(0, os.path.join(VOICE, "service"))
    try:
        sys.modules.pop("run", None)
        import run
    finally:
        sys.path.pop(0)
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "part.py").write_text("import time\nopen('started', 'a').write('x')\ntime.sleep(60)\n")
    (tmp_path / "b" / "part.py").write_text("open('started', 'a').write('x')\n")                # stops at once
    return run, {"a": (str(tmp_path / "a"), "part.py"), "b": (str(tmp_path / "b"), "part.py")}


def test_a_part_that_stops_is_started_again_and_the_other_runs_on(tmp_path):
    run, parts = _supervisor(tmp_path)
    end = time.time() + 2.5
    restarts = run.run(parts, pause=0.1, should_stop=lambda: time.time() > end)          # absolute folders: HERE is not used
    assert restarts["a"] == 0 and restarts["b"] >= 2
    assert (tmp_path / "a" / "started").read_text() == "x"
    assert restarts["b"] <= len((tmp_path / "b" / "started").read_text()) <= restarts["b"] + 1


def test_the_settings_that_were_env_lines_come_from_the_page(vad):
    import voice_rules                                                                        # apply_config sets its wake fields: kept for the others
    with mock.patch.multiple(voice_rules, WAKE=voice_rules.WAKE, WAKE_MAXDIST=voice_rules.WAKE_MAXDIST, WAKE_VARIANTS=voice_rules.WAKE_VARIANTS), \
            mock.patch.multiple(vad, LLM_URL="", BIG_URL="", BIG_HEALTH="", WAKE_ON_DEVICE=False, TV_DEVICE="", LLM_ON=True, ASK_TTL=vad.ASK_TTL), \
            mock.patch.object(vad.threading, "Thread") as th:
        vad.apply_config({"llm_url": "http://192.0.2.1:11434", "big_url": "http://192.0.2.1:11435", "big_health": "http://192.0.2.1:11436/health",
                          "wake_on_device": True, "tv_device": "tv_1"})
        assert (vad.LLM_URL, vad.BIG_URL, vad.BIG_HEALTH, vad.WAKE_ON_DEVICE, vad.TV_DEVICE) == (
            "http://192.0.2.1:11434", "http://192.0.2.1:11435", "http://192.0.2.1:11436/health", True, "tv_1")
        assert th.call_count == 1                                                             # another model host: the clients made again
        vad.apply_config({"ask_ttl": 12})                                                     # a field left out keeps its value
        assert vad.LLM_URL == "http://192.0.2.1:11434" and th.call_count == 1
        vad.apply_config({"llm_url": ""})                                                     # off: nothing is started
        assert vad.LLM_URL == "" and th.call_count == 1 and vad._llm["ollama"] is None


def test_the_seed_carries_what_the_service_runs_with(vad):
    with mock.patch.multiple(vad, LLM_URL="http://192.0.2.1:11434", BIG_URL="", BIG_HEALTH="", WAKE_ON_DEVICE=True, TV_DEVICE="tv_1"):
        seed = vad._env_config()
    assert (seed["llm_url"], seed["big_url"], seed["wake_on_device"], seed["tv_device"]) == ("http://192.0.2.1:11434", "", True, "tv_1")
    with mock.patch.multiple(vad, LLM_URL="file:///etc/passwd", TV_DEVICE="a/b"):
        seed = vad._env_config()
    assert "llm_url" not in seed and "tv_device" not in seed                                  # what the page would refuse is not sent


def test_the_tv_commands_need_a_tv_set_on_the_page(vad):
    with mock.patch.object(vad, "TV_DEVICE", ""):
        assert vad.try_tv("выключи телевизор") is False   # i18n
    posted = []
    with mock.patch.object(vad, "TV_DEVICE", "tv_1"), mock.patch.object(vad, "requests", mock.Mock(post=lambda url, **kw: posted.append((url, kw["json"])))):
        assert vad.try_tv("выключи телевизор") is True   # i18n
    assert posted == [("http://home:8765/api/devices/tv_1/control", {"code": "power", "value": False})]


def test_the_wake_check_on_the_device_trusts_every_peer_but_the_ticked_ones(vad):
    with mock.patch.multiple(vad, WAKE_ON_DEVICE=True, NOWAKE_IPS=set(), NEEDWAKE_IPS={"192.0.2.9"}):
        assert vad.wake_checked_on_device("192.0.2.8") and not vad.wake_checked_on_device("192.0.2.9")
    with mock.patch.multiple(vad, WAKE_ON_DEVICE=False, NOWAKE_IPS=set(), NEEDWAKE_IPS=set()):
        assert not vad.wake_checked_on_device("192.0.2.8")                                   # off: every phrase needs the wake word


def test_no_house_of_its_own_in_the_service(vad):
    assert vad._DEFAULT_DEVICES == {} and vad.APPLIANCES_FALLBACK == []                       # the phrase map is the house's file
    assert vad.CMD_PATH == "/nonexistent/voice_commands.json" and vad.AC_API == "http://home:8765/api/devices"
    src = {n: open(os.path.join(VAD, n), encoding="utf-8").read() for n in ("voice_vosk.py", "voice_record.py", "voice_phraselog.py", "voice_state.py")}
    assert '"VA_VOSK_DIR", "/vosk"' in src["voice_vosk.py"]                                  # /models is the TTS's volume
    assert '"/house/wake-samples"' in src["voice_record.py"] and '"/house/wake_phrases.json"' in src["voice_record.py"]
    assert '"/house/phraselog"' in src["voice_phraselog.py"] and '"/house/voice_glossary.json"' in src["voice_state.py"]
