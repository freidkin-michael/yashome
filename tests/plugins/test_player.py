"""player: host/ctl.py drives mpv through its IPC socket (a fake mpv here); the plug-in sends the commands through the helper
(a fake), turns the TV on and onto its input before a film; computers installs the role, with no Use."""
import importlib.util
import io
import json
import os
import shutil
import socket
import sys
import tarfile
import tempfile
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as core

REPO = Path(__file__).resolve().parents[2]
me = sys.modules["yashome_plugins.player"]
pcs = sys.modules["yashome_plugins.computers"]
HOUSE = {"Authorization": "Bearer test-token-not-real"}
_spec = importlib.util.spec_from_file_location("player_ctl", REPO / "plugins/tools/player/host/ctl.py")
ctl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ctl)


class FakeMpv:
    """mpv's IPC as far as ctl.py uses it: properties, loadfile, seek, cycle, add, stop; an event before every answer."""

    def __init__(self, path):
        self.props = {"idle-active": True, "volume": 100.0, "mute": False, "pause": False, "start": "none"}
        self.log = []
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(path)
        self.srv.listen(4)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.client, args=(c,), daemon=True).start()

    def client(self, c):
        buf = b""
        while True:
            chunk = c.recv(4096)
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                req = json.loads(line)
                cmd = req["command"]
                self.log.append(cmd)
                data, err = self.do(cmd)
                c.sendall(json.dumps({"event": "property-change"}).encode() + b"\n"
                          + json.dumps({"request_id": req["request_id"], "error": err, "data": data}).encode() + b"\n")

    def do(self, cmd):
        p = self.props
        if cmd[0] == "get_property":
            playing = not p["idle-active"]
            if cmd[1] in ("media-title", "path", "time-pos", "duration", "track-list") and not playing:
                return None, "property unavailable"
            return p.get(cmd[1]), "success"
        if cmd[0] == "set_property":
            p[cmd[1]] = cmd[2]
            return None, "success"
        if cmd[0] == "loadfile":
            p.update({"idle-active": False, "path": cmd[1], "media-title": p.get("force-media-title") or cmd[1], "time-pos": 0.0,
                      "duration": 5400.0, "track-list": [{"id": 1, "type": "video"}, {"id": 1, "type": "audio", "lang": "rus", "selected": True},
                                                         {"id": 2, "type": "audio", "lang": "eng"}, {"id": 1, "type": "sub", "lang": "eng"}]})
            return None, "success"
        if cmd[0] == "seek":
            p["time-pos"] = cmd[1] if cmd[2] == "absolute" else p["time-pos"] + cmd[1]
            return None, "success"
        if cmd[0] == "cycle":
            p[cmd[1]] = not p[cmd[1]]
            return None, "success"
        if cmd[0] == "add":
            p[cmd[1]] += cmd[2]
            return None, "success"
        if cmd[0] == "stop":
            p["idle-active"] = True
            return None, "success"
        return None, "invalid parameter"


@pytest.fixture
def mpv():
    d = tempfile.mkdtemp(prefix="ypl")          # a unix socket's path is short
    m = FakeMpv(os.path.join(d, "mpv.sock"))
    yield m, lambda req: ctl.run(req, ctl.Mpv(os.path.join(d, "mpv.sock")))
    m.srv.close()
    shutil.rmtree(d)


