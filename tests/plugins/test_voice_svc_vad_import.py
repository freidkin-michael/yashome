"""voice_vad.py imported with its heavy and network modules stubbed: no thread starts, no socket opens (the service starts only as
__main__), so a name that is used but never defined fails here and not on the first real phrase. Also: ruff F821/F811 over voice/."""
import json
import os
import shutil
import subprocess
import sys
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
VOICE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugins", "tools", "voice", "service", "vad")
sys.path.insert(0, VOICE)
sys.dont_write_bytecode = True

vad = None
try:                                                  # the audio paths need numpy: the image has it, a test host may not
    import numpy  # noqa: F401
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False


def _commands_file():
    """A made-up phrase map (the engine has none of its own): the kitchen light by its stem, the two verbs."""
    import tempfile
    path = os.path.join(tempfile.mkdtemp(prefix="voice-svc-"), "voice_commands.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"on_words": ["включ"], "off_words": ["выключ"], "appliances": [],  # i18n
                   "zones": [{"words": ["кухн"], "backend": "z2m", "target": "Kitchen light", "code": "state"}]}, f)
    return path


def setUpModule():
    global vad
    stubs = {"webrtcvad": mock.MagicMock(), "paho": mock.MagicMock(), "paho.mqtt": mock.MagicMock(), "paho.mqtt.client": mock.MagicMock()}
    try:
        import requests  # noqa: F401
    except ImportError:
        stubs["requests"] = mock.MagicMock()
    env = {"VA_COMMANDS": _commands_file(), "VA_LLM_URL": "", "VA_BIG_URL": "", "DASHBOARD_TOKEN": "t", "VA_PHRASELOG_DIR": ""}
    with mock.patch.dict(sys.modules, stubs), mock.patch.dict(os.environ, env):
        import voice_vad
        vad = voice_vad


class Resp:
    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class FakeRequests:
    def __init__(self, routes=None):
        self.routes, self.urls = routes, []

    def get(self, url, **kw):
        self.urls.append(url)
        if self.routes is None:
            raise OSError("house down")
        return Resp(self.routes[url.rsplit("/api", 1)[1]])


class ImportAndPaths(unittest.TestCase):
    def test_the_module_imports_without_starting_anything(self):
        self.assertTrue(callable(vad._map_facts) and callable(vad.confirm_pending) and callable(vad._brains))
        self.assertFalse(hasattr(vad, "srv") or hasattr(vad, "conns"))          # the socket and the loop live under __main__

    def test_map_facts_reads_the_map_and_names_the_loads(self):
        m = {"items": [{"id": "l1", "name": "центральная люстра"}],  # i18n
             "links": [{"load": "l1", "dev": "sw1"}]}
        fake = FakeRequests({"/map/facts": {"lines": ["a", 2]}, "/map": m})
        with mock.patch.object(vad, "requests", fake):
            lines, words = vad._map_facts()
        self.assertEqual(lines, ["a", "2"])
        self.assertEqual(words, [("центра", "sw1"), ("\u043b\u044e\u0441\u0442\u0440\u0430", "sw1")])  # i18n
        self.assertEqual(len(fake.urls), 2)

    def test_map_facts_is_empty_when_the_house_does_not_answer(self):
        with mock.patch.object(vad, "requests", FakeRequests()):
            self.assertEqual(vad._map_facts(), ([], []))

    def test_a_question_that_expired_and_a_refusal_are_answered_in_the_language_asked(self):
        vad._pending.clear()
        self.assertEqual(vad.confirm_pending("k", "yes")["said"], "Вопрос устарел.")  # i18n
        vad._pending["k"] = {"actions": [], "until": time.time() + 30, "q": "q", "learn": None, "lang": "en"}
        self.assertEqual(vad.confirm_pending("k", "no")["said"], "Cancelled.")
        self.assertNotIn("k", vad._pending)

    def test_the_text_box_answers_in_the_house_language_whatever_the_phrase(self):
        self.assertEqual(vad.ask_text("   ")["said"], vad.NOT_UNDERSTOOD)
        with mock.patch.object(vad, "HOUSE_LANG", "en"):
            res = vad._ask_text("zzqx qwvb plmn")
            self.assertEqual((res["ok"], res["said"]), (False, "Sorry, I did not understand."))
            self.assertEqual(vad._ask_text("щщцу ффывы")["said"], "Sorry, I did not understand.")  # i18n
        with mock.patch.object(vad, "HOUSE_LANG", "ru"):
            self.assertEqual(vad._ask_text("zzqx qwvb plmn")["said"], vad.NOT_UNDERSTOOD)
        with mock.patch.object(vad, "HOUSE_LANG", "de"):                     # a language with no phrases yet: Russian
            self.assertEqual(vad._ask_text("zzqx qwvb plmn")["said"], vad.NOT_UNDERSTOOD)


