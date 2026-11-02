"""voice: the TTS service (voice choice, WAV 16 kHz, files by random name, the key) and the plug-in's client of it."""
import array
import http.client
import importlib.util
import io
import json
import logging
import os
import sys
import threading
import time
import wave
from http.server import ThreadingHTTPServer

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import app as core

me = sys.modules["yashome_plugins.voice"]
HOUSE = {"Authorization": "Bearer " + core.DASHBOARD_TOKEN}


@pytest.fixture
def tts(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(os.path.join(os.path.dirname(me.__file__), "service", "tts"))        # server.py imports numwords beside it
    spec = importlib.util.spec_from_file_location("tts_server", os.path.join(os.path.dirname(me.__file__), "service", "tts", "server.py"))
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)
    monkeypatch.setattr(srv, "DATA", tmp_path)
    return srv


def wav_info(raw):
    with wave.open(io.BytesIO(raw), "rb") as w:
        return w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()


def test_the_voice_follows_the_language_of_the_phrase(tts):
    ru, en = "ru-xenia", "en-en_99"
    ru_text = "".join(chr(c) for c in (0x413, 0x43e, 0x442, 0x43e, 0x432, 0x43e))          # a Russian word
    assert tts.pick_voice("The light is on") == en and tts.pick_voice(ru_text) == ru
    assert tts.pick_voice("Done 24", lang="ru") == ru and tts.pick_voice(ru_text, lang="en") == en
    assert tts.pick_voice("x", voice=en) == en and tts.pick_voice("x", voice="nope") == en
    assert tts.pick_voice("x", voice="ru-baya") == "ru-baya" and tts.pick_voice("x", voice="en-en_5") == "en-en_5"    # any Silero speaker
    assert tts.pick_voice(ru_text, voice="ru-irina") == ru and tts.pick_voice("x", voice="de-x") == en     # an unknown one: the language's default
    assert tts.VOICES == [ru, en] and tts.ALL_VOICES[:2] == [ru, en] and "ru-baya" in tts.ALL_VOICES and "en-random" not in tts.ALL_VOICES
    assert tts.pick_voice("1 2 3") == en                                                  # no letters: the default language


def test_audio_is_resampled_to_16_khz_stereo(tts):
    one_second = b"\x00\x10" * 22050
    rate, ch, width, frames = wav_info(tts.to_wav16(one_second, 22050))
    assert (rate, ch, width) == (16000, 2, 2) and abs(frames - 16000) <= 2        # the mono channel is repeated
    assert wav_info(tts.to_wav16(b"\x00\x00" * 160, 16000))[3] == 160
    with wave.open(io.BytesIO(tts.to_wav16(b"\x01\x00\x02\x00", 16000)), "rb") as w:
        assert w.readframes(2) == b"\x01\x00\x01\x00\x02\x00\x02\x00"                 # left = right = the sample


def test_a_wav_with_lead_in_starts_with_faint_noise_not_zeros(tts):
    """The satellite's codec path mutes on digital zeros: a lead-in of quiet noise opens it before the first syllable."""
    lead = 16000 * tts.LEAD_IN_MS // 1000
    assert wav_info(tts.to_wav16(b"\x00\x00" * 160, 16000, lead_in=True))[3] == lead + 160
    raw = tts.to_wav16(b"\x00\x00" * 1600, 16000, lead_in=True)                                         # a silent reply: the head is still not zeros
    with wave.open(io.BytesIO(raw), "rb") as w:
        a = array.array("h", w.readframes(lead))
    assert 0.3 <= tts.LEAD_IN_MS / 1000 <= 0.5 and a[0::2] == a[1::2]                      # left = right
    assert max(abs(x) for x in a) <= tts.LEAD_IN_AMP and sum(1 for x in a if x) > lead * 0.95
    assert raw == tts.to_wav16(b"\x00\x00" * 1600, 16000, lead_in=True)                                  # the same bytes every time


def test_the_confirmation_sounds_are_made(tts, tmp_path):
    tts.make_sounds(tmp_path / "s")
    sizes = {n: wav_info((tmp_path / "s" / f"{n}.wav").read_bytes()) for n in ("listening", "done", "error", "thinking")}
    assert all(i[0] == 16000 and i[1] == 2 for i in sizes.values())
    lead = {n: wav_info((tmp_path / "s" / f"{n}_lead.wav").read_bytes())[3] for n in sizes}
    assert all(lead[n] == sizes[n][3] + 16000 * tts.LEAD_IN_MS // 1000 for n in sizes)     # the second variant is the same sound after the noise
    assert sizes["error"][3] > sizes["listening"][3] > 0                                   # the error one is the longest, with a gap


def test_spoken_files_get_random_names_and_old_ones_go(tts, tmp_path):
    a, b = tts.store(b"RIFFa"), tts.store(b"RIFFb")
    assert a != b and len(a) >= 20 and (tmp_path / "a" / f"{a}.wav").read_bytes() == b"RIFFa"
    old = tmp_path / "a" / f"{a}.wav"
    os.utime(old, (time.time() - 2 * tts.KEEP_S,) * 2)
    tts.store(b"RIFFc")
    assert not old.exists() and (tmp_path / "a" / f"{b}.wav").exists()


def serve(tts):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), tts.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def call(port, method, path, body=None, key=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers={"X-Voice-Key": key} if key else {})
    r = c.getresponse()
    return r.status, r.read()


def test_the_service_speaks_only_with_the_key(tts, monkeypatch):
    monkeypatch.setattr(tts.VOICE_SET, "say", lambda text, name, lead_in=False: tts.to_wav16(b"\x00\x08" * 1600, 16000))
    httpd, port = serve(tts)
    try:
        monkeypatch.setattr(tts, "KEY", "")
        assert call(port, "POST", "/say", {"text": "hi"}, key="x")[0] == 503              # no key configured: it refuses
        monkeypatch.setattr(tts, "KEY", "secret-key")
        assert call(port, "POST", "/say", {"text": "hi"})[0] == 403
        assert call(port, "POST", "/say", {"text": "hi"}, key="wrong")[0] == 403
        assert call(port, "POST", "/say", {"text": ""}, key="secret-key")[0] == 400
        assert call(port, "POST", "/say", {"text": "x" * 501}, key="secret-key")[0] == 400
        st, raw = call(port, "POST", "/say", {"text": "The light is on"}, key="secret-key")
        out = json.loads(raw)
        assert st == 200 and out["voice"].startswith("en-") and out["url"].startswith("/a/") and out["seconds"] == 0.1
        st, wav = call(port, "GET", out["url"])
        assert st == 200 and wav_info(wav)[0] == 16000                                       # the file is served without a key
        assert call(port, "GET", "/a/doesnotexist1.wav")[0] == 404 and call(port, "GET", "/etc/passwd")[0] == 404
        assert json.loads(call(port, "GET", "/health")[1])["ok"] is True
        monkeypatch.setattr(tts, "KEY", "")
        monkeypatch.setattr(tts, "OPEN", True)                                            # a computer's: its loopback only, no key
        assert call(port, "POST", "/say", {"text": "The light is on"})[0] == 200
    finally:
        httpd.shutdown()


def test_the_plug_in_asks_the_service_with_its_key(monkeypatch):
    monkeypatch.setenv("BIND_ADDRESS", "192.0.2.7")
    monkeypatch.delenv("VOICE_TTS_KEY", raising=False)
    with pytest.raises(RuntimeError, match="VOICE_TTS_KEY"):
        me.say_url("hi")
    monkeypatch.setenv("VOICE_TTS_KEY", "k")
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout=0):
        seen["key"], seen["body"], seen["url"] = req.headers.get("X-voice-key"), json.loads(req.data), req.full_url
        return Resp(json.dumps({"url": "/a/abc.wav", "voice": "en-en_99"}).encode())
    monkeypatch.setattr(me.urllib.request, "urlopen", fake)
    out = me.say_url("hi", lang="en")
    assert out["url"] == "http://192.0.2.7:8765/plugins/voice/tts/a/abc.wav" and seen["key"] == "k" and seen["body"]["text"] == "hi"
    assert seen["url"] == "http://tts:8092/say"
    assert me.sound_url("done") == "http://192.0.2.7:8765/plugins/voice/tts/s/done.wav"
    with pytest.raises(ValueError):
        me.sound_url("../etc")
    monkeypatch.delenv("BIND_ADDRESS")
    with pytest.raises(RuntimeError, match="BIND_ADDRESS"):
        me.say_url("hi")