def test_ctl_plays_an_address_full_screen_and_drives_it(mpv):
    m, run = mpv
    assert run({"cmd": "status"}) == {"idle": True, "volume": 100.0, "mute": False}
    st = run({"cmd": "play", "url": "https://example.com/film.m3u8", "title": "A film", "start": 90})
    assert (st["idle"], st["title"], st["pause"], st["duration"]) == (False, "A film", False, 5400.0)
    assert ["set_property", "start", "90"] in m.log and ["set_property", "fullscreen", True] in m.log
    assert ["loadfile", "https://example.com/film.m3u8", "replace"] in m.log
    assert [t for t in st["tracks"] if t["type"] == "audio"] == [{"id": 1, "type": "audio", "lang": "rus", "title": "", "selected": True},
                                                                {"id": 2, "type": "audio", "lang": "eng", "title": "", "selected": False}]
    assert run({"cmd": "seek", "by": 30})["pos"] == 30 and run({"cmd": "seek", "to": 600})["pos"] == 600
    assert run({"cmd": "pause"})["pause"] is True and run({"cmd": "pause", "on": False})["pause"] is False
    assert run({"cmd": "volume", "by": -5})["volume"] == 95 and run({"cmd": "volume", "level": 40})["volume"] == 40
    assert run({"cmd": "mute", "on": True})["mute"] is True
    run({"cmd": "audio", "id": 2})
    run({"cmd": "sub", "id": 0})
    assert m.props["aid"] == 2 and m.props["sid"] == "no"
    assert run({"cmd": "stop"})["idle"] is True
    run({"cmd": "play", "url": "https://example.com/b.mp4"})                 # no start: from the beginning, no old title
    assert m.props["start"] == "none" and m.props["force-media-title"] == ""


def test_ctl_takes_only_its_commands_and_an_http_address(mpv):
    _, run = mpv
    for bad in ({"cmd": "run"}, {"cmd": "play", "url": "file:///etc/passwd"}, {"cmd": "play", "url": "https://a b"},
                {"cmd": "volume", "level": 500}, {"cmd": "seek", "by": "x"}, {"cmd": "audio", "id": True}):
        with pytest.raises(ctl.Error):
            run(bad)


def test_ctl_answers_json_even_when_mpv_is_not_running(monkeypatch, capsys):
    monkeypatch.setattr(ctl, "SOCK", "/nonexistent/mpv.sock")
    monkeypatch.setattr(ctl.Mpv.__init__, "__defaults__", ("/nonexistent/mpv.sock",))
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"cmd": "status"}'))
    assert ctl.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert not out["ok"] and "not running" in out["error"]
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    assert ctl.main() == 1


TVS = {"tv_main": {"id": "tv_main", "name": "Main TV", "category": "tv", "transport": "webos",
                   "controls": [{"code": "power"}, {"code": "input", "range": ["PC", "HDMI 3"]}]}}


@pytest.fixture
def house(monkeypatch, tmp_path):
    calls, sent = [], []

    def helper(method, path, body=None, timeout=15):
        calls.append((path, body))
        if path == "/v1/hosts/player":
            if body["host"] == "192.0.2.62":
                return {"ok": False, "error": "the player is not running (systemctl --user status yashome-player)"}
            return {"ok": True, "state": {"idle": body["cmd"] != "play", "title": body.get("title", "")}}
        if path == "/v1/hosts/run":
            return {"run": {"host": body["host"], "role": body["role"], "state": "running"}}
        return {}

    tv = {"online": False, "values": {"power": False, "input": "PC"}}

    async def control(dev, body):
        sent.append((dev, body.code, body.value))
        tv["online"] = True
        tv["values"][body.code] = body.value
        return {"ok": True}

    monkeypatch.setattr(core, "_helper", helper)
    monkeypatch.setattr(core, "control", control)
    monkeypatch.setattr(core, "get_status", lambda dev: tv if dev == "tv_main" else {})
    monkeypatch.setattr(pcs, "DATA", tmp_path / "computers.json")
    monkeypatch.setattr(pcs, "_computers", {
        "i3server": {"name": "i3server", "host": "192.0.2.61", "user": "tester", "tv": "tv_main", "input": "HDMI 3"},
        "bedroom": {"name": "bedroom", "host": "192.0.2.62", "user": "tester"},
        "kid": {"name": "Kid PC", "host": "192.0.2.63", "user": "kid", "wsl": "Ubuntu/kid"},
        "dell": {"name": "Dell", "host": "192.0.2.10", "user": "tester"}})
    monkeypatch.setattr(pcs, "_status", {"i3server": {"ok": True, "roles": ["player"]}, "bedroom": {"ok": True, "roles": ["player"]},
                                         "kid": {"ok": True, "roles": ["player"]}, "dell": {"ok": True, "roles": []}})
    monkeypatch.setattr(me, "_cache", {})
    for k, v in TVS.items():
        monkeypatch.setitem(core.DEVICES, k, v)
    c = TestClient(core.app)
    c.calls, c.sent, c.tv = calls, sent, tv
    return c