class DashboardSettings(unittest.TestCase):
    """Settings -> Voice: voice-vad reads /api/voice/config, seeds it once from its env, and the values act without a restart."""
    NAMES = ("WAKE", "WAKE_MAXDIST", "WAKE_PREFIX", "LLM_ON", "LLM_MODEL", "BIG_MODEL", "LLM_TIMEOUT", "LLM_CONFIRM_ALL_OFF", "ASK_TTL",
             "NEEDWAKE_IPS", "HOUSE_LANG")

    def setUp(self):
        import voice_rules
        self.rules = voice_rules
        self.saved = {n: getattr(vad, n) for n in self.NAMES}
        self.saved_rules = (voice_rules.WAKE, list(voice_rules.WAKE_VARIANTS), voice_rules.WAKE_MAXDIST)
        self.llm = dict(vad._llm)

    def tearDown(self):
        for n, v in self.saved.items():
            setattr(vad, n, v)
        self.rules.WAKE, self.rules.WAKE_VARIANTS, self.rules.WAKE_MAXDIST = self.saved_rules
        vad._llm.update(self.llm)

    def test_the_page_values_act_at_once_and_reach_the_live_models(self):
        small = types.SimpleNamespace(model="a", timeout=5.0)
        big = types.SimpleNamespace(model="b", timeout=30.0)
        vad._llm.update(ollama=small, brains=types.SimpleNamespace(big=big, model="b", loaded=True))
        with mock.patch.object(vad, "LLM_URL", ""):                                # no Ollama: the switch and the model change start nothing
                vad.apply_config({"wake": "Домовой", "wake_maxdist": 1, "wake_prefix": "", "llm": False, "llm_model": "m1", "big_model": "m2",  # i18n
                          "llm_timeout": 7, "confirm_all_off": False, "ask_ttl": 20, "lang": "en", "seeded": True,
                              "need_wake": ["192.0.2.9"]})
        self.assertEqual((vad.WAKE, vad.WAKE_MAXDIST, vad.WAKE_PREFIX, vad.LLM_ON, vad.ASK_TTL, vad.HOUSE_LANG, vad.NEEDWAKE_IPS),
                         ("домовой", 1, "", False, 20.0, "en", {"192.0.2.9"}))  # i18n
        self.assertEqual((small.model, small.timeout, big.model, vad._llm["brains"].model, vad._llm["brains"].loaded), ("m1", 7.0, "m2", "m2", False))
        self.assertEqual(vad.find_wake(["домовоя", "свет"]), 0)  # i18n
        self.assertEqual(vad.find_wake(["домовые", "свет"]), -1)                 # two letters off with "how exact" = 1
        self.assertFalse(vad.LLM_CONFIRM_ALL_OFF)

    def test_an_absent_field_keeps_its_value_and_need_wake_waits_for_the_seed(self):
        vad.NEEDWAKE_IPS = {"192.0.2.1"}
        vad.apply_config({"lang": "ru", "need_wake": []})
        self.assertEqual((vad.WAKE, vad.NEEDWAKE_IPS, vad.LLM_MODEL), (self.saved["WAKE"], {"192.0.2.1"}, self.saved["LLM_MODEL"]))

    def test_the_first_read_seeds_the_house_from_the_env_once(self):
        vad.NEEDWAKE_IPS = {"192.0.2.98"}
        calls = []

        class R:
            def get(self, url, **kw):
                calls.append(("GET", url))
                return Resp({"lang": "ru", "need_wake": []})

            def put(self, url, **kw):
                calls.append(("PUT", url, kw["params"], kw["json"]))
                return Resp({**kw["json"], "lang": "ru", "seeded": True, "need_wake": kw["json"]["need_wake_peers"]})
        with mock.patch.object(vad, "requests", R()):
            c = vad.config_tick()
        self.assertEqual([x[0] for x in calls], ["GET", "PUT"])
        self.assertTrue(calls[0][1].endswith("/api/voice/config") and calls[1][2] == {"seed": 1})
        self.assertEqual(calls[1][3]["wake"], vad.WAKE)
        self.assertEqual((c["seeded"], vad.NEEDWAKE_IPS), (True, {"192.0.2.98"}))

    def test_only_the_wake_word_and_its_variants_wake(self):
        vad.apply_config({"wake": "домовой", "wake_maxdist": 0, "wake_prefix": "", "wake_variants": ["лесовик*", "домик"]})  # i18n
        for w, i in (("домовой", 0), ("лесовика", 0), ("домик", 0), ("домовая", -1), ("джарвис", -1), ("крис", -1), ("берлога", -1)):
            self.assertEqual(vad.find_wake([w, "свет"]), i, w)  # i18n
        self.assertFalse(self.rules.close_to_wake("домовая"))                       # how exact 0: nothing close counts
        self.assertEqual(self.rules.strip_wake_phrase("эй лесовику включи свет".split()), ["включи", "свет"])  # i18n
        self.assertEqual(self.rules.strip_wake_phrase("эй джарвис включи свет".split()), "эй джарвис включи свет".split())
        vad.apply_config({"wake_maxdist": 3})
        self.assertEqual(vad.WAKE_MAXDIST, 3)
        vad.apply_config({"wake": "кот", "wake_maxdist": 3})                        # i18n: a short word allows fewer
        self.assertEqual((vad.WAKE_MAXDIST, self.rules.WAKE_MAXDIST), (1, 1))

    def test_the_seed_fits_the_bounds_and_carries_todays_variants(self):
        vad.WAKE, vad.WAKE_MAXDIST, vad.WAKE_PREFIX = "крис", 3, "кр"  # i18n
        c = vad._env_config()
        self.assertEqual((c["wake_maxdist"], c["wake_prefix"]), (1, ""))
        self.assertIn("берлог*", c["wake_variants"])  # i18n

    def test_a_seed_never_carries_a_value_the_page_would_refuse(self):
        vad.WAKE, vad.LLM_TIMEOUT, vad.ASK_TTL, vad.LLM_MODEL = "джарвис2", 90.0, 1.0, "bad model"  # i18n
        with mock.patch.object(vad, "WAKE_VARIANTS", ["эй берлога", "дж*", "x" * 25, "ok"]), mock.patch("builtins.print") as out:
            c = vad._env_config()
        self.assertEqual((c["wake"], c["llm_timeout"], c["ask_ttl"]), ("джарвис", 60.0, 3.0))  # i18n
        self.assertNotIn("llm_model", c)
        self.assertTrue(set(c["wake_variants"]) >= {"ok", "берлог*"} and not {"эй берлога", "дж*", "x" * 25} & set(c["wake_variants"]))  # i18n
        self.assertIn("fitted", out.call_args.args[0])

    def test_a_stored_short_prefix_or_stem_counts_as_none(self):
        vad.apply_config({"wake_prefix": "дж", "wake_variants": ["дж*", "берлог*"]})  # i18n
        self.assertEqual((vad.WAKE_PREFIX, self.rules.WAKE_VARIANTS), ("", ["берлог*"]))

    def test_a_seed_value_that_is_no_number_or_no_plain_letter_is_fitted(self):
        vad.WAKE, vad.LLM_TIMEOUT = "джарвис\u00b2", float("nan")  # i18n
        with mock.patch("builtins.print"):
            c = vad._env_config()
        self.assertEqual((c["wake"], c["llm_timeout"]), ("джарвис", 60))  # i18n

    def test_smart_answers_off_stop_the_big_models_poller_and_on_starts_the_models(self):
        stopped, started = [], []
        vad._llm.update(ollama=types.SimpleNamespace(model="a", timeout=5.0), brains=types.SimpleNamespace(big=None, model=None, stop=lambda: stopped.append(1)))
        with mock.patch.object(vad, "LLM_URL", "http://llm"), mock.patch.object(vad, "_llm_start", lambda: started.append(1)):
            vad.apply_config({"llm": False})
            self.assertEqual((stopped, vad._llm["brains"]), ([1], None))
            vad.apply_config({"llm": True})
            time.sleep(0.2)
            self.assertEqual(started, [1])
            vad.apply_config({"llm_model": "other"})                                  # a new model: warmed now
            time.sleep(0.2)
            self.assertEqual(started, [1, 1])

    def test_a_failed_read_is_logged_once_per_reason(self):
        ticks = iter([ValueError("a"), ValueError("a"), ValueError("b"), KeyboardInterrupt()])

        def tick():
            raise next(ticks)
        with mock.patch.object(vad, "config_tick", tick), mock.patch.object(vad.time, "sleep", lambda s: None), \
                mock.patch("builtins.print") as out:
            with self.assertRaises(KeyboardInterrupt):
                vad._config_loop()
        self.assertEqual(len([c for c in out.call_args_list if "not read" in c.args[0]]), 2)

    def test_an_error_answer_changes_nothing(self):
        with mock.patch.object(vad, "requests", FakeRequests({"/voice/config": {"detail": "Not Found"}})):
            with self.assertRaises(ValueError):
                vad.config_tick()
        self.assertEqual(vad.WAKE, self.saved["WAKE"])