def test_the_status_route_is_for_the_dashboard(monkeypatch):
    monkeypatch.setattr(me, "tts_health", lambda timeout=3.0: {"ok": True, "voices": ["v"]})
    c = TestClient(core.app)
    assert c.get("/api/voice/status").status_code in (401, 403)
    r = c.get("/api/voice/status", headers=HOUSE)
    assert r.status_code == 200 and r.json()["tts"]["ok"] is True and "key" in r.json()


# ---- step 2: satellites, cards, say/play/stop/volume, wake and reply ----
@pytest.fixture
def vo(monkeypatch):
    sent = []
    monkeypatch.setattr(me, "_pub", lambda topic, payload: sent.append((topic, payload)))     # not core.mqtt_pub: its alias can be pinned by an earlier test
    monkeypatch.setattr(core, "_save_settings", lambda: None)
    monkeypatch.setenv("BIND_ADDRESS", "192.0.2.7")
    monkeypatch.setenv("VOICE_TTS_KEY", "k")
    monkeypatch.setattr(me, "say_url", lambda text, lang=None, voice=None, timeout=20.0, lead_in=False: {"url": "http://192.0.2.7:8765/plugins/voice/tts/a/x.wav", "voice": voice or "v", "seconds": 1.0, "ms": 5})
    n_sub, n_h = len(core.MQTT_SUBSCRIPTIONS), len(core.MQTT_HANDLERS)
    me._cfg["satellites"] = []
    me._stats.clear()
    me._subscribed.clear()
    yield sent
    me._cfg["satellites"] = []
    me._sync_devices()
    del core.MQTT_SUBSCRIPTIONS[n_sub:]
    del core.MQTT_HANDLERS[n_h:]


def add(c, sid="kitchen", **kw):
    body = {"node": "kit-node", "mic": True, "player": True, "room": "kitchen", "peer": "192.0.2.50", "wake_chime": True, **kw}
    return c.put(f"/api/voice/satellites/{sid}", headers=HOUSE, json=body)


def values(did):
    return core.status_copy(did, {}).get("values") or {}


def test_a_satellite_makes_a_card_per_role_and_goes_with_its_cards(vo):
    c = TestClient(core.app)
    assert add(c).status_code == 200
    assert core.DEVICES["kitchen_mic"]["category"] == "voice" and core.DEVICES["kitchen_spk"]["transport"] == "voice"
    codes = [x["code"] for x in core.DEVICES["kitchen_spk"]["controls"]]
    assert codes == ["now", "volume", "stop", "test", "say", "play"] and [x["code"] for x in core.DEVICES["kitchen_mic"]["controls"]] == ["listen", "last_wake", "bg_record", "bg_cancel"]
    assert [s["id"] for s in c.get("/api/voice/satellites", headers=HOUSE).json()] == ["kitchen"]
    assert "kit-node/wake" in core.MQTT_SUBSCRIPTIONS and "kit-node/player/state" in core.MQTT_SUBSCRIPTIONS
    assert add(c, "only-mic", node="mic-only", mic=True, player=False).status_code == 200 and "only-mic_spk" not in core.DEVICES
    assert c.delete("/api/voice/satellites/kitchen", headers=HOUSE).status_code == 200
    assert "kitchen_mic" not in core.DEVICES and "kitchen_spk" not in core.DEVICES and "only-mic_mic" in core.DEVICES
    assert c.delete("/api/voice/satellites/kitchen", headers=HOUSE).status_code == 404


def test_a_satellite_cannot_take_the_house_bus_or_another_name(vo):
    c = TestClient(core.app)
    for node in ("home", "homeassistant", "panel", "voice"):
        assert add(c, node=node).status_code == 409, node
    assert add(c).status_code == 200
    assert add(c, "other", node="kit-node").status_code == 409                       # the node of another satellite
    assert add(c, "kitchen", node="kit-node", room="hall").status_code == 200       # the same satellite edited
    assert add(c, "x", mic=False, player=False, node="n2").status_code == 400
    assert c.put("/api/voice/satellites/Bad Id", headers=HOUSE, json={"node": "n3"}).status_code in (400, 404, 422)
    assert c.put("/api/voice/satellites/kitchen", json={"node": "kit-node"}).status_code in (401, 403)


def test_the_topics_of_a_satellite_drive_its_cards(vo):
    c = TestClient(core.app)
    add(c, volume_state="kit-node/number/kit_volume/state")
    feed = lambda topic, payload, retain=False: me._on_node(topic.split("/"), payload.encode(), retain)
    feed("kit-node/status", "online")
    assert core.status_copy("kitchen_mic", {}).get("online") is True and core.status_copy("kitchen_spk", {}).get("online") is True
    feed("kit-node/status", "offline")
    assert core.status_copy("kitchen_spk", {}).get("online") is False
    feed("kit-node/player/state", "playing")
    assert values("kitchen_spk")["now"].startswith("playing")
    feed("kit-node/player/state", "idle")
    assert values("kitchen_spk")["now"] == "idle"
    feed("kit-node/listen/state", "OFF")
    assert values("kitchen_mic")["listen"] is False
    feed("kit-node/number/kit_volume/state", "42.000000")
    assert values("kitchen_spk")["volume"] == 42
    feed("kit-node/volume/state", "7")
    assert values("kitchen_spk")["volume"] == 42                                       # not the topic this satellite declared
    me._on_node(["unknown", "status"], b"online", False)                               # not a satellite: ignored


def test_a_wake_stops_the_player_and_says_listening_from_the_house(vo):
    c = TestClient(core.app)
    add(c)
    me._on_node(["kit-node", "wake"], b"hey_jarvis", True)                             # a replayed wake is not an event
    assert vo == []
    me._on_node(["kit-node", "wake"], b"hey_jarvis", False)
    assert vo == [("kit-node/say/url", "http://192.0.2.7:8765/plugins/voice/tts/s/listening.wav")]                 # nothing plays: no stop, the chime only
    assert values("kitchen_mic")["last_wake"] and me._stat("kitchen")["wakes"] == 1
    vo.clear()
    add(c, "quiet", node="quiet-node", mic=True, player=False)
    me._on_node(["quiet-node", "wake"], b"x", False)
    assert vo == [] and me._stat("quiet")["wakes"] == 1                                # a mic without a player: counted, nothing sent


def test_the_reply_goes_to_the_satellite_of_that_address(vo):
    c = TestClient(core.app)
    add(c)
    heard = lambda **m: me.on_heard(["voice", "heard"], json.dumps(m).encode(), False)

    def done():                                                                       # the node plays a sound and reports idle: the next may go out
        me._on_node(["kit-node", "player", "state"], b"playing", False)
        me._on_node(["kit-node", "player", "state"], b"idle", False)
    heard(ip="192.0.2.50", ok=True)
    done()
    heard(ip="192.0.2.50", ok=False)
    done()
    heard(ip="192.0.2.50", ok=True, reply="Light is on")
    done()
    heard(ip="192.0.2.50", ok=False, reply="Not understood")                            # a spoken error
    done()
    heard(ip="192.0.2.50", sound="thinking")                                           # only played, not counted
    heard(ip="192.0.2.99", ok=True)                                                    # nobody has that address
    me.on_heard(["voice", "heard"], b"not json", False)
    me.on_heard(["voice", "heard"], b'{"ip": "192.0.2.50"}', True)                      # retained: ignored
    assert vo == [("kit-node/say/url", "http://192.0.2.7:8765/plugins/voice/tts/s/done.wav"), ("kit-node/say/url", "http://192.0.2.7:8765/plugins/voice/tts/s/error.wav"),
                  ("kit-node/say/url", "http://192.0.2.7:8765/plugins/voice/tts/a/x.wav"),
                  ("kit-node/say/url", "http://192.0.2.7:8765/plugins/voice/tts/a/x.wav"), ("kit-node/say/url", "http://192.0.2.7:8765/plugins/voice/tts/s/thinking.wav")]
    assert me._stat("kitchen")["commands"] == 2 and me._stat("kitchen")["errors"] == 2