def test_the_players_are_the_computers_with_the_role_and_say_why_one_does_not_answer(house):
    ps = {p["id"]: p for p in house.get("/api/player", headers=HOUSE).json()["players"]}
    assert set(ps) == {"i3server", "bedroom"}                                 # a WSL has no screen; Dell has no player
    assert (ps["i3server"]["tv_name"], ps["i3server"]["input"], ps["i3server"]["state"]["idle"]) == ("Main TV", "HDMI 3", True)
    assert ps["bedroom"]["state"] is None and "not running" in ps["bedroom"]["error"]
    n = len(house.calls)
    house.get("/api/player", headers=HOUSE)
    assert len(house.calls) == n                                              # asked again only after STATE_TTL
    assert house.post("/api/player/kid/play", headers=HOUSE, json={"url": "https://example.com/a.mp4"}).status_code == 404


def test_play_turns_the_tv_on_onto_its_input_then_plays_there(house):
    r = house.post("/api/player/i3server/play", headers=HOUSE, json={"url": "https://example.com/a.m3u8", "title": "A film"})
    assert r.status_code == 200 and r.json()["state"]["idle"] is False and r.json()["tv_note"] == ""
    assert house.sent == [("tv_main", "power", True), ("tv_main", "input", "HDMI 3")]
    assert house.calls[-1] == ("/v1/hosts/player", {"host": "192.0.2.61", "user": "tester", "cmd": "play",
                                                    "url": "https://example.com/a.m3u8", "title": "A film"})
    house.sent.clear()
    house.post("/api/player/i3server/play", headers=HOUSE, json={"url": "https://example.com/b.m3u8"})
    assert house.sent == []                                                   # on and on its input already
    r = house.post("/api/player/bedroom/play", headers=HOUSE, json={"url": "https://example.com/a.m3u8"})
    assert r.status_code == 409 and r.json()["detail"].startswith("the player is not running")   # no TV there: nothing switched


def test_a_command_passes_only_its_fields(house):
    r = house.post("/api/player/i3server/cmd", headers=HOUSE, json={"cmd": "seek", "by": -30})
    assert r.status_code == 200
    assert house.calls[-1] == ("/v1/hosts/player", {"host": "192.0.2.61", "user": "tester", "cmd": "seek", "by": -30.0})
    assert house.post("/api/player/i3server/cmd", headers=HOUSE, json={"cmd": "play"}).status_code == 400
    assert house.post("/api/player/nope/cmd", headers=HOUSE, json={"cmd": "stop"}).status_code == 404


def test_the_computer_gets_ctl_and_its_unit_from_the_house_without_the_token(house):
    r = house.get("/plugins/player/host.tar.gz")
    assert r.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(r.content)) as t:
        assert sorted(t.getnames()) == ["ctl.py", "yashome-player.service"]
        assert t.getmember("ctl.py").mode == 0o755 and t.getmember("ctl.py").uid == 0


def test_computers_installs_a_player_anywhere_but_in_a_wsl_and_has_no_use(house):
    v = house.get("/api/computers", headers=HOUSE).json()
    roles = {c["id"]: c["roles"]["player"] for c in v["computers"]}
    assert roles["i3server"] == {"installed": True, "active": False, "many": True} and not roles["dell"]["installed"]
    r = house.post("/api/computers/dell/roles/player", headers=HOUSE, json={"action": "install"})
    assert r.status_code == 200
    assert house.calls[-1] == ("/v1/hosts/run", {"host": "192.0.2.10", "user": "tester", "role": "player", "action": "install",
                                                 "args": {"house": "http://192.0.2.10:8765"}})
    assert house.post("/api/computers/i3server/roles/player", headers=HOUSE, json={"action": "use"}).status_code == 400
    assert house.post("/api/computers/kid/roles/player", headers=HOUSE, json={"action": "install"}).status_code == 409
    house.post("/api/computers/i3server/roles/player", headers=HOUSE, json={"action": "remove"})
    assert house.calls[-1][1]["action"] == "remove" and house.calls[-1][1]["args"] == {}