class PhrasePaths(unittest.TestCase):
    """_finish_vosk run end to end on a recorded command with the recognizer stubbed, one test per branch: the sounds that go out (voice/heard) in order.
    22.10: a name that was bound only in one branch of the function raised after the command had run and the same audio was decoded on every frame."""
    IP = "192.0.2.50"

    def setUp(self):
        self.heard = []
        fake_pub = lambda topic, payload, *a, **k: self.heard.append(json.loads(payload)) if topic == "voice/heard" else None          # noqa: E731
        self.patches = [mock.patch.object(vad, "pub", fake_pub), mock.patch.object(vad, "_save_capture", lambda pcm: "p.wav"), mock.patch.object(vad, "_plog", lambda **k: None),
                        mock.patch.object(vad, "_room_of", lambda ip: ""), mock.patch.object(vad, "NOWAKE_IPS", {"*"}), mock.patch.object(vad, "LLM_URL", ""),
                        mock.patch.object(vad, "_result_reply", lambda *a, **k: None)]
        for p in self.patches:
            p.start()
        vad._pending.clear()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        vad._pending.clear()

    def run_phrase(self, grammar=("", 0.0), free=("", 0.0)):
        class V:
            def recognize(self, pcm, grammar_pass):
                return grammar if grammar_pass else free
        st = {"ip": self.IP, "voiced": bytearray(b"\x01\x00" * 16000), "buf": bytearray(), "triggered": True, "quiet": 0}
        with mock.patch.object(vad, "_vosk_grammar", lambda: V()):
            vad._finish_vosk(st, b"\x01\x00" * 16000, "x.wav")
        return self.heard

    def test_a_command_is_beeped_at_once_and_then_silent(self):
        with mock.patch.object(vad, "act", lambda cmd: True):
            h = self.run_phrase(("эй берлога включи свет на кухне", 0.9), ("эй берлога включи свет на кухне", 0.9))  # i18n
        self.assertEqual([(m.get("sound"), m.get("quiet"), m.get("reply")) for m in h], [("done", None, None), (None, True, None)])
        self.assertTrue(h[1]["ok"])

    def test_a_command_that_went_wrong_says_which_device_did_not_answer_after_its_beep(self):
        with mock.patch.object(vad, "act", lambda cmd: True), mock.patch.object(vad, "_result_reply", lambda *a, **k: "Свет в спальне не ответил"):  # i18n
            h = self.run_phrase(("включи свет в спальне", 0.9), ("включи свет в спальне", 0.9))
        self.assertEqual([(m.get("sound"), m.get("quiet"), m.get("reply")) for m in h], [("done", None, None), (None, None, "Свет в спальне не ответил")])  # i18n

    def test_a_question_is_answered_aloud_without_a_beep(self):
        with mock.patch.object(vad, "voice_state_is_question", lambda cmd: True), mock.patch.object(vad, "quick_answer", lambda cmd: ("В спальне 22 градуса", "В спальне 22 градуса")):  # i18n
            h = self.run_phrase(("", 0.0), ("эй берлога какая температура в спальне", 0.9))
        self.assertEqual([(m.get("sound"), m.get("reply")) for m in h], [(None, "В спальне 22 градуса")])  # i18n

    def test_the_answer_to_a_pending_question_is_a_confirm(self):
        vad._pending[self.IP] = {"actions": [], "until": time.time() + 30, "q": "Выключить всё?", "learn": None, "lang": "ru"}  # i18n
        with mock.patch.object(vad, "confirm_pending", lambda key, text, voice=False: {"ok": True, "said": "Сделано", "reply": "Выключила"}):
            h = self.run_phrase(("", 0.0), ("да", 0.9))  # i18n
        self.assertEqual([(m.get("engine"), m.get("reply")) for m in h], [("confirm", "Выключила")])

    def test_a_phrase_for_the_model_gets_its_reply_and_no_second_sound(self):
        llm = {"ok": True, "reply": "Включила", "said": "Сделано", "ask": False, "line": "", "decision": "do(1/1)", "brain": "x"}  # i18n
        with mock.patch.object(vad, "LLM_URL", "http://llm"), mock.patch.object(vad, "act", lambda cmd: False), mock.patch.object(vad, "llm_handle", lambda cmd, key, source: llm):
            h = self.run_phrase(("", 0.0), ("эй берлога сделай поуютнее", 0.9))  # i18n
        self.assertEqual([(m.get("engine"), m.get("reply")) for m in h], [("llm", "Включила")])

    def test_a_phrase_that_is_not_understood_is_answered_ne_ponyala_without_a_beep(self):
        with mock.patch.object(vad, "act", lambda cmd: False):
            h = self.run_phrase(("", 0.0), ("эй берлога тарабарщина какая то", 0.9))  # i18n
        self.assertEqual([(m.get("sound"), m.get("ok"), m.get("reply"), m.get("quiet")) for m in h], [(None, False, vad.NOT_UNDERSTOOD, None)])

    def test_only_the_wake_phrase_a_stray_word_or_low_confidence_get_no_answer_at_all(self):
        with mock.patch.object(vad, "act", lambda cmd: False):
            self.assertEqual(self.run_phrase(("", 0.0), ("эй берлога", 0.9)), [])                 # i18n: 22.10: only the wake phrase after a pause: nothing to say
            self.assertEqual(self.run_phrase(("", 0.0), ("я", 0.32)), [])                        # a stray word after a reply

    def test_the_house_own_reply_heard_by_the_mic_is_no_phrase(self):
        with mock.patch.object(vad, "act", lambda cmd: False):
            vad._last_said[self.IP] = ({"не", "поняла"}, time.time())                             # i18n: "Не поняла." was just said to this satellite
            self.assertEqual(self.run_phrase(("", 0.0), ("не поняла", 0.9)), [])
            self.assertEqual(self.run_phrase(("", 0.0), ("не", 0.9)), [])  # i18n
            vad._last_said.clear()

    @unittest.skipUnless(HAVE_NUMPY, "numpy is not installed here")
    def test_a_finished_phrase_closes_the_stream_of_the_node(self):
        st = vad.new_state()
        st["triggered"], st["voiced"] = True, bytearray(b"\x01\x00" * 8000)
        vad.vad = mock.MagicMock()
        vad.vad.is_speech.return_value = False
        with mock.patch.object(vad, "_finish", lambda state: None), mock.patch("voice_record.bg_feed", lambda *a: False), mock.patch("voice_record.feed", lambda *a: False):
            vad.process(b"\x00" * vad.FBYTES * 300, st)
        self.assertTrue(st.get("done"))                                                         # marked finished; the node closes its stream itself

    def test_a_peer_that_must_say_the_wake_phrase_is_ignored_without_one(self):
        with mock.patch.object(vad, "act", lambda cmd: True), mock.patch.object(vad, "NEEDWAKE_IPS", {self.IP}):
            self.assertEqual(self.run_phrase(("включи свет на кухне", 0.9), ("кэмерона включи свет на кухне", 0.9)), [])       # i18n: the tablet woke on a stray sound
            h = self.run_phrase(("эй берлога включи свет на кухне", 0.9), ("эй берлога включи свет на кухне", 0.9))
        self.assertEqual([m.get("sound") for m in h], ["done", None])

    def stream(self, speech_frames, total):
        """The frames of a stream (30 ms each): speech where speech_frames holds the index; -> the index of the frame at which the phrase was finished."""
        st = vad.new_state()
        vad.vad = mock.MagicMock()
        vad.vad.is_speech.side_effect = lambda frame, rate: vad.vad.i in speech_frames
        done = []
        for i in range(total):
            vad.vad.i = i
            with mock.patch.object(vad, "_finish", lambda state: done.append(i)), mock.patch("voice_record.bg_feed", lambda *a: False), mock.patch("voice_record.feed", lambda *a: False):
                vad.process(b"\x00" * vad.FBYTES, st)
            if done:
                return done[0]
        return None

    @unittest.skipUnless(HAVE_NUMPY, "numpy is not installed here")
    def test_the_wake_word_in_the_preroll_is_no_part_of_the_command_the_end_waits_for_the_first_word(self):
        # the node's preroll (1.5 s = 50 frames) holds the wake word; he pauses: no speech after it
        end = self.stream(set(range(5, 40)), 400)
        self.assertGreaterEqual(end, 40 + 130)                                                  # 4 s of silence, not 0.6 s
        # he speaks after the preroll (frames 70-110): 0.6 s of silence ends it
        end = self.stream(set(range(5, 40)) | set(range(70, 110)), 400)
        self.assertLessEqual(end, 110 + 22)
        self.assertGreaterEqual(end, 110 + 19)

    @unittest.skipUnless(HAVE_NUMPY, "numpy is not installed here")
    def test_a_phrase_that_raises_is_dropped_and_the_stream_is_reset_never_decoded_again(self):
        st = vad.new_state()
        st["triggered"], st["voiced"] = True, bytearray(b"\x01\x00" * 8000)
        vad.vad = mock.MagicMock()
        vad.vad.is_speech.return_value = False
        calls = []

        def boom(state):
            calls.append(1)
            raise RuntimeError("boom")
        with mock.patch.object(vad, "_finish", boom), mock.patch("voice_record.bg_feed", lambda *a: False), mock.patch("voice_record.feed", lambda *a: False):
            for _ in range(4):
                vad.process(b"\x00" * vad.FBYTES * 200, st)
        self.assertEqual(len(calls), 1)                                                      # one failure, then the state is clean: nothing runs again
        self.assertFalse(st["triggered"])
        self.assertEqual(len(st["voiced"]), 0)