def test_a_satellite_silent_when_not_understood_plays_nothing_for_it(vo):
    c = TestClient(core.app)
    assert add(c, quiet_fail=True).json()["quiet_fail"] is True
    heard = lambda **m: me.on_heard(["voice", "heard"], json.dumps(m).encode(), False)
    heard(ip="192.0.2.50", ok=False, reply="Not understood")                            # a false wake on the TV: silence
    heard(ip="192.0.2.50", ok=False)
    assert vo == [] and me._stat("kitchen")["errors"] == 2                              # still counted
    heard(ip="192.0.2.50", ok=False, reply="Which light?", ask=True)                    # a question is still asked
    assert vo == [("kit-node/say/url", "http://192.0.2.7:8765/plugins/voice/tts/a/x.wav")]


def test_the_channels_of_a_card_reach_the_satellite(vo):
    c = TestClient(core.app)
    add(c)
    spk, mic = core.DEVICES["kitchen_spk"], core.DEVICES["kitchen_mic"]
    ctl = {x["code"]: x for x in spk["controls"]}
    core.CONTROL_KINDS["voice_press"](spk, ctl["stop"], True)
    core.CONTROL_KINDS["voice_press"](spk, ctl["test"], True)
    core.TRANSPORTS["voice"](spk, "volume", 150)
    core.TRANSPORTS["voice"](mic, "listen", False)
    core.TRANSPORTS["voice"](mic, "listen", "on")
    core.CONTROL_KINDS["voice_say"](spk, ctl["say"], "Hello there")
    core.CONTROL_KINDS["voice_play"](spk, ctl["play"], "http://192.0.2.9/a.wav")
    assert vo == [("kit-node/stop/command", "PRESS"), ("kit-node/say/url", "http://192.0.2.7:8765/plugins/voice/tts/s/done.wav"), ("kit-node/volume/set", "100"),
                  ("kit-node/listen/command", "OFF"), ("kit-node/listen/command", "ON"), ("kit-node/say/url", "http://192.0.2.7:8765/plugins/voice/tts/a/x.wav"),
                  ("kit-node/say/url", "http://192.0.2.9/a.wav")]
    for bad in (False, "off", 0):
        with pytest.raises(HTTPException):
            core.CONTROL_KINDS["voice_press"](spk, ctl["stop"], bad)
    with pytest.raises(HTTPException):
        core.CONTROL_KINDS["voice_play"](spk, ctl["play"], "file:///etc/passwd")
    with pytest.raises(HTTPException):
        core.CONTROL_KINDS["voice_say"](spk, ctl["say"], "  ")
    with pytest.raises(HTTPException):
        core.TRANSPORTS["voice"](spk, "nope", 1)


def test_the_api_names_players_by_id_room_or_all(vo):
    c = TestClient(core.app)
    add(c)
    add(c, "hall", node="hall-node", room="hall", mic=False)
    r = c.post("/api/voice/say", headers=HOUSE, json={"text": "Hello", "target": "kitchen"})
    assert r.status_code == 200 and r.json()["players"] == ["kitchen"] and vo[-1] == ("kit-node/say/url", "http://192.0.2.7:8765/plugins/voice/tts/a/x.wav")
    vo.clear()
    assert c.post("/api/voice/say", headers=HOUSE, json={"text": "Hello", "target": "all"}).json()["players"] == ["kitchen", "hall"]
    assert len(vo) == 2 and {t for t, _ in vo} == {"kit-node/say/url", "hall-node/say/url"}
    assert c.post("/api/voice/stop", headers=HOUSE, json={"target": "hall"}).json()["players"] == ["hall"]
    assert c.post("/api/voice/volume", headers=HOUSE, json={"target": "hall", "value": 30}).status_code == 200 and vo[-1] == ("hall-node/volume/set", "30")
    assert c.post("/api/voice/play", headers=HOUSE, json={"url": "http://192.0.2.9/a.wav", "target": "kitchen"}).status_code == 200
    assert c.post("/api/voice/say", headers=HOUSE, json={"text": "x", "target": "nobody"}).status_code == 404
    assert c.post("/api/voice/volume", headers=HOUSE, json={"target": "hall", "value": 101}).status_code == 422
    assert c.post("/api/voice/play", headers=HOUSE, json={"url": "ftp://x", "target": "hall"}).status_code == 422
    assert c.post("/api/voice/say", json={"text": "x", "target": "hall"}).status_code in (401, 403)