# ---- what to play: the sources ----
src = sys.modules["yashome_plugins.player._sources"]

OWN = '''
NAME = "Site"
CALLS = []

def search(query):
    return [{"id": "s1", "title": "Series " + query, "year": 2020, "poster": "https://img.example.com/s1.jpg", "kind": "series"}]

def match(url):
    return "s1" if url.startswith("https://site.example.com/") else None

def info(id, translation=None):
    return {"title": "Series", "kind": "series", "poster": "https://img.example.com/s1.jpg",
            "translations": [{"id": "10", "name": "Dub"}, {"id": "11", "name": "Subs"}],
            "seasons": [{"n": 1, "episodes": [{"n": 1}, {"n": 2}]}, {"n": 2, "episodes": [{"n": 1}]}], "qualities": ["1080p", "720p"]}

def stream(id, translation=None, season=None, episode=None, quality=None):
    CALLS.append((id, translation, season, episode, quality))
    return {"url": f"https://cdn.example.com/{season}/{episode}.m3u8", "headers": {"Referer": "https://site.example.com/"},
            "subs": [{"url": "https://cdn.example.com/s.vtt", "lang": "en"}]}
'''


@pytest.fixture
def films(house, monkeypatch, tmp_path):
    d = tmp_path / "sources"
    d.mkdir()
    (d / "site.py").write_text(OWN)
    (d / "broken.py").write_text("raise ValueError('no key')\n")
    monkeypatch.setattr(src, "USER_DIR", d)
    monkeypatch.setattr(src, "_user", {})
    monkeypatch.setitem(me._conf, "folders", [])
    monkeypatch.setattr(me.core, "save_settings", lambda: None)
    monkeypatch.setattr(me, "WATCH", tmp_path / "watching.json")
    monkeypatch.setattr(me, "_watch", {"now": {}, "recent": []})
    monkeypatch.setattr(me, "_posters", set())
    house.dir = d
    return house


def test_the_sources_are_the_houses_own_first_then_files_and_link_and_a_broken_one_says_why(films):
    s = films.get("/api/player/sources", headers=HOUSE).json()["sources"]
    assert [x["id"] for x in s] == ["site", "files", "link", "broken"]
    assert s[0] == {"id": "site", "name": "Site", "own": True, "search": True, "match": True}
    assert s[-1]["error"] == "ValueError: no key"
    r = films.get("/api/player/search", headers=HOUSE, params={"q": "lost"}).json()
    assert r["results"][0] | {} == {"id": "s1", "title": "Series lost", "year": 2020, "poster": "https://img.example.com/s1.jpg",
                                    "kind": "series", "source": "site", "source_name": "Site"} and r["errors"] == {}


def test_a_pasted_page_goes_to_the_source_of_its_site_and_link_takes_the_rest(films, monkeypatch):
    r = films.post("/api/player/resolve", headers=HOUSE, json={"url": "https://site.example.com/series/123-lost.html"}).json()
    assert (r["source"], r["id"], r["info"]["title"]) == ("site", "s1", "Series")
    page = ('<html><head><title>T</title><meta property="og:title" content="A &amp; B">'
            '<meta property="og:image" content="https://img.example.com/p.jpg"></head><body>'
            '<video src="/v/a-720p.mp4"></video><script>var f="https:\\/\\/cdn.example.com\\/hls\\/1080p\\/index.m3u8?t=1";</script></body></html>')
    monkeypatch.setattr(src, "fetch", lambda url, headers=None, limit=0, method="GET": (url, "text/html; charset=utf-8", page.encode()))
    src.Link._pages.clear()
    r = films.post("/api/player/resolve", headers=HOUSE, json={"url": "https://other.example.org/watch/9"}).json()
    assert r["source"] == "link" and r["info"]["title"] == "A & B" and r["info"]["poster"] == "https://img.example.com/p.jpg"
    assert r["info"]["qualities"] == ["720p \u00b7 a-720p", "1080p \u00b7 index"]
    s = src.Link.stream("https://other.example.org/watch/9", quality="1080p \u00b7 index")
    assert s["url"] == "https://cdn.example.com/hls/1080p/index.m3u8?t=1" and s["headers"]["Referer"] == "https://other.example.org/watch/9"
    assert src.Link.stream("https://other.example.org/watch/9")["url"] == "https://other.example.org/v/a-720p.mp4"
    monkeypatch.setattr(src, "fetch", lambda *a, **k: ("https://x.example.org/", "text/html", b"<html>nothing</html>"))
    r = films.post("/api/player/resolve", headers=HOUSE, json={"url": "https://x.example.org/"})
    assert r.status_code == 503 and r.json()["detail"].startswith("no video on this page")
    assert films.post("/api/player/resolve", headers=HOUSE, json={"url": "ftp://x"}).status_code == 400


