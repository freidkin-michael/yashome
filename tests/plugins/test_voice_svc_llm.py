"""Unit tests of voice_llm: the 15 phrases of the bench through a mock Ollama (recorded answers), the strict parser, the values, the questions.
Run: python -m unittest discover -s tests  (from the voice folder)."""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugins", "tools", "voice", "service", "vad"))
from voice_svc_fixtures import FIXTURES  # noqa: E402
import voice_llm as L  # noqa: E402

load = lambda n: json.loads(json.dumps(FIXTURES[n]))  # noqa: E731
ANSWERS = load("llm_answers.json")
REG = L.Registry(load("registry.json"), [tuple(x) for x in load("zone_words.json")])


class MapFacts(unittest.TestCase):
    def test_the_home_map_facts_are_in_the_prompt_only_when_there_are_some(self):
        devs, zw = load("registry.json"), [tuple(x) for x in load("zone_words.json")]
        self.assertNotIn("Places", L.Registry(devs, zw).system)
        reg = L.Registry(devs, zw, facts=["\u0426\u0435\u043d\u0442\u0440\u0430\u043b\u044c\u043d\u0430\u044f \u043b\u044e\u0441\u0442\u0440\u0430 (\u0421\u0430\u043b\u043e\u043d, lamp): controlled by Hall main / state_left"] * 30)  # i18n
        self.assertEqual(len(reg.facts), 24)                                              # a bound: the prompt stays small
        self.assertIn("Places (a load the owner marked on the home map", reg.system)
        self.assertIn("controlled by Hall main / state_left", reg.system)

    def test_a_map_word_is_a_synonym_only_when_it_is_one_devices_and_not_generic(self):
        m = {"items": [{"id": "l1", "name": "Центральная люстра"}, {"id": "l2", "name": "Лампа у кровати"}, {"id": "l3", "name": "Люстра спальни"},  # i18n
                       {"id": "l4", "name": "Свет над диваном"}],
             "links": [{"load": "l1", "dev": "A"}, {"load": "l2", "dev": "B"}, {"load": "l3", "dev": "C"}, {"load": "l4", "dev": "D"}]}
        got = L.map_words(m)
        self.assertEqual(sorted(got), [("дивано", "D"), ("кроват", "B"), ("спальн", "C"), ("центра", "A")])     # i18n: люстра: two devices, лампа/свет: generic
        self.assertNotIn(("кроват", "B"), L.map_words(m, [("кров", "Z")]))                                   # another device already has the word
        self.assertIn(("кроват", "B"), L.map_words(m, [("кров", "B")]))  # i18n
        two_loads = {"items": [{"id": "a", "name": "Левая люстра"}, {"id": "b", "name": "Правая люстра"}],
                     "links": [{"load": "a", "dev": "E"}, {"load": "b", "dev": "E"}]}
        self.assertIn(("люстра", "E"), L.map_words(two_loads))                                              # i18n: two loads of one device: still its word

    def test_a_generic_map_word_does_not_make_a_device_named(self):
        m = {"items": [{"id": "l", "name": "Свет над диваном"}], "links": [{"load": "l", "dev": "ac"}]}  # i18n
        reg = L.Registry(load("registry.json"), L.map_words(m))
        self.assertFalse(reg.named_in("ac", ["включи", "свет"]))  # i18n
        self.assertTrue(reg.named_in("ac", ["диван"]))

    def test_the_load_facts_come_first_when_the_cap_drops_lines(self):
        loads = ["Лампа %d (Салон, lamp): controlled by Hall main / state_left" % i for i in range(3)]  # i18n
        reg = L.Registry(load("registry.json"), [], facts=["Комната %d: salon" % i for i in range(30)] + loads)
        self.assertEqual(reg.facts[:3], loads)
        self.assertEqual((len(reg.facts), reg.facts_dropped), (24, 9))

    def test_structured_items_sort_without_reading_the_text(self):
        js = {"lines": ["x"], "items": [{"text": "Room A: salon", "load": False, "controlled": False},
                                        {"text": "Lamp 1 (Salon): wired to nothing", "load": True, "controlled": False},
                                        {"text": "Lamp 2 (Salon): on the wall switch", "load": True, "controlled": True}]}
        reg = L.Registry(load("registry.json"), [], facts=L.facts_of(js))
        self.assertEqual(reg.facts, ["Lamp 2 (Salon): on the wall switch", "Lamp 1 (Salon): wired to nothing", "Room A: salon"])

    def test_the_cap_keeps_controlled_loads_of_the_items_and_plain_lines_are_the_fallback(self):
        items = [{"text": "Room %d: salon" % i, "load": False, "controlled": False} for i in range(30)]
        items.append({"text": "Lamp: on a switch", "load": True, "controlled": True})
        reg = L.Registry(load("registry.json"), [], facts=L.facts_of({"items": items}))
        self.assertEqual((reg.facts[0], len(reg.facts), reg.facts_dropped), ("Lamp: on a switch", 24, 7))
        self.assertEqual(L.facts_of({"lines": ["a", "b"]}), ["a", "b"])
        self.assertEqual(L.facts_of({"lines": ["a"], "items": []}), ["a"])
        self.assertEqual(L.facts_of([]), [])