def test_a_failing_tts_is_a_503_and_nothing_is_sent(vo, monkeypatch):
    monkeypatch.setattr(me, "say_url", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tts is not reachable: OSError")))
    c = TestClient(core.app)
    add(c)
    r = c.post("/api/voice/say", headers=HOUSE, json={"text": "Hello", "target": "kitchen"})
    assert r.status_code == 503 and "tts" in r.json()["detail"] and vo == []


def test_ask_the_house_goes_to_the_voice_service_and_can_speak_the_answer(vo, monkeypatch):
    c = TestClient(core.app)
    add(c)
    calls = []
    monkeypatch.setattr(me, "_vad_post", lambda path, body, timeout=25.0: calls.append((path, body)) or {"engine": "llm", "ok": True, "said": "Switch off?", "ask": True, "token": "t1"})
    r = c.post("/api/voice/ask", json={"text": "turn it all off", "speak": "kitchen"}, headers=HOUSE)
    assert r.status_code == 200 and r.json()["token"] == "t1"
    r = c.post("/api/voice/ask/confirm", json={"token": "t1", "yes": True}, headers=HOUSE)
    assert r.status_code == 200
    assert calls == [("/ask", {"text": "turn it all off"}), ("/confirm", {"token": "t1", "yes": True})]
    assert ("kit-node/say/url", "http://192.0.2.7:8765/plugins/voice/tts/a/x.wav") in vo                     # the question was also said on the satellite
    assert c.post("/api/voice/ask", json={"text": ""}, headers=HOUSE).status_code == 422


def test_the_chat_is_streamed_from_the_voice_service(vo, monkeypatch):
    c = TestClient(core.app)
    seen = []

    class Resp:
        def __init__(self):
            self.parts = [b"Hel", b"lo", b""]

        def read1(self, n):
            return self.parts.pop(0)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(me, "_vad_open", lambda path, body, timeout=60.0: seen.append((path, body)) or Resp())
    r = c.post("/api/voice/chat", json={"text": "how are you", "history": [{"role": "user", "content": "hi"}]}, headers=HOUSE)
    assert r.status_code == 200 and r.text == "Hello" and r.headers["content-type"].startswith("text/plain")
    assert seen == [("/chat", {"text": "how are you", "history": [{"role": "user", "content": "hi"}]})]
    assert c.post("/api/voice/chat", json={"text": ""}, headers=HOUSE).status_code == 422


def test_a_tablet_satellite_is_commanded_through_the_panels_queue(vo):
    c = TestClient(core.app)
    r = c.put("/api/voice/satellites/zte", headers=HOUSE, json={"panel": "Tab-ZTE", "mic": True, "player": True, "room": "bedroom", "peer": "192.0.2.60", "wake_chime": True})
    assert r.status_code == 200 and r.json()["node"] == "panel-tab-zte" and r.json()["panel"] == "Tab-ZTE"
    vo.clear()
    me._on_node(["panel-tab-zte", "wake"], b"wake", False)                              # the panels plug-in publishes it like an ESP node
    assert vo == [("panel/Tab-ZTE/voice/cmd", json.dumps({"t": "say/url", "p": "http://192.0.2.7:8765/plugins/voice/tts/s/listening.wav"}))]
    vo.clear()
    me.set_volume(me._sat("zte"), 30)
    assert vo == [("panel/Tab-ZTE/voice/cmd", json.dumps({"t": "volume/set", "p": "30"}))]
    assert c.put("/api/voice/satellites/bad", headers=HOUSE, json={"mic": True}).status_code == 400            # neither node nor panel


def test_two_satellites_of_one_room_waking_together_are_heard_once(vo, monkeypatch):
    c = TestClient(core.app)
    add(c)                                                                              # kitchen, node kit-node
    c.put("/api/voice/satellites/tablet", headers=HOUSE, json={"panel": "tab9", "mic": True, "player": True, "room": "kitchen", "peer": "192.0.2.61", "wake_chime": True})
    monkeypatch.setattr(me, "_room_wake", {})
    clock = [1000.0]
    monkeypatch.setattr(me.time, "time", lambda: clock[0])
    vo.clear()
    me._on_node(["kit-node", "wake"], b"x", False)                                      # the first one is heard
    clock[0] += 0.8
    me._on_node(["panel-tab9", "wake"], b"wake", False)                                 # 0.8 s later: a duplicate, no sounds
    assert [t for t, _p in vo].count("kit-node/say/url") == 1 and not any(t.startswith("panel/") for t, _p in vo)
    assert me._stat("tablet")["dups"] == 1 and me._stat("tablet")["wakes"] == 1
    clock[0] += 5
    vo.clear()
    me._on_node(["panel-tab9", "wake"], b"wake", False)                                 # a new burst: the tablet is first now
    assert any(t.startswith("panel/") for t, _p in vo)


def test_a_phone_is_a_satellite_with_push_to_talk(vo, monkeypatch):
    c = TestClient(core.app)
    r = c.put("/api/voice/satellites/phone_guest", headers=HOUSE, json={"phone": True, "name": "Phone", "room": ""})
    assert r.status_code == 200 and r.json()["phone"] is True and r.json()["node"] == "phone-phone-guest"
    assert "phone_guest_mic" in core.DEVICES and "phone_guest_spk" not in core.DEVICES                 # one card, no player
    assert [x["code"] for x in core.DEVICES["phone_guest_mic"]["controls"]] == ["last_wake"]
    sent = []
    answers = iter([{"engine": "grammar", "ok": True, "said": "Done", "ask": False, "text": "light on"},
                    {"engine": "llm", "ok": True, "ask": True, "said": "Switch off?", "reply": "Switch off?", "token": "t1", "text": "all off"},
                    {"engine": "llm", "ok": False, "ask": False, "chat": True, "said": "Not understood.", "text": "weather"}])
    monkeypatch.setattr(me, "_vad_raw", lambda path, data, timeout=40.0: sent.append((path, len(data))) or dict(next(answers)))
    pcm = b"\x00\x01" * 8000
    r1 = c.post("/api/voice/ptt?sat=phone_guest", headers={**HOUSE, "Content-Type": "application/octet-stream"}, content=pcm)
    assert r1.status_code == 200 and r1.json()["audio"] == "http://192.0.2.7:8765/plugins/voice/tts/s/done.wav"             # a command: the done chime
    r2 = c.post("/api/voice/ptt?sat=phone_guest", headers=HOUSE, content=pcm)
    assert r2.json()["audio"] == "http://192.0.2.7:8765/plugins/voice/tts/a/x.wav" and r2.json()["token"] == "t1"            # a question is spoken
    r3 = c.post("/api/voice/ptt?sat=phone_guest", headers=HOUSE, content=pcm)
    assert r3.json()["audio"] == "" and r3.json()["chat"] is True                                          # a chat: no sound yet
    assert sent == [("/utter", len(pcm))] * 3
    assert values("phone_guest_mic")["last_wake"] and me._stat("phone_guest")["wakes"] == 3
    assert c.post("/api/voice/ptt?sat=nobody", headers=HOUSE, content=pcm).status_code == 404
    assert c.post("/api/voice/ptt?sat=phone_guest", headers=HOUSE, content=b"x" * 700001).status_code == 413
    assert vo == []                                                                                         # nothing was sent to a node


def test_the_speech_of_a_text_for_a_phone(vo):
    c = TestClient(core.app)
    r = c.post("/api/voice/tts", headers=HOUSE, json={"text": "Hello"})
    assert r.status_code == 200 and r.json()["url"] == "http://192.0.2.7:8765/plugins/voice/tts/a/x.wav"


def test_the_first_talk_of_a_phone_makes_its_satellite(vo, monkeypatch):
    c = TestClient(core.app)
    monkeypatch.setattr(me, "_vad_raw", lambda path, data, timeout=40.0: {"engine": "grammar", "ok": True, "said": "Done", "ask": False, "text": "x"})
    assert "phone_anna_mic" not in core.DEVICES
    assert c.post("/api/voice/ptt?sat=phone_anna", headers=HOUSE, content=b"\x00\x01" * 100).status_code == 200
    assert "phone_anna_mic" in core.DEVICES and me._sat("phone_anna")["phone"] is True
    assert c.post("/api/voice/ptt?sat=Bad Name", headers=HOUSE, content=b"\x00\x01").status_code in (404, 422)       # only phone_<name> is made
    c.delete("/api/voice/satellites/phone_anna", headers=HOUSE)


def test_a_node_that_cannot_play_while_it_streams_gets_no_chimes(vo):
    c = TestClient(core.app)
    add(c, chime=False)
    me._on_node(["kit-node", "wake"], b"x", False)
    heard = lambda **m: me.on_heard(["voice", "heard"], json.dumps(m).encode(), False)
    heard(ip="192.0.2.50", ok=True, sound="thinking")
    assert vo == []                                                                      # neither the listening nor the thinking sound
    heard(ip="192.0.2.50", ok=True)                                                      # the result chime after the stream stays
    assert vo == [("kit-node/say/url", "http://192.0.2.7:8765/plugins/voice/tts/s/done.wav")]


def test_a_stop_goes_out_on_a_wake_only_when_something_plays(vo):
    c = TestClient(core.app)
    add(c)
    me._stat("kitchen")["playing"] = True
    me._on_node(["kit-node", "wake"], b"x", False)
    assert vo == [("kit-node/stop/command", "PRESS")]                                    # it plays: stopped, and no chime over it
    me._stat("kitchen")["playing"] = False


def test_a_question_must_be_played_then_the_mic_opens_once(vo, monkeypatch):
    c = TestClient(core.app)
    add(c, listen_once=True)
    timers = []
    monkeypatch.setattr(me.threading, "Timer", lambda t, fn, args: timers.append((t, fn, args)) or type("T", (), {"daemon": False, "start": lambda self: None})())
    me.on_heard(["voice", "heard"], json.dumps({"ip": "192.0.2.50", "ok": True, "reply": "Switch all off?", "ask": True}).encode(), False)
    assert vo[-1] == ("kit-node/say/url", "http://192.0.2.7:8765/plugins/voice/tts/a/x.wav") and timers[0][0] == me.QUESTION_PLAY_S
    vo.clear()
    me._on_node(["kit-node", "player", "state"], b"playing", False)
    me._on_node(["kit-node", "player", "state"], b"idle", False)                         # the question ended: the mic opens for the answer
    assert vo == [("kit-node/listen_once/command", str(me.LISTEN_ONCE_S))]
    vo.clear()
    me._question_check("kitchen", "192.0.2.50")                                          # it WAS played: nothing is cancelled
    assert vo == []


def test_a_question_the_node_never_played_is_cancelled(vo):
    c = TestClient(core.app)
    add(c, listen_once=True)
    me._track_question(me._sat("kitchen"), "192.0.2.50")
    me._question_check("kitchen", "192.0.2.50")                                          # no "playing" came: voice/ask_failed tells the recognizer
    assert vo == [("voice/ask_failed", json.dumps({"ip": "192.0.2.50"}))]
    vo.clear()
    me._on_node(["kit-node", "player", "state"], b"idle", False)                        # a late idle does not open the mic
    assert vo == []


def _states(*texts):
    for t in texts:
        me._on_node(["kit-node", "player", "state"], t.encode(), False)


def test_the_house_never_cuts_its_own_reply_the_19_06_sequence(vo):
    """The live sequence: a wake chime, "thinking" 0.9 s later, the answer, "thinking" again, the next answer: only ONE sound at a time, in order."""
    c = TestClient(core.app)
    add(c)
    heard = lambda **m: me.on_heard(["voice", "heard"], json.dumps(m).encode(), False)
    chime, answer = "http://192.0.2.7:8765/plugins/voice/tts/s/listening.wav", "http://192.0.2.7:8765/plugins/voice/tts/a/x.wav"
    me._on_node(["kit-node", "wake"], b"x", False)
    assert vo == [("kit-node/say/url", chime)]                                        # the wake chime goes out
    heard(ip="192.0.2.50", ok=True, sound="thinking")                                 # 0.9 s later: the chime still plays - "thinking" is dropped
    assert vo == [("kit-node/say/url", chime)]
    heard(ip="192.0.2.50", ok=True, reply="Switch it off?", ask=True)                 # the answer waits behind the chime, it does not cut it
    assert vo == [("kit-node/say/url", chime)]
    _states("playing", "idle")                                                        # the chime ended: the answer goes out
    assert vo[-1] == ("kit-node/say/url", answer) and len(vo) == 2
    heard(ip="192.0.2.50", ok=True, sound="thinking")                                 # the next phrase's "thinking" while the answer plays: dropped
    assert len(vo) == 2
    heard(ip="192.0.2.50", ok=True, reply="Done again")                               # its answer queues, the running reply is not cut
    assert len(vo) == 2
    _states("playing", "idle")                                                        # the first answer ended: the second goes out
    assert len(vo) == 3 and vo[-1] == ("kit-node/say/url", answer)
    _states("playing", "idle")                                                        # the queue is empty: nothing more, the node is free
    assert len(vo) == 3 and not me._stat("kitchen")["sound_active"]
    heard(ip="192.0.2.50", ok=True, sound="thinking")                                 # free again: a "thinking" plays
    assert vo[-1] == ("kit-node/say/url", "http://192.0.2.7:8765/plugins/voice/tts/s/thinking.wav")


def test_a_result_chime_waits_behind_a_reply_and_a_real_stop_clears_everything(vo):
    c = TestClient(core.app)
    add(c)
    heard = lambda **m: me.on_heard(["voice", "heard"], json.dumps(m).encode(), False)
    heard(ip="192.0.2.50", ok=True, reply="Light is on")
    heard(ip="192.0.2.50", ok=True)                                                   # the done chime queues
    heard(ip="192.0.2.50", ok=False)                                                  # and the error chime too
    assert len(vo) == 1 and len(me._stat("kitchen")["queue"]) == 2
    vo.clear()
    me.stop(me._sat("kitchen"))                                                       # a real stop: cut and forget
    assert vo == [("kit-node/stop/command", "PRESS")] and me._stat("kitchen")["queue"] == []
    me._on_node(["kit-node", "wake"], b"x", False)                                    # a new wake after a stop: the chime goes out at once
    assert vo[-1] == ("kit-node/say/url", "http://192.0.2.7:8765/plugins/voice/tts/s/listening.wav")


def test_a_node_that_never_reports_does_not_stall_the_queue(vo, monkeypatch):
    c = TestClient(core.app)
    add(c)
    me.on_heard(["voice", "heard"], json.dumps({"ip": "192.0.2.50", "ok": True, "reply": "One"}).encode(), False)
    st = me._stat("kitchen")
    st["sent_at"] -= me.SOUND_MAX_S + 1                                               # no player/state ever came back
    me.on_heard(["voice", "heard"], json.dumps({"ip": "192.0.2.50", "ok": True, "reply": "Two"}).encode(), False)
    assert len(vo) == 2                                                               # the second is sent, not queued for ever


def test_an_api_play_cuts_and_replaces(vo):
    c = TestClient(core.app)
    add(c)
    me.on_heard(["voice", "heard"], json.dumps({"ip": "192.0.2.50", "ok": True, "reply": "Reply"}).encode(), False)
    me.on_heard(["voice", "heard"], json.dumps({"ip": "192.0.2.50", "ok": True}).encode(), False)
    vo.clear()
    r = c.post("/api/voice/say", headers=HOUSE, json={"text": "Hello", "target": "kitchen"})                  # explicit: it plays at once
    assert r.status_code == 200 and len(vo) == 1 and me._stat("kitchen")["queue"] == []


def test_a_node_that_talks_is_online_even_without_a_status_line(vo):
    c = TestClient(core.app)
    add(c)
    me._on_node(["kit-node", "status"], b"offline", True)                                 # the retained state at a restart: offline
    assert core.status_copy("kitchen_spk", {}).get("online") is False
    me._on_node(["kit-node", "player", "state"], b"idle", False)                          # but the node is talking: it is on
    assert core.status_copy("kitchen_spk", {}).get("online") is True and core.status_copy("kitchen_mic", {}).get("online") is True
    me._on_node(["kit-node", "status"], b"offline", False)                                # its last will: offline again
    assert core.status_copy("kitchen_spk", {}).get("online") is False


def test_the_phone_card_is_on_while_the_app_reports(vo):
    c = TestClient(core.app)
    assert c.put("/api/voice/satellites/phone_anna", headers=HOUSE, json={"phone": True, "name": "Anna"}).status_code == 200
    assert core.status_copy("phone_anna_mic", {}).get("online") is not True                    # nothing heard from the app yet
    me.on_phone_seen(["voice", "phone", "anna", "seen"], b"1", False)                          # the family plug-in: the app reported
    assert core.status_copy("phone_anna_mic", {}).get("online") is True
    me._phone_check(time.time() + me.PHONE_SILENT_S - 5)                                       # silent for a while: still on
    assert core.status_copy("phone_anna_mic", {}).get("online") is True
    me._phone_check(time.time() + me.PHONE_SILENT_S + 5)                                       # silent too long: off
    assert core.status_copy("phone_anna_mic", {}).get("online") is False
    me.on_phone_seen(["voice", "phone", "anna", "seen"], b"1", True)                           # a retained replay is not a sign of life
    me.on_phone_seen(["voice", "phone", "nobody", "seen"], b"1", False)                        # not a phone satellite: ignored
    assert core.status_copy("phone_anna_mic", {}).get("online") is False
    c.delete("/api/voice/satellites/phone_anna", headers=HOUSE)


def test_a_retained_online_after_a_restart_brings_the_cards_back(vo):
    c = TestClient(core.app)
    add(c)
    me._on_node(["kit-node", "status"], b"offline", False)                                # the node was off when the house stopped
    assert core.status_copy("kitchen_spk", {}).get("online") is False
    me._on_node(["kit-node", "status"], b"online", True)                                  # a restarted house reads the retained "online"
    assert core.status_copy("kitchen_spk", {}).get("online") is True
    assert core.status_copy("kitchen_mic", {}).get("online") is True


def test_the_lead_in_noise_goes_only_to_a_satellite_that_asks_for_it(vo, monkeypatch):
    monkeypatch.setattr(me, "public_base", lambda: "http://h")
    assert me.sound_url("done", {"lead_in": True}).endswith("/s/done_lead.wav")
    assert me.sound_url("done", {}).endswith("/s/done.wav") and me.sound_url("done").endswith("/s/done.wav")
    got = []
    monkeypatch.setattr(me, "say_url", lambda text, lang=None, voice=None, timeout=20.0, lead_in=False: got.append(lead_in) or {"url": "u"})
    monkeypatch.setattr(me, "_enqueue", lambda *a, **k: None)
    me.speak({"id": "x", "lead_in": True}, "hi", queued=True)
    me.speak({"id": "y"}, "hi", queued=True)
    assert got == [True, False]


def test_numbers_are_spoken_as_words(tts):
    n = tts.numwords.normalize
    assert n("\u0412 \u0441\u043f\u0430\u043b\u044c\u043d\u0435 27 \u0433\u0440\u0430\u0434\u0443\u0441\u043e\u0432", "ru") == "\u0412 \u0441\u043f\u0430\u043b\u044c\u043d\u0435 \u0434\u0432\u0430\u0434\u0446\u0430\u0442\u044c \u0441\u0435\u043c\u044c \u0433\u0440\u0430\u0434\u0443\u0441\u043e\u0432"  # i18n
    assert n("\u0432\u043b\u0430\u0436\u043d\u043e\u0441\u0442\u044c 51%, 24\u00b0", "ru") == "\u0432\u043b\u0430\u0436\u043d\u043e\u0441\u0442\u044c \u043f\u044f\u0442\u044c\u0434\u0435\u0441\u044f\u0442 \u043e\u0434\u0438\u043d \u043f\u0440\u043e\u0446\u0435\u043d\u0442, \u0434\u0432\u0430\u0434\u0446\u0430\u0442\u044c \u0447\u0435\u0442\u044b\u0440\u0435 \u0433\u0440\u0430\u0434\u0443\u0441\u0430"  # i18n
    assert n("27,5 \u0438 -5", "ru") == "\u0434\u0432\u0430\u0434\u0446\u0430\u0442\u044c \u0441\u0435\u043c\u044c \u0438 \u043f\u044f\u0442\u044c \u0438 \u043c\u0438\u043d\u0443\u0441 \u043f\u044f\u0442\u044c"  # i18n
    assert [n(x, "ru") for x in ("113", "1001", "2000", "12")] == ["\u0441\u0442\u043e \u0442\u0440\u0438\u043d\u0430\u0434\u0446\u0430\u0442\u044c", "\u043e\u0434\u043d\u0430 \u0442\u044b\u0441\u044f\u0447\u0430 \u043e\u0434\u0438\u043d", "\u0434\u0432\u0435 \u0442\u044b\u0441\u044f\u0447\u0438", "\u0434\u0432\u0435\u043d\u0430\u0434\u0446\u0430\u0442\u044c"]  # i18n
    assert n("It is 21.5% and 1000", "en") == "It is twenty one point five percent and one thousand"


def test_silero_speech_is_converted_to_the_house_format(tts, monkeypatch):
    """The model gets <speaker>, 48 kHz and the text with numbers in words; the answer is 16 kHz stereo, the lead-in first when asked."""
    seen = {}

    class Fake:
        def apply_tts(self, text, speaker, sample_rate, **kw):
            seen.update(text=text, speaker=speaker, rate=sample_rate, kw=kw)

            class A:
                def tolist(self):
                    return [0.5] * 48000                                                              # one second
            return A()
    monkeypatch.setattr(tts.VOICE_SET, "model", lambda lang: Fake())
    wav = tts.VOICE_SET.say("\u0421\u0435\u0439\u0447\u0430\u0441 24 \u0433\u0440\u0430\u0434\u0443\u0441\u0430", "ru-xenia")  # i18n
    assert seen["text"] == "\u0421\u0435\u0439\u0447\u0430\u0441 \u0434\u0432\u0430\u0434\u0446\u0430\u0442\u044c \u0447\u0435\u0442\u044b\u0440\u0435 \u0433\u0440\u0430\u0434\u0443\u0441\u0430" and seen["speaker"] == "xenia" and seen["rate"] == 48000 and seen["kw"]["put_accent"]  # i18n
    assert abs(wav_info(wav)[3] - 16000) <= 2 and wav_info(wav)[:3] == (16000, 2, 2)
    lead = tts.VOICE_SET.say("\u041f\u0440\u0438\u0432\u0435\u0442", "ru-xenia", lead_in=True)  # i18n
    assert wav_info(lead)[3] == wav_info(wav)[3] + 16000 * tts.LEAD_IN_MS // 1000
    tts.VOICE_SET.say("Hello 5", "en-en_99")
    assert seen["text"] == "Hello five" and seen["speaker"] == "en_99" and seen["kw"] == {}


def test_background_recording_sessions(vo, monkeypatch):
    """A quiet session starts at once; a TV session waits for the TV, replaces the quiet one while the TV is on, ends when it goes off."""
    c = TestClient(core.app)
    rec = sys.modules["yashome_plugins.voice._record"]
    assert c.put("/api/voice/satellites/rs", headers=HOUSE, json={"node": "rs-node", "mic": True, "player": True, "peer": "192.0.2.51",
                                                                  "listen_once": True, "name": "RS"}).status_code == 200
    for k in [k for k, v in core.DEVICES.items() if v.get("category") == "tv"]:
        monkeypatch.delitem(core.DEVICES, k)
    assert c.post("/api/voice/background", headers=HOUSE, json={"kind": "tv", "sat": "rs"}).status_code == 400        # no TV: nothing to watch
    monkeypatch.setitem(core.DEVICES, "tv_1", {"id": "tv_1", "name": "TV", "category": "tv"})   # the house's only TV: that one
    calls, vad_state, tv = [], {"state": "recording", "label": "quiet"}, {"on": False}

    def fake_vad(path, body, timeout=25.0):
        calls.append((path, body["action"], body.get("label")))
        if body["action"] == "stop":
            return {"state": "stopped", "files": 3, "seconds": 120.0, "dir": "_background/d/" + str(vad_state["label"])}
        if body["action"] == "start":
            vad_state.update(state="recording", label=body["label"])
        return dict(vad_state)
    monkeypatch.setattr(rec, "_vad_post", fake_vad)
    monkeypatch.setattr(rec, "_tv_on", lambda dev: dev == "tv_1" and tv["on"])
    rec.SESS.clear()
    assert c.post("/api/voice/background", headers=HOUSE, json={"kind": "quiet", "sat": "rs", "minutes": 30}).status_code == 200
    assert calls[-1] == ("/record/bg", "start", "quiet") and rec.SESS["quiet"]["state"] == "recording"
    assert c.post("/api/voice/background", headers=HOUSE, json={"kind": "tv", "sat": "rs"}).status_code == 200
    rec.tick()
    assert rec.SESS["tv"]["state"] == "waiting" and rec.SESS["quiet"]["state"] == "recording"       # the TV is off: it waits
    assert "rs_mic" in core.DEVICES and core.status_copy("rs_mic", {})["values"]["bg_record"].startswith("\u0437\u0430\u043f\u0438\u0441\u044c")  # i18n
    tv["on"] = True
    rec.tick()
    assert rec.SESS["tv"]["state"] == "recording" and rec.SESS["quiet"]["state"] == "replaced"
    tv["on"] = False
    rec.tick()
    assert rec.SESS["tv"]["state"] == "done" and rec.SESS["tv"]["summary"]["files"] == 3
    tv["on"] = True
    rec.tick()
    assert rec.SESS["tv"]["state"] == "done"                                                          # one time: not every evening
    assert c.post("/api/voice/background", headers=HOUSE, json={"kind": "quiet", "sat": "nope"}).status_code == 404
    assert c.post("/api/voice/background", json={"kind": "quiet", "sat": "rs"}).status_code in (401, 403)
    assert c.delete("/api/voice/background", headers=HOUSE).status_code == 200
    c.delete("/api/voice/satellites/rs", headers=HOUSE)


def test_sample_recording_calls(vo, monkeypatch):
    """The page's calls reach voice-vad as they are: arm + beep + listen_once on a satellite, a browser's or phone's recording saved whole."""
    c = TestClient(core.app)
    rec = sys.modules["yashome_plugins.voice._record"]
    assert c.put("/api/voice/satellites/rs", headers=HOUSE, json={"node": "rs-node", "mic": True, "player": True, "peer": "192.0.2.51",
                                                                  "listen_once": True, "name": "RS"}).status_code == 200
    asked, sent = [], []
    monkeypatch.setattr(rec, "_vad_post", lambda path, body, timeout=25.0: asked.append((path, body)) or {"id": "r1", "deleted": "x.wav"})
    monkeypatch.setattr(rec, "_vad_get", lambda path, timeout=10.0, raw=False: (b"RIFFx" if raw else
                        {"state": "saved", "file": "a/positive/1.wav", "secs": 2.5, "peak": 9000, "count": 5} if path.startswith("/record/status")
                        else {"anna": {"name": "Anna", "positive": 5, "negative": 2}} if path == "/record/list" else {"phrases": ["a", "b"]}))
    monkeypatch.setattr(rec, "_vad_raw", lambda path, data, timeout=40.0: asked.append((path, len(data))) or {"file": "u/negative/2.wav", "count": 1})
    monkeypatch.setattr(rec, "_send", lambda s_, suffix, payload: sent.append((suffix, payload)))
    monkeypatch.setattr(rec, "play", lambda *a, **k: None)
    monkeypatch.setattr(rec, "public_base", lambda: "")
    st = c.get("/api/voice/record/state", headers=HOUSE).json()
    assert st["devices"] == [{"id": "rs", "name": "RS"}] and st["speakers"]["anna"]["positive"] == 5 and st["target"] == 100
    r = c.post("/api/voice/record", headers=HOUSE, json={"speaker": "anna", "label": "positive", "device": "rs"}).json()
    assert r["state"] == "saved" and asked[0][0] == "/record/arm" and asked[0][1]["ip"] == "192.0.2.51" and sent == [("listen_once/command", "5")]
    assert c.post("/api/voice/record", headers=HOUSE, json={"speaker": "anna", "label": "other", "device": "rs"}).status_code == 422
    assert c.post("/api/voice/record", headers=HOUSE, json={"speaker": "anna", "label": "positive", "device": "nobody"}).status_code == 404
    ok = c.post("/api/voice/record/sample?speaker=anna&label=negative&sat=browser", headers=HOUSE, content=b"\x00\x01" * 40000)
    assert ok.status_code == 200 and asked[-1][0].startswith("/record/save?") and "label=negative" in asked[-1][0] and asked[-1][1] == 80000
    assert c.post("/api/voice/record/sample?speaker=anna&label=negative", headers=HOUSE, content=b"\x00" * 10).status_code == 413
    assert c.post("/api/voice/record/sample?speaker=anna&label=x", headers=HOUSE, content=b"\x00" * 20000).status_code == 400
    assert c.get("/api/voice/record/phrases?n=2", headers=HOUSE).json() == {"phrases": ["a", "b"]}
    assert c.get("/api/voice/record/file?f=a/positive/1.wav", headers=HOUSE).content == b"RIFFx"
    assert c.post("/api/voice/record/delete", headers=HOUSE, json={"speaker": "anna", "label": "positive"}).json()["deleted"] == "x.wav"
    assert c.get("/api/voice/record/file?f=_background/d/quiet/x.wav", headers=HOUSE).status_code == 404                  # a room recording is not a sample
    assert c.post("/api/voice/record/delete", headers=HOUSE, json={"file": "a/../_background/x.wav"}).status_code == 404
    rec.SESS["quiet"] = {"state": "recording", "sat": "rs", "since": time.time(), "until": time.time() + 99, "summary": {}}
    assert c.post("/api/voice/record", headers=HOUSE, json={"speaker": "anna", "label": "positive", "device": "rs"}).status_code == 409   # a background session runs
    rec.SESS.clear()
    c.delete("/api/voice/satellites/rs", headers=HOUSE)


def test_background_sessions_survive_a_restart(vo, monkeypatch, tmp_path):
    rec = sys.modules["yashome_plugins.voice._record"]
    monkeypatch.setattr(rec, "_file", lambda: tmp_path / "background.json")
    rec.SESS.clear()
    rec.SESS["tv"] = {"state": "waiting", "sat": "rs", "since": 1.0, "until": 0, "summary": {}}
    rec._persist()
    rec.SESS.clear()
    rec._restore()
    assert rec.SESS["tv"]["state"] == "waiting"
    rec.SESS.clear()


def test_recording_is_offered_only_when_the_recognizer_service_answers(vo, monkeypatch):
    c = TestClient(core.app)
    rec = sys.modules["yashome_plugins.voice._record"]
    rec._avail.update(t=0.0, ok=False)
    def down(path, timeout=10.0, raw=False):
        raise core.HTTPException(503, "not reachable")
    monkeypatch.setattr(rec, "_vad_get", down)
    assert c.get("/api/voice/record/available", headers=HOUSE).json() == {"available": False}
    monkeypatch.setattr(rec, "_vad_get", lambda path, timeout=10.0, raw=False: {})
    assert c.get("/api/voice/record/available", headers=HOUSE).json() == {"available": False}            # the answer is kept for a minute
    rec._avail["t"] = 0.0
    assert c.get("/api/voice/record/available", headers=HOUSE).json() == {"available": True}


def test_the_sessions_file_is_written_only_when_it_changes(vo, monkeypatch, tmp_path):
    rec = sys.modules["yashome_plugins.voice._record"]
    monkeypatch.setattr(rec, "_file", lambda: tmp_path / "background.json")
    writes = []
    monkeypatch.setattr(rec.core, "atomic_json_dump", lambda path, data, **kw: writes.append(1))
    rec.SESS.clear()
    rec._saved.clear()
    rec.SESS["quiet"] = {"state": "waiting", "sat": "rs", "since": 1.0, "until": 0, "summary": {}}
    rec._persist()
    rec._persist()
    assert len(writes) == 1                                                  # the 3 s watch does not rewrite an unchanged file
    rec.SESS["quiet"]["state"] = "done"
    rec._persist()
    assert len(writes) == 2


def test_no_sound_on_a_wake_by_default_and_an_understood_command_has_one_beep_and_no_second_chime(vo):
    c = TestClient(core.app)
    add(c, wake_chime=False)
    me._on_node(["kit-node", "wake"], b"x", False)
    assert vo == [] and me._stat("kitchen")["wakes"] == 1                                 # the node's wake word is followed by the command at once: no beep
    heard = lambda **m: me.on_heard(["voice", "heard"], json.dumps(m).encode(), False)     # noqa: E731
    heard(ip="192.0.2.50", ok=True, sound="done")                                          # understood: the confirm beep at once
    assert vo == [("kit-node/say/url", "http://192.0.2.7:8765/plugins/voice/tts/s/done.wav")]
    vo.clear()
    me._stat("kitchen")["sound_active"] = False
    heard(ip="192.0.2.50", ok=True, quiet=True)                                            # the command is done and nothing went wrong: silence (the beep was the answer)
    assert vo == [] and me._stat("kitchen")["commands"] == 1
    heard(ip="192.0.2.50", ok=False, reply="I did not understand.")                      # not understood: spoken, no beep
    assert vo and "s/error.wav" not in vo[-1][1] and "s/done.wav" not in vo[-1][1]


# ---- Settings -> Voice: what voice-vad runs with (GET/PUT /api/voice/config), and the house language ----
@pytest.fixture
def conf(vo, monkeypatch):
    monkeypatch.setitem(me._cfg, "config", {})
    monkeypatch.setitem(core._house, "lang", "ru")
    yield TestClient(core.app)


def test_the_first_read_of_the_voice_service_seeds_the_settings_once(conf):
    c = conf
    add(c)
    add(c, "zte", node="zte-node", peer="192.0.2.98")
    assert c.get("/api/voice/config").status_code in (401, 403)
    r = c.get("/api/voice/config", headers=HOUSE).json()
    assert r == {"lang": "ru", "need_wake": []}                                        # nothing set: voice-vad keeps its own values
    seed = {"wake": "джарвис", "wake_maxdist": 3, "wake_prefix": "джарв", "llm": True, "llm_model": "qwen3:4b", "big_model": "qwen3:14b",   # i18n
            "llm_timeout": 5.0, "confirm_all_off": True, "ask_ttl": 12.0, "need_wake_peers": ["192.0.2.98"]}
    r = c.put("/api/voice/config?seed=1", headers=HOUSE, json=seed).json()
    assert r["seeded"] is True and r["wake"] == "джарвис" and r["need_wake"] == ["192.0.2.98"] and "need_wake_peers" not in r   # i18n
    assert [s["id"] for s in me._sats() if s.get("need_wake")] == ["zte"]
    assert c.put("/api/voice/config?seed=1", headers=HOUSE, json={**seed, "wake": "x" * 5}).json()["wake"] == "джарвис"   # once only   # i18n
    r = c.put("/api/voice/config", headers=HOUSE, json={"wake": "домовой", "llm": False}).json()   # i18n
    assert (r["wake"], r["llm"], r["ask_ttl"]) == ("домовой", False, 12.0)               # a field left out stays   # i18n


SEED = {"wake": "джарвис", "wake_maxdist": 3, "wake_prefix": "джарв", "wake_variants": ["компьютер", "компьют*"], "llm": True, "need_wake_peers": []}   # i18n


def test_the_voice_settings_are_checked(conf):
    assert conf.put("/api/voice/config", headers=HOUSE, json={"wake_prefix": ""}).status_code == 409          # before the seed: the page waits
    conf.put("/api/voice/config?seed=1", headers=HOUSE, json=SEED)
    for bad in ({"wake": "два слова"}, {"wake": "x1"}, {"wake_maxdist": 4}, {"llm_timeout": 0}, {"ask_ttl": 500}, {"llm_model": "a b"},   # i18n
                {"wake_prefix": "дж"}):   # i18n
        assert conf.put("/api/voice/config", headers=HOUSE, json=bad).status_code == 422, bad
    for bad in ({"wake": "крис"}, {"wake_variants": ["эй компьютер"]}, {"wake_variants": ["б*"]}, {"wake_variants": ["дж*"]}):           # "крис" with how exact 3: at most 1   # i18n
        assert conf.put("/api/voice/config", headers=HOUSE, json=bad).status_code == 400, bad
    r = conf.put("/api/voice/config", headers=HOUSE, json={"wake": "крис", "wake_maxdist": 1, "wake_prefix": "", "wake_variants": [" Компьют* ", "компьют*", "jarvis"]}).json()   # i18n
    assert (r["wake"], r["wake_maxdist"], r["wake_prefix"], r["wake_variants"]) == ("крис", 1, "", ["компьют*", "jarvis"])   # i18n


def test_a_seed_beyond_the_bounds_is_fitted_not_refused(conf):
    r = conf.put("/api/voice/config?seed=1", headers=HOUSE, json={**SEED, "wake": "крис"}).json()   # i18n
    assert (r["seeded"], r["wake_maxdist"]) == (True, 1) and "need_wake_env" not in r


def test_need_wake_is_kept_by_a_save_without_it_and_follows_the_env_list_for_a_new_satellite(conf):
    add(conf, need_wake=True)
    nw = lambda: conf.get("/api/voice/config", headers=HOUSE).json()["need_wake"]         # noqa: E731
    assert nw() == ["192.0.2.50"]
    add(conf)                                                                             # an old form or an API client: the field left out
    assert nw() == ["192.0.2.50"]
    add(conf, need_wake=False)
    assert nw() == []
    conf.put("/api/voice/config?seed=1", headers=HOUSE, json={**SEED, "need_wake_peers": ["192.0.2.98"]})
    add(conf, "zte", node="zte-node", peer="192.0.2.98")                                  # re-added at the address the env named
    add(conf, "other", node="other-node", peer="192.0.2.99")
    assert nw() == ["192.0.2.98"]


def test_a_good_config_poll_is_left_out_of_the_access_log():
    f = me._QuietConfigPoll()
    rec = lambda *a: logging.LogRecord("uvicorn.access", 20, "", 0, '%s - "%s %s HTTP/%s" %d', a, None)    # noqa: E731
    assert not f.filter(rec("1.2.3.4:5", "GET", "/api/voice/config", "1.1", 200))
    assert f.filter(rec("1.2.3.4:5", "GET", "/api/voice/config", "1.1", 401))
    assert f.filter(rec("1.2.3.4:5", "PUT", "/api/voice/config", "1.1", 200))


def test_the_house_language_leads_the_voice_the_clocks_and_the_dashboard(conf, monkeypatch):
    c = conf
    monkeypatch.setattr(core, "ROOT", core.Path(core.__file__).parent)                  # the checkout serves i18n.js, not the test house
    assert c.get("/api/house", headers=HOUSE).json() == {"lang": "ru", "langs": core.house_langs()}
    assert "en" in core.house_langs() and "ru" in core.house_langs()
    assert c.put("/api/house", json={"lang": "en"}).status_code in (401, 403)
    assert c.put("/api/house", headers=HOUSE, json={"lang": "xx"}).status_code == 400
    assert c.put("/api/house", headers=HOUSE, json={"lang": "en"}).json()["lang"] == "en"
    assert c.get("/api/voice/config", headers=HOUSE).json()["lang"] == "en"
    assert c.get("/i18n.js").text.startswith('window.HOUSE_LANG = "en";\n')
    t = time.struct_time((2026, 7, 27, 9, 5, 0, 0, 208, 0))
    assert core.clock_text(t) == ("09:05", "Mon 27 July")
    monkeypatch.setitem(core._house, "lang", "ru")
    assert core.clock_text(t)[1] != "Mon 27 July" and c.get("/i18n.js").text.startswith('window.HOUSE_LANG = "ru";')
    assert core._clean_house({"lang": "zz"})["lang"] == "en" and core._clean_house({})["lang"] == "en"


def test_the_models_come_from_the_voice_service(conf, monkeypatch):
    monkeypatch.setattr(me, "_vad_post", lambda path, body, timeout=25.0: {"path": path, "small": ["a"], "big": []})
    assert conf.post("/api/voice/models", headers=HOUSE).json() == {"path": "/models", "small": ["a"], "big": []}


def test_a_seed_is_never_refused_a_field_that_does_not_fit_is_dropped(conf):
    bad = {**SEED, "wake": "джарвис\u00b2", "llm_timeout": 1e9, "ask_ttl": "x", "wake_variants": ["компьют*", "дж*", "эй компьютер", "jarvis"], "bogus": 1}   # i18n
    r = conf.put("/api/voice/config?seed=1", headers=HOUSE, json=bad)
    assert r.status_code == 200
    c = r.json()
    assert c["seeded"] is True and "wake" not in c and "llm_timeout" not in c and "ask_ttl" not in c and "bogus" not in c
    assert c["wake_variants"] == ["компьют*", "jarvis"] and c["wake_prefix"] == "джарв"   # i18n
    assert me.CONFIG_KEYS >= {"wake", "seeded", "need_wake_env"} and "big_timeout" not in me.CONFIG_KEYS


def test_one_letter_class_and_a_seed_without_variants_keeps_none_stored(conf):
    r = conf.put("/api/voice/config?seed=1", headers=HOUSE, json={k: v for k, v in SEED.items() if k != "wake_variants"}).json()
    assert "wake_variants" not in r                                                         # voice-vad keeps its own list
    assert conf.put("/api/voice/config", headers=HOUSE, json={"wake_variants": ["caf\u00e9"]}).status_code == 400   # a letter voice-vad does not match
    assert conf.put("/api/voice/config", headers=HOUSE, json={"wake_variants": ["Jarvis", "\u0451\u0436\u0438\u043a*"]}).json()["wake_variants"] == ["jarvis", "\u0451\u0436\u0438\u043a*"]   # i18n


def test_a_satellite_name_is_unique_among_satellites(vo):
    c = TestClient(core.app)
    assert add(c, name="Kitchen").status_code == 200
    assert add(c, "hall", node="hall-node", peer="192.0.2.51", name=" kitchen ").status_code == 409
    assert add(c, "hall", node="hall-node", peer="192.0.2.51", name="x" * 41).status_code in (400, 422)


def test_a_tts_service_on_another_computer_is_the_one_in_use(monkeypatch):
    import io as _io
    import tarfile as _tar
    monkeypatch.setattr(me, "_cfg", {"satellites": []})
    monkeypatch.setattr(core, "_save_settings", lambda: None)
    monkeypatch.setenv("BIND_ADDRESS", "192.0.2.10")
    monkeypatch.setenv("VOICE_TTS_KEY", "house-key")
    pub = "http://192.0.2.10:8765/plugins/voice/tts"                                # the dashboard's port hands the files on
    assert (me._url(), me.public_base(), me._tts_key(), me.tts_in_use()) == ("http://tts:8092", pub, "house-key", "")
    from fastapi.testclient import TestClient as _TC
    c = _TC(core.app)
    got = []

    class R:
        def __init__(self, url):
            got.append(url)

        def read(self):
            return b"RIFF"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    monkeypatch.setattr(me.urllib.request, "urlopen", lambda url, timeout=10: R(url))
    assert c.get("/plugins/voice/tts/s/done.wav").status_code == 200 and got == ["http://tts:8092/s/done.wav"]    # the house's own TTS
    me.use_tts("192.0.2.61", "http://helper:8092/")
    assert (me._url(), me.public_base(), me._tts_key(), me.tts_in_use()) == ("http://helper:8092", pub, "", "192.0.2.61")   # the tunnel, no key
    r = c.get("/plugins/voice/tts/a/Ab_9-x.wav")                                # open: the satellites fetch it with no token
    assert r.status_code == 200 and r.content == b"RIFF" and r.headers["content-type"] == "audio/wav" and got[-1] == "http://helper:8092/a/Ab_9-x.wav"
    assert c.get("/plugins/voice/tts/x/abc.wav").status_code == 404 and c.get("/plugins/voice/tts/s/a.b.wav").status_code == 404
    me.use_tts("")
    assert me._url() == "http://tts:8092" and "tts" not in me._cfg
    tar = _tar.open(fileobj=_io.BytesIO(c.get("/plugins/voice/tts.tar.gz").content))
    assert sorted(tar.getnames()) == ["Dockerfile", "numwords.py", "server.py"]
    assert b"COPY server.py numwords.py ./" in tar.extractfile("Dockerfile").read()