def test_watch_sends_the_sources_stream_with_its_headers_and_the_next_episode_follows(films):
    r = films.post("/api/player/i3server/watch", headers=HOUSE, json={"source": "site", "id": "s1", "translation": "10", "season": 1,
                                                                       "episode": 2, "quality": "1080p", "title": "Series"})
    assert r.status_code == 200, r.text
    assert sys.modules["yashome_player_source_site"].CALLS[-1] == ("s1", "10", 1, 2, "1080p")
    assert films.calls[-1] == ("/v1/hosts/player", {"host": "192.0.2.61", "user": "tester", "cmd": "play", "url": "https://cdn.example.com/1/2.m3u8",
                                                    "title": "Series \u00b7 S1E2", "headers": {"Referer": "https://site.example.com/"},
                                                    "subs": ["https://cdn.example.com/s.vtt"]})
    assert films.sent[:2] == [("tv_main", "power", True), ("tv_main", "input", "HDMI 3")]
    rec = films.get("/api/player/recent", headers=HOUSE).json()
    assert rec["recent"][0]["episode"] == 2 and rec["now"]["i3server"]["season"] == 1
    r = films.post("/api/player/i3server/next", headers=HOUSE).json()           # the last of season 1: season 2, episode 1
    assert films.calls[-1][1]["url"] == "https://cdn.example.com/2/1.m3u8" and r["watching"]["season"] == 2
    assert films.post("/api/player/i3server/next", headers=HOUSE).status_code == 409
    assert json.loads(me.WATCH.read_text())["now"]["i3server"]["episode"] == 1


def test_an_episode_that_ends_near_its_end_counts_as_seen_and_a_stop_by_hand_ends_the_series(films):
    films.post("/api/player/i3server/watch", headers=HOUSE, json={"source": "site", "id": "s1", "season": 1, "episode": 1})
    me._seen("i3server", {"idle": False, "pos": 1300.0, "duration": 1400.0})
    assert me._watch["recent"][0]["pos"] == 1300.0
    me._seen("i3server", {"idle": True})
    assert me._watch["now"]["i3server"]["ended"] is True                      # the loop starts the next one
    assert me._next_of(me._watch["now"]["i3server"]) == (1, 2)
    me._seen("i3server", {"idle": False, "pos": 100.0, "duration": 1400.0})
    films.post("/api/player/i3server/cmd", headers=HOUSE, json={"cmd": "stop"})
    assert "i3server" not in me._watch["now"]


def test_only_a_poster_a_source_named_is_fetched(films, monkeypatch):
    assert films.get("/api/player/poster", headers=HOUSE, params={"u": "https://img.example.com/s1.jpg"}).status_code == 404
    films.get("/api/player/search", headers=HOUSE, params={"q": "x"})
    monkeypatch.setattr(src, "fetch", lambda url, headers=None, limit=0, method="GET": (url, "image/jpeg", b"\xff\xd8jpg"))
    r = films.get("/api/player/poster", headers=HOUSE, params={"u": "https://img.example.com/s1.jpg"})
    assert r.status_code == 200 and r.content == b"\xff\xd8jpg" and r.headers["content-type"] == "image/jpeg"