class Lint(unittest.TestCase):
    def test_no_undefined_or_redefined_names_in_voice(self):
        ruff = shutil.which("ruff")
        if ruff is None:
            self.skipTest("ruff is not installed (command: ruff check --select F821,F811 voice)")
        r = subprocess.run([ruff, "check", "--no-cache", "--select", "F821,F811,F823,F841", "--isolated", VOICE], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()


class Rekey(unittest.TestCase):
    def test_a_merged_devices_former_id_reads_as_the_device_it_is_now(self):
        g = {"names": {"tv_ir": {"nom": "телевизор в спальне"}, "tv_ir/ir_menu": {"nom": "меню"}, "lamp": {"nom": "лампа"}},  # i18n
             "aliases": {"tv_ir": ["телик"]}, "learned": {"включи телик": [["tv_ir", "ir_power_on", True]]}}
        devs = [{"id": "tv", "aliases": ["tv_ir"]}, {"id": "lamp", "aliases": []}]
        got = vad._rekey(g, devs)
        self.assertEqual(set(got["names"]), {"tv", "tv/ir_menu", "lamp"})
        self.assertEqual(got["aliases"], {"tv": ["телик"]})  # i18n
        self.assertEqual(got["learned"]["включи телик"], [["tv", "ir_power_on", True]])
        g2 = {"names": {"tv_ir": {"nom": "old"}, "tv": {"nom": "new"}}}
        self.assertEqual(vad._rekey(g2, devs)["names"], {"tv": {"nom": "new"}})       # the new id's own entry wins


class ServedRoom(unittest.TestCase):
    def test_a_device_counts_in_the_room_it_serves_not_where_it_stands(self):
        rooms = [{"name": "Hall", "devices": ["ir1", "lamp"], "serves": ["lamp"]},
                 {"name": "Salon", "devices": ["tv"], "serves": ["tv", "ir1"]}]
        self.assertEqual(vad._served_by(rooms), {"lamp": "Hall", "tv": "Salon", "ir1": "Salon"})

    def test_an_older_dashboard_without_serves_goes_by_where_it_stands(self):
        self.assertEqual(vad._served_by([{"name": "Hall", "devices": ["lamp"]}]), {"lamp": "Hall"})