class Mock(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Mock.seen.append(body)
        if body.get("stream"):                                     # the chat: NDJSON pieces
            self.send_response(200)
            self.end_headers()
            for piece in ("При", "вет", "!"):  # i18n
                self.wfile.write((json.dumps({"message": {"content": piece}, "done": False}) + "\n").encode())
            self.wfile.write((json.dumps({"message": {"content": ""}, "done": True}) + "\n").encode())
            return
        out = ANSWERS.get(body["messages"][-1]["content"], "none")
        data = json.dumps({"message": {"content": out}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


STATE = {("dev_10", "temp"): 18}


def run(phrase, ollama, confirm_all_off=False):
    parsed = L.parse_dsl(ollama.ask(REG.system, phrase), REG.names)
    words = L.re.findall(r"[а-яё0-9]+", phrase.lower())  # i18n
    return parsed, L.plan(REG, parsed, words, state=lambda did, code: STATE.get((did, code)), confirm_all_off=confirm_all_off)


class Phrases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.ol = L.Ollama("http://127.0.0.1:%d" % cls.srv.server_port, "m")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def acts(self, phrase):
        parsed, p = run(phrase, self.ol)
        return p, sorted(p["actions"])

    def test_plain_commands_run_without_a_question(self):
        want = {
            "включи свет на кухне": [("Кухня", "state", "on")],  # i18n
            "выключи коридор": [("Corridor 1 Gang Switch", "state", "off")],
            "зажги верхний свет в салоне": [("AC light", "state", "on")],  # i18n
            "включи кондиционер в спальне": [("dev_10", "power", "on")],
            "кондиционер в салоне на охлаждение": [("ac", "mode", "cool")],  # i18n
            "включи бойлер": [("water heater", "state", "on")],
        }
        for phrase, acts in want.items():
            p, got = self.acts(phrase)
            self.assertEqual(got, sorted(acts), phrase)
            self.assertEqual(p["ask"], [], phrase)

    def test_temperature_number_and_relative(self):
        p, got = self.acts("поставь кондей в зале на двадцать четыре градуса")  # i18n
        self.assertEqual(got, [("ac", "temp", "24")])
        parsed, p = run("потеплее в спальне", self.ol)  # i18n
        self.assertEqual(p["actions"], [("dev_10", "temp", "19")])     # +1 of the live 18
        parsed = L.parse_dsl("dev_10 temp=+1", REG.names)
        self.assertEqual(L.plan(REG, parsed, ["теплее"])["dropped"], ["dev_10 temp=+1"])   # i18n: no live value: not trusted

    def test_ir_press_runs_when_named(self):
        p, got = self.acts("сделай телевизор в спальне погромче")  # i18n
        self.assertEqual(got, [("dev_108", "ir_vol_up", "press")])
        self.assertEqual(p["ask"], [])

    def test_a_device_the_phrase_does_not_name_asks(self):
        # i18n: the model knows the garden light is "подсветка в саду", the house has no such word for it: a guess, so a question
        parsed, p = run("включи подсветку в саду", self.ol)
        self.assertEqual(p["actions"], [("Led Garden", "state_l1", "on")])
        self.assertEqual(p["ask"], ["guess:Led Garden"])

    def test_the_lock_always_asks(self):
        p, got = self.acts("запри дверь во двор")  # i18n
        self.assertEqual(got, [("dev_125", "locked", "on")])
        self.assertEqual(p["ask"], ["risky:dev_125"])

    def test_guessed_except_asks(self):
        parsed, p = run("выключи всё кроме ночника", self.ol)  # i18n
        self.assertEqual(parsed["except"], ["AC light"])
        self.assertIn("guess:AC light", p["ask"])
        self.assertTrue(p["actions"] and all(v == "off" for _, _, v in p["actions"]))
        self.assertNotIn("AC light", [a[0] for a in p["actions"]])
        self.assertNotIn("dev_125", [a[0] for a in p["actions"]])        # a lock is never switched by all_off
        self.assertIn("Выключить всё, кроме AC light?", L.ask_text(REG, p, parsed))  # i18n

    def test_named_except_does_not_ask(self):
        words = ["выключи", "всё", "кроме", "бойлера"]  # i18n
        parsed = L.parse_dsl("all_off except=water_heater", REG.names)
        p = L.plan(REG, parsed, words, confirm_all_off=False)
        self.assertEqual(p["ask"], [])
        p = L.plan(REG, parsed, words, confirm_all_off=True)
        self.assertEqual(p["ask"], ["all_off"])

    def test_unknown_name_is_dropped(self):
        with self.assertRaises(ValueError):
            run("выключи лампу в углу", self.ol)                           # i18n: "угол state=off" is not a name

    def test_none(self):
        for phrase in ("какая сегодня погода", "расскажи анекдот про программистов"):  # i18n
            parsed, p = run(phrase, self.ol)
            self.assertEqual((parsed["action"], p["actions"]), ("none", []))

    def test_the_chat_streams_and_has_no_devices(self):
        Mock.seen.clear()
        got = list(self.ol.chat_stream([{"role": "user", "content": "как дела"}]))  # i18n
        self.assertEqual("".join(got), "Привет!")
        b = Mock.seen[0]
        self.assertEqual(b["messages"][0]["content"], L.CHAT_SYSTEM)
        self.assertNotIn(REG.slug["Кухня"] + " | ", L.CHAT_SYSTEM)                     # i18n: the chat does not know a single device
        self.assertEqual(b["options"]["num_ctx"], 4096)                                # the same context as the commands: no reload

    def test_request_shape(self):
        Mock.seen.clear()
        self.ol.ask(REG.system, "включи бойлер")  # i18n
        b = Mock.seen[0]
        self.assertIs(b["keep_alive"], -1 + 0)
        self.assertEqual((b["options"]["num_thread"], b["options"]["temperature"], b["stream"]), (4, 0, False))
        self.assertNotIn("format", b)
        self.assertEqual(b["messages"][0]["content"], REG.system)


class Parts(unittest.TestCase):
    def test_strict_parser(self):
        for bad in ("кухня state", "nope state=on", "кухня state=on; nope x=1", "all_off except=nope", "all_off foo", "кухня state=on extra"):  # i18n
            with self.assertRaises(ValueError, msg=bad):
                L.parse_dsl(bad, REG.names)
        self.assertEqual(L.parse_dsl("кухня state=on; ac temp=24", REG.names)["intents"],  # i18n
                         [{"device": "Кухня", "code": "state", "value": "on"}, {"device": "ac", "code": "temp", "value": "24"}])
        self.assertEqual(L.parse_dsl("none\nextra lines are ignored", REG.names)["action"], "none")

    def test_a_copied_channel_name_is_ignored(self):
        self.assertEqual(L.parse_dsl("led_garden state_l1(1)=on", REG.names)["intents"],
                         [{"device": "Led Garden", "code": "state_l1", "value": "on"}])

    def test_values(self):
        c = REG.dev["ac"]["controls"]
        self.assertEqual(L.check_value(c["temp"], "24"), "24")
        self.assertIsNone(L.check_value(c["temp"], "35"))
        self.assertEqual(L.check_value(c["temp"], "-1", 16), "16")         # clamped at the floor
        self.assertEqual(L.check_value(c["mode"], "COOL"), "cool")
        self.assertIsNone(L.check_value(c["mode"], "boil"))
        self.assertIsNone(L.check_value(c["power"], "maybe"))
        g = REG.dev["Led Garden"]["controls"]
        self.assertEqual(L.check_value(g["brightness_l1"], "50"), 50)
        self.assertIsNone(L.check_value(g["brightness_l1"], "150"))

    def test_bad_part_spoils_the_line(self):
        parsed = L.parse_dsl("кухня state=on; ac mode=boil", REG.names)  # i18n
        p = L.plan(REG, parsed, ["кухне", "кондиционер"])
        self.assertEqual((p["actions"], p["ask"]), ([], []))
        self.assertTrue(p["dropped"])

    def test_confirmation(self):
        self.assertIs(L.confirmation("да, давай"), True)  # i18n
        self.assertIs(L.confirmation("нет"), False)
        self.assertIs(L.confirmation("да нет"), False)  # i18n
        self.assertIsNone(L.confirmation("включи свет"))

    def test_prompt_is_generated_and_stable(self):
        again = L.Registry(load("registry.json"), [tuple(x) for x in load("zone_words.json")])
        self.assertEqual(REG.system, again.system)                         # byte-identical: the model cache is reused
        for did in REG.dev:
            self.assertIn(REG.slug[did] + " | ", REG.system)
        self.assertNotIn("voice", " ".join(REG.dev))                       # satellites are not devices to command

    def test_custom_names_rooms_and_channels_reach_the_model(self):
        self.assertIn("shelf_switch | Угол, Салон |", REG.system)             # i18n: the custom name, not only the id
        self.assertIn("ac_power | Свет в ванной, Ванна |", REG.system)                # a misleading id carries its human name
        self.assertIn("state_left(центральный свет в салоне)=on|off", REG.system)                 # i18n: the channel names of one switch
        self.assertIn("state_right(свет у выхода во двор)=on|off", REG.system)
        self.assertNotIn("state_center", REG.system)                                # a channel named "-" is not connected: hidden
        self.assertNotIn("r1788", REG.system)                                       # a satellite's room id is not a room name

    def test_except_none_means_nothing_excepted(self):
        self.assertEqual(L.parse_dsl("all_off except=none", REG.names), {"action": "all_off", "except": [], "intents": []})

    def test_questions_are_plain(self):
        parsed = L.parse_dsl("dev_125 locked=on; ac_power state=off", REG.names)
        p = L.plan(REG, parsed, ["запри"])  # i18n
        self.assertEqual(L.ask_text(REG, p, parsed), "Запереть Замок во двор, Выключить Свет в ванной?")
        parsed = L.parse_dsl("all_off", REG.names)
        self.assertEqual(L.ask_text(REG, L.plan(REG, parsed, ["всё"]), parsed), "Выключить всё?")  # i18n

    def test_questions_use_human_names(self):
        spoken = {"AC light": {"acc": "верхний свет в салоне", "gen": "верхнего света в салоне"},  # i18n
                  "Led Garden/state_l1": {"acc": "подсветку в саду (первую)"}}
        reg = L.Registry(load("registry.json"), [tuple(x) for x in load("zone_words.json")], None, spoken)
        parsed = L.parse_dsl("all_off except=ac_light", reg.names)
        self.assertEqual(L.ask_text(reg, L.plan(reg, parsed, ["всё"]), parsed), "Выключить всё, кроме верхнего света в салоне?")  # i18n
        parsed = L.parse_dsl("led_garden state_l1=on", reg.names)
        self.assertEqual(L.ask_text(reg, L.plan(reg, parsed, ["свет"]), parsed), "Включить подсветку в саду (первую)?")  # i18n
        parsed = L.parse_dsl("ac_light state=on", reg.names)                                  # no "gen" needed here: acc is used
        self.assertEqual(L.ask_text(reg, L.plan(reg, parsed, ["свет"]), parsed), "Включить верхний свет в салоне?")  # i18n
        parsed = L.parse_dsl("shelf_switch state=on", reg.names)                              # no glossary entry: the house's own name
        self.assertEqual(L.ask_text(reg, L.plan(reg, parsed, ["свет"]), parsed), "Включить Угол?")  # i18n

    def test_a_confirmed_guess_is_learned(self):
        store = {}
        L.learn(store, "включи подсветку в саду", [("Led Garden", "state_l1", "on")])  # i18n
        self.assertEqual(L.learned(store, REG, "включи подсветку в саду"), [("Led Garden", "state_l1", "on")])
        self.assertIsNone(L.learned(store, REG, "другая фраза"))  # i18n
        L.learn(store, "запри", [("dev_125", "locked", "on")])
        self.assertIsNone(L.learned(store, REG, "запри"))                     # i18n: a lock is never run from memory
        L.learn(store, "старое", [("gone device", "state", "on")])
        self.assertIsNone(L.learned(store, REG, "старое"))                    # i18n: a device that went away is forgotten

    def test_the_ask_forms_of_the_model(self):
        p = lambda line: L.parse_dsl(line, REG.names, REG.qnames, REG.room_slugs)                   # noqa: E731
        self.assertEqual(p("ask who_home")["query"], {"type": "who_home"})
        self.assertEqual(p("ask all lights")["query"], {"type": "all", "kind": "lights"})
        self.assertEqual(p("ask all battery")["query"], {"type": "all", "kind": "battery"})
        self.assertEqual(p("ask кухня state")["query"], {"type": "device", "ids": ["Кухня"], "code": "state"})  # i18n
        self.assertEqual(p("ask кухня")["query"], {"type": "device", "ids": ["Кухня"], "code": None})
        self.assertEqual(p("ask room=спальня")["query"], {"type": "room", "room": "Спальня"})  # i18n
        self.assertEqual(p("ask bedroom_clima temperature")["query"]["ids"], ["bedroom_clima"])      # a sensor: readable, never commandable
        self.assertEqual(p("ask кухня room=Салон")["query"]["ids"], ["Кухня"])                       # i18n: an extra word of the model is ignored
        self.assertEqual(p("ask кухня state(1)")["query"]["code"], "state")
        for bad in ("ask", "ask all gold", "ask room=чердак", "ask nobody", "ask who_home now now", "ask room=салон now"):  # i18n
            with self.assertRaises(ValueError, msg=bad):
                p(bad)
        with self.assertRaises(ValueError):
            L.parse_dsl("bedroom_clima temperature=20", REG.names)                                 # a sensor is not in the command names
        plan = L.plan(REG, p("ask who_home"), ["кто", "дома"])  # i18n
        self.assertEqual((plan["actions"], plan["ask"], plan["query"]), ([], [], {"type": "who_home"}))   # a question needs no question back
        self.assertIn("ask who_home", REG.system)
        self.assertIn("Rooms:", REG.system)
        self.assertIn("bedroom_clima | ", REG.system)                                                # a sensor-only device is listed as readable

    def test_the_chat_never_answers_about_the_house(self):
        for text in ("кто дома", "дома ли я", "я дома?", "сколько градусов", "какая температура в спальне", "горит ли свет", "включи свет"):  # i18n
            self.assertIn(L.chat_gate(text), (L.HOUSE_REFUSAL, "Понятно."), text)
        self.assertEqual(L.chat_gate("я дома?"), "Понятно.")  # i18n
        self.assertIsNone(L.chat_gate("расскажи что-нибудь интересное"))
        self.assertIsNone(L.chat_gate("как дела"))  # i18n
        self.assertEqual(L.chat_filter("Да, ты дома, я чувствую твою энергию"), L.HOUSE_REFUSAL)
        self.assertEqual(L.chat_filter("Сейчас в комнате тепло"), L.HOUSE_REFUSAL)  # i18n
        self.assertEqual(L.chat_filter("Не знаю, у меня нет доступа к погоде."), "Не знаю, у меня нет доступа к погоде.")
        self.assertIn("не знаю", L.CHAT_SYSTEM.lower())  # i18n

    def test_timeout_and_warm_up(self):
        ol = L.Ollama("http://127.0.0.1:9", "m", timeout=0.5)             # nothing listens
        self.assertIsNone(ol.warm_up("x"))
        with self.assertRaises(Exception):
            ol.ask("x", "y")


if __name__ == "__main__":
    unittest.main()


class Thinking(unittest.TestCase):
    def test_the_thinking_is_never_shown(self):
        self.assertEqual(L.strip_think("<think>U and I...</think>\nU = I*R."), "U = I*R.")
        self.assertEqual(L.strip_think("U = I*R."), "U = I*R.")
        self.assertEqual(L.strip_think("<think>cut off before the end"), "")

    def test_the_chat_prompt_answers_only_what_was_asked_in_its_language(self):
        self.assertIn("на языке вопроса", L.CHAT_SYSTEM)  # i18n
        self.assertIn("только то, что спросили", L.CHAT_SYSTEM)
        self.assertIn("не знаю", L.CHAT_SYSTEM.lower())  # i18n


class ClockClassified(unittest.TestCase):
    def test_the_model_may_answer_a_clock_question_with_an_ask_line(self):
        self.assertEqual(L.parse_dsl("ask clock time", {})["query"], {"type": "clock", "what": "time"})
        self.assertEqual(L.parse_dsl("ask clock weekday", {})["query"], {"type": "clock", "what": "weekday"})
        with self.assertRaises(ValueError):
            L.parse_dsl("ask clock century", {})


class EveryWording(unittest.TestCase):
    """Michael's list: whatever the wording, the model's one line lands on the intent; the answer is built from the house, never by the model."""
    def intent(self, line):
        parsed = L.parse_dsl(line, REG.names, REG.qnames, REG.room_slugs)
        if parsed["action"] == "ask":
            return parsed["query"]["type"]
        return parsed["action"] if parsed["action"] == "none" else "command"

    def test_each_phrase_reaches_its_intent(self):
        sw, room = REG.slug["Кухня"], next(iter(REG.room_slugs))  # i18n
        code = next(c for c, v in REG.dev[REG.names[sw]]["controls"].items() if v["kind"] == "switch")
        said = {"кто в доме": ("ask who_home", "who_home"), "кто дома": ("ask who_home", "who_home"),  # i18n
                "есть кто дома?": ("ask who_home", "who_home"), "дома кто-нибудь есть": ("ask who_home", "who_home"),
                "в зале тепло?": ("ask room=%s temp" % room, "room"), "горит ли свет на кухне": ("ask %s" % sw, "device"),  # i18n
                "выключи свет в коридоре": ("%s %s=off" % (sw, code), "command"), "напомни закон Ома": ("none", "none"),
                "почему включился свет": ("ask why %s" % sw, "why"), "какое сегодня число": ("ask clock date", "clock")}  # i18n
        for phrase, (line, want) in said.items():
            self.assertEqual(self.intent(line), want, phrase)

    def test_no_prompt_example_sends_a_house_question_to_the_chat(self):
        import re as _re
        import voice_state as S
        nones = _re.findall(r"\n([^\n>]+) -> none", REG.system)
        self.assertTrue(nones)
        for p in nones:
            self.assertIsNone(S.clock_of(p), p)
            self.assertIsNone(S.quick(p, list(REG.room_slugs.values()), lambda w: []), p)