def test_files_searches_the_web_listings_of_the_folders(films, tmp_path):
    import functools
    import http.server
    root = tmp_path / "nas"
    (root / "films" / "Old").mkdir(parents=True)
    (root / "films" / "The.Big.Film.2019.mkv").write_text("x")
    (root / "films" / "Old" / "Silent_Film.mp4").write_text("x")
    (root / "films" / "notes.txt").write_text("x")
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_port}/films"
        assert films.put("/api/player/folders", headers=HOUSE, json={"folders": [base, "file:///etc"]}).status_code == 400
        assert films.put("/api/player/folders", headers=HOUSE, json={"folders": [base]}).json() == {"folders": [base + "/"]}
        src.Files._index["ts"] = 0
        got = films.get("/api/player/search", headers=HOUSE, params={"q": "film"}).json()["results"]
        files = sorted(r["title"] for r in got if r["source"] == "files")
        assert files == ["Silent Film", "The Big Film 2019"]
        u = base + "/Old/Silent_Film.mp4"
        r = films.post("/api/player/resolve", headers=HOUSE, json={"url": u}).json()
        assert (r["source"], r["id"]) == ("files", u) and src.Files.stream(u)["url"] == u
    finally:
        srv.shutdown()


def test_ctl_sends_a_sites_headers_and_subtitles_with_the_film(mpv):
    m, run = mpv
    run({"cmd": "play", "url": "https://cdn.example.com/a.m3u8", "headers": {"Referer": "https://site.example.com/"},
         "subs": ["https://cdn.example.com/a.vtt"]})
    assert m.props["http-header-fields"] == ["Referer: https://site.example.com/"] and m.props["sub-files"] == ["https://cdn.example.com/a.vtt"]
    run({"cmd": "play", "url": "https://cdn.example.com/b.mp4"})
    assert m.props["http-header-fields"] == [] and m.props["sub-files"] == []
    for bad in ({"headers": {"Bad Name": "x"}}, {"headers": {"X": "a\r\nInjected: 1"}}, {"subs": ["file:///x"]}, {"headers": ["x"]}):
        with pytest.raises(ctl.Error):
            run({"cmd": "play", "url": "https://cdn.example.com/a.m3u8", **bad})


def test_the_touchmouse_receiver_is_a_role_of_every_computer_that_wants_one(house):
    r = house.get("/plugins/touchmouse/host.tar.gz")
    with tarfile.open(fileobj=io.BytesIO(r.content)) as t:
        assert sorted(t.getnames()) == ["touchmouse-rx.py", "touchmouse-rx.service", "touchmouse-type.py", "touchmouse-type.service"]
    pcs._status["bedroom"]["roles"] = ["player", "touchmouse"]
    v = house.get("/api/computers", headers=HOUSE).json()
    tm = {c["id"]: c["roles"]["touchmouse"] for c in v["computers"]}
    assert tm["bedroom"]["installed"] and tm["bedroom"]["many"] and not tm["dell"]["installed"]
    house.post("/api/computers/dell/roles/touchmouse", headers=HOUSE, json={"action": "install"})
    assert house.calls[-1] == ("/v1/hosts/run", {"host": "192.0.2.10", "user": "tester", "role": "touchmouse", "action": "install",
                                                 "args": {"house": "http://192.0.2.10:8765"}})
    assert house.post("/api/computers/bedroom/roles/touchmouse", headers=HOUSE, json={"action": "use"}).status_code == 400


def test_the_films_button_sits_right_of_the_logo():
    js = (REPO / "plugins/tools/player/ui.js").read_text(encoding="utf-8")
    assert "slot: 'brand'" in js and "Home.openTab('p:films')" in js and "hidden: true" in js


def test_with_no_source_that_searches_the_page_is_told_there_is_nowhere_to_search(films):
    (films.dir / "site.py").unlink()
    r = films.get("/api/player/search", headers=HOUSE, params={"q": "x"}).json()
    assert r == {"results": [], "errors": {}, "searched": []}                    # Files has no folders: not asked
