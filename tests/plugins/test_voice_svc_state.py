"""State questions: an answer per kind of device, from LIVE state, in human words; stale and offline data say so."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugins", "tools", "voice", "service", "vad"))
import voice_state as S  # noqa: E402

NOW = 1_000_000.0
D = [
    {"id": "kitchen", "name": "Кухня", "category": "switch", "controls": [{"kind": "switch", "code": "state", "label": "State"}]},  # i18n
    {"id": "hall", "name": "Hall main", "category": "switch", "controls": [
        {"kind": "switch", "code": "state_left", "label": "Салон"}, {"kind": "switch", "code": "state_right", "label": "Дверь"}]},  # i18n
    {"id": "garden", "name": "Led Garden", "category": "switch", "controls": [
        {"kind": "switch", "code": "state_l1", "label": "1"}, {"kind": "bright", "code": "brightness_l1", "label": "Brightness 1"}]},
    {"id": "dev_125", "name": "Замок во двор", "category": "switch", "controls": [  # i18n
        {"kind": "switch", "code": "locked", "label": "Lock"}, {"kind": "sensor_text", "code": "door", "label": "Door", "ro": True},
        {"kind": "sensor", "code": "battery", "label": "Battery", "unit": "%", "ro": True}]},
    {"id": "ac", "name": "Кондей (салон)", "category": "climate", "controls": [  # i18n
        {"kind": "switch", "code": "power", "label": "Power"},
        {"kind": "setting_enum", "code": "temp", "label": "Temperature", "range": ["16", "24"]},
        {"kind": "setting_enum", "code": "mode", "label": "Mode", "range": ["cool", "heat"]},
        {"kind": "sensor", "code": "room_temp", "label": "Room", "unit": "°C", "ro": True}]},  # i18n
    {"id": "dev_88", "name": "LG TV", "category": "tv", "controls": [
        {"kind": "switch", "code": "power", "label": "Power"}, {"kind": "bright", "code": "volume", "label": "Volume"},
        {"kind": "setting_enum", "code": "input", "label": "Input", "range": ["HDMI 2", "PC"]}]},
    {"id": "bed_clima", "name": "Спальня датчик", "category": "sensor", "controls": [  # i18n
        {"kind": "sensor", "code": "temperature", "label": "T", "unit": "°C"}, {"kind": "sensor", "code": "humidity", "label": "H", "unit": "%"},
        {"kind": "sensor", "code": "wifi_signal", "label": "wifi", "unit": "dBm"}]},
    {"id": "motion", "name": "Салон датчик", "category": "sensor", "controls": [  # i18n
        {"kind": "sensor", "code": "occupancy", "label": "Occ"}, {"kind": "sensor", "code": "illuminance", "label": "Lux", "unit": "lx"},
        {"kind": "sensor", "code": "battery", "label": "Battery", "unit": "%"}]},
    {"id": "shower", "name": "Душ", "category": "indicator", "controls": [{"kind": "sensor", "code": "active", "label": "Active"},  # i18n
                                                                          {"kind": "sensor", "code": "humidity", "label": "H", "unit": "%"}]},
    {"id": "person_michael", "name": "michael", "category": "indicator", "controls": [{"kind": "sensor", "code": "home", "label": "Home"}]},
    {"id": "person_anna", "name": "anna", "category": "indicator", "controls": [{"kind": "sensor", "code": "home", "label": "Home"}]},
    {"id": "old_sensor", "name": "Старый датчик", "category": "sensor", "controls": [{"kind": "sensor", "code": "temperature", "label": "T", "unit": "°C"},  # i18n
                                                                                     {"kind": "sensor", "code": "battery", "label": "B", "unit": "%"}]},
    {"id": "lost", "name": "Потерянный", "category": "switch", "controls": [{"kind": "switch", "code": "state", "label": "S"}]},  # i18n
]
ROOMS = {"kitchen": "Кухня", "hall": "Салон", "ac": "Салон", "motion": "Салон", "bed_clima": "Спальня", "garden": "Двор", "dev_125": "Двор"}  # i18n
ST = {
    "kitchen": {"online": True, "values": {"state": True}, "last_seen": NOW - 5},
    "hall": {"online": True, "values": {"state_left": True, "state_right": False}, "last_seen": NOW - 5},
    "garden": {"online": True, "values": {"state_l1": True, "brightness_l1": 40}, "last_seen": NOW - 5},
    "dev_125": {"online": True, "values": {"locked": True, "door": "closed", "battery": 78}, "last_seen": NOW - 5},
    "ac": {"online": True, "values": {"power": True, "temp": 24, "mode": "cool", "room_temp": 27.46}, "last_seen": NOW - 5},
    "dev_88": {"online": True, "values": {"power": False, "volume": 12, "input": "HDMI 2"}, "last_seen": NOW - 5},
    "bed_clima": {"online": True, "values": {"temperature": 22.04, "humidity": 51.2, "wifi_signal": -60}, "last_seen": NOW - 60},
    "motion": {"online": True, "values": {"occupancy": True, "illuminance": 130, "battery": 90}, "last_seen": NOW - 30},
    "shower": {"online": True, "values": {"active": True, "humidity": 80}, "last_seen": NOW - 5},
    "person_michael": {"online": True, "values": {"home": False}, "last_seen": NOW - 5},
    "person_anna": {"online": True, "values": {"home": True}, "last_seen": NOW - 5},
    "old_sensor": {"online": True, "values": {"temperature": 19, "battery": 11}, "last_seen": NOW - 3 * 86400},
    "lost": {"online": False, "values": {}, "last_seen": NOW - 1800},
}
NAMES = {"person_michael": "Миша", "person_anna": "Аня"}  # i18n


SENSORS = {"спальня": {"temperature": {"order": ["bed_clima/temperature"]}, "humidity": {"order": ["bed_clima/humidity"]}}}    # i18n: GET /api/rooms


def state(spoken=NAMES):
    return S.State(D, ROOMS, lambda did: ST.get(did), (lambda did: spoken.get(did)), now=lambda: NOW, sensors=SENSORS)


class Answers(unittest.TestCase):
    def test_switches_and_channels(self):
        s = state()
        self.assertEqual(s.device("kitchen"), "Кухня: включено")  # i18n
        self.assertEqual(s.device("hall", "state_right"), "Hall main: выключено")
        self.assertIn("включено, выключено", s.device("hall"))  # i18n

    def test_brightness(self):
        self.assertEqual(state().device("garden"), "Led Garden: включено, яркость 40%")  # i18n

    def test_lock_door_and_battery(self):
        self.assertEqual(state().device("dev_125"), "Замок во двор: заперт, дверь закрыта, батарея 78%")  # i18n

    def test_climate(self):
        a = state().device("ac")
        self.assertIn("включено", a)  # i18n
        self.assertIn("задана температура 24°", a)
        self.assertIn("режим охлаждение", a)  # i18n
        self.assertIn("в комнате 27°", a)                                         # whole degrees, as every screen shows them

    def test_tv(self):
        self.assertEqual(state().device("dev_88"), "LG TV: выключено, громкость 12%, вход HDMI 2")  # i18n

    def test_sensors_without_noise(self):
        a = state().device("bed_clima")
        self.assertEqual(a, "Спальня датчик: температура 22°, влажность 51%")     # i18n: no wifi signal
        m = state().device("motion")
        self.assertIn("есть движение", m)  # i18n
        self.assertIn("освещённость 130 lx", m)

    def test_shower(self):
        self.assertIn("идёт", state().device("shower"))  # i18n

    def test_stale_and_offline_say_so(self):
        self.assertEqual(state().device("old_sensor"), "Старый датчик: нет данных 3 дн")  # i18n
        self.assertEqual(state().device("lost"), "Потерянный: нет связи, последние данные 30 мин назад")

    def test_who_home(self):
        self.assertEqual(state().who_home(), "Дома: Аня")  # i18n
        ST["person_anna"]["values"]["home"] = False
        try:
            self.assertEqual(state().who_home(), "Никого нет дома")  # i18n
        finally:
            ST["person_anna"]["values"]["home"] = True
        self.assertEqual(S.State([], {}, lambda d: None).who_home(), "Не знаю: нет телефонов с отметкой «дома»")  # i18n

    def test_room(self):
        a = state().room("Салон")  # i18n
        self.assertIn("включено: Hall main (Салон), Кондей (салон)", a)
        self.assertEqual(state().room("Спальня"), "В спальне 22 градуса, влажность 51 процент; ничего не включено")  # i18n
        self.assertIn("нет устройств", state().room("Чердак"))

    def test_a_room_answers_with_the_first_live_source_of_the_houses_order_never_a_stale_one(self):
        """The house sends each room's order (a hand choice alone, or auto without a module's board); Ask reads what the tab shows."""
        board = {"id": "board", "name": "Радар", "category": "sensor", "controls": [{"kind": "sensor", "code": "temperature", "label": "T", "advanced": True}]}  # i18n
        exact = {"id": "exact", "name": "Датчик", "category": "sensor", "controls": [{"kind": "sensor", "code": "temperature", "label": "T"}]}
        live = lambda v, seen=5, **kw: {"online": True, "values": {"temperature": v}, "last_seen": NOW - seen, **kw}      # noqa: E731

        def room(order, st):
            sens = {"ванна": {"temperature": {"order": order}}}  # i18n
            return S.State([board, exact], {"board": "Ванна", "exact": "Ванна"}, lambda d: st.get(d, {}), now=lambda: NOW, sensors=sens).room("Ванна", "temp")
        both = {"board": live(35.3), "exact": live(24.0)}
        self.assertEqual(room(["exact/temperature"], both), "В ванной 24 градуса")                       # i18n: auto: the board is not in the order
        self.assertEqual(room([], both), "В ванной нет данных о температуре")                              # only a board in the room: no reading
        self.assertEqual(room(["board/temperature"], both), "В ванной 35 градусов")                       # i18n: the owner chose the board by hand
        self.assertEqual(room(["exact/temperature", "board/temperature"], {**both, "exact": live(24.0, stale=True)}), "В ванной 35 градусов")   # a stale one is skipped
        self.assertEqual(room(["exact/temperature"], {**both, "exact": {"online": False, "values": {"temperature": 24.0}}}),
                         "В ванной нет данных о температуре")                                               # i18n: a silent choice: never another one

    def test_a_room_question_is_answered_for_what_was_asked_only(self):
        """The 20.10 case: "сколько градусов в спальне" must say the temperature, not what is on."""  # i18n
        s = state()
        self.assertEqual(S.answer(s, {"type": "room", "room": "Спальня", "aspect": "temp"}), "В спальне 22 градуса")  # i18n
        self.assertEqual(S.answer(s, {"type": "room", "room": "Спальня", "aspect": "humidity"}), "В спальне влажность 51 процент")
        self.assertEqual(S.answer(s, {"type": "room", "room": "Спальня", "aspect": "on"}), "В спальне ничего не включено")  # i18n
        self.assertIn("включено: Hall main", S.answer(s, {"type": "room", "room": "Салон", "aspect": "on"}))
        self.assertNotIn("°", S.answer(s, {"type": "room", "room": "Салон"}))                 # i18n: a spoken answer has words, not signs

    def test_the_aspect_comes_only_from_heard_words(self):
        """20.10: 'какая с ванной' (влажность not heard) made the model guess 'temp' and the house said the temperature."""  # i18n
        self.assertEqual(S.fix_aspect({"type": "room", "room": "Ванна", "aspect": "temp"}, "какая с ванной"), {"type": "room", "room": "Ванна"})
        self.assertEqual(S.fix_aspect({"type": "room", "room": "Ванна"}, "какая влажность в ванной"), {"type": "room", "room": "Ванна", "aspect": "humidity"})  # i18n
        self.assertEqual(S.fix_aspect({"type": "room", "room": "Ванна", "aspect": "on"}, "холодно в ванной")["aspect"], "temp")
        self.assertEqual(S.fix_aspect({"type": "who_home"}, "кто дома"), {"type": "who_home"})  # i18n
        self.assertEqual([S.aspect_of(t) for t in ("сырость в спальне", "жарко ли в салоне", "что сейчас горит")], ["humidity", "temp", "on"])

    def test_units_and_places_decline(self):
        self.assertEqual([S.plural(v, "градус", "градуса", "градусов") for v in (1, 2, 5, 11, 21, 24, 27.5)],  # i18n
                         ["градус", "градуса", "градусов", "градусов", "градус", "градуса", "градуса"])
        self.assertEqual([S.where(r) for r in ("Спальня", "Кухня", "Салон", "Ванна", "Гостиная", "Прихожая", "Коридор", "Детская", "Двор")],  # i18n
                         ["в спальне", "на кухне", "в салоне", "в ванной", "в гостиной", "в прихожей", "в коридоре", "в детской", "во дворе"])
        self.assertEqual(S.where("Вторая спальня"), "в комнате «Вторая спальня»")  # i18n

    def test_a_channel_with_a_spoken_name_is_called_by_it(self):
        spoken = {"hall/state_left": "центральный свет в салоне"}  # i18n
        s = S.State(D, ROOMS, lambda did: ST.get(did), lambda did: None, now=lambda: NOW, chan=lambda did, code: spoken.get("%s/%s" % (did, code)))
        self.assertIn("центральный свет в салоне", s.room("Салон"))  # i18n
        self.assertNotIn("Hall main", s.room("Салон"))
        self.assertIn("центральный свет в салоне", s.all("lights"))  # i18n
        self.assertIn("Led Garden", s.all("lights"))                                      # no spoken name: the device and its channel label

    def test_lights_are_not_sockets(self):
        spoken = {"hall/state_right": "розетка у входа", "hall/state_left": "центральный свет"}  # i18n
        s = S.State(D, ROOMS, lambda did: ST.get(did), lambda did: None, now=lambda: NOW, chan=lambda did, code: spoken.get("%s/%s" % (did, code)))
        ST["hall"]["values"]["state_right"] = True
        try:
            self.assertNotIn("розетка", s.all("lights"))  # i18n
            self.assertIn("розетка у входа", s.all("on"))                               # but it is on
        finally:
            ST["hall"]["values"]["state_right"] = False

    def test_all_kinds(self):
        s = state()
        self.assertIn("Свет включён: Кухня, Hall main (Салон), Led Garden", s.all("lights"))  # i18n
        self.assertNotIn("Замок", s.all("lights"))
        self.assertEqual(s.all("open"), "Всё закрыто")  # i18n
        ST["dev_125"]["values"]["locked"] = False
        try:
            self.assertEqual(state().all("open"), "Открыто: Замок во двор")  # i18n
        finally:
            ST["dev_125"]["values"]["locked"] = True
        b = s.all("battery")
        self.assertTrue(b.startswith("Батарейки: Старый датчик 11%"))                # i18n: the lowest first
        self.assertIn("Замок во двор 78%", b)
        self.assertEqual(s.all("online"), "Не в сети: Потерянный")  # i18n

    def test_answer_dispatch(self):
        s = state()
        self.assertEqual(S.answer(s, {"type": "who_home"}), "Дома: Аня")  # i18n
        self.assertEqual(S.answer(s, {"type": "device", "ids": ["kitchen", "dev_88"], "code": None}), "Кухня: включено; LG TV: выключено, громкость 12%, вход HDMI 2")
        self.assertEqual(S.answer(s, {"type": "device", "ids": ["nope"]}), "Нет такого устройства")  # i18n


class Phrases(unittest.TestCase):
    ROOMS = ["Салон", "Спальня", "Ванна", "Двор"]  # i18n
    FIND = staticmethod(lambda words: [i for i, k in (("kitchen", "кухн"), ("garden", "подсвет"), ("dev_88", "телевиз"), ("dev_125", "замок")) if any(w.startswith(k) for w in words)])

    def q(self, t):
        return S.quick(t, self.ROOMS, self.FIND)

    def test_questions_are_recognized_before_any_command_rule(self):
        for t in ("включён ли свет на кухне", "выключен ли телевизор", "кто дома", "какая температура в спальне", "горит ли свет", "сколько градусов в салоне"):  # i18n
            self.assertTrue(S.is_question(t), t)
        for t in ("включи свет на кухне", "выключи телевизор", "сделай 24 градуса"):  # i18n
            self.assertFalse(S.is_question(t), t)

    def test_a_state_request_without_a_question_word_is_a_query_too(self):
        for t in ("батарейки", "что включено", "кто дома", "температура в спальне", "что сейчас горит"):  # i18n
            self.assertTrue(S.is_query(t), t)
        for t in ("включи батарейки", "включи свет", "выключи всё"):  # i18n
            self.assertFalse(S.is_query(t), t)

    def test_a_device_with_only_commands(self):
        d = D + [{"id": "ir_tv", "name": "Телевизор (спальня)", "category": "ir", "controls": [{"kind": "ir_press", "code": "ir_power_on", "label": "On"}]}]  # i18n
        st = dict(ST, ir_tv={"online": True, "values": {"x": 1}, "last_seen": NOW})
        s = S.State(d, ROOMS, lambda did: st.get(did), lambda did: None, now=lambda: NOW)
        self.assertEqual(s.device("ir_tv"), "Телевизор (спальня): состояния не знаю, это только команды")  # i18n

    def test_templates(self):
        self.assertEqual(self.q("кто дома"), {"type": "who_home"})  # i18n
        self.assertEqual(self.q("дома ли я"), {"type": "who_home"})
        self.assertEqual(self.q("какая температура в спальне"), {"type": "room", "room": "Спальня", "aspect": "temp"})  # i18n
        self.assertEqual(self.q("сколько градусов в салоне"), {"type": "room", "room": "Салон", "aspect": "temp"})
        self.assertEqual(self.q("какая влажность в ванне"), {"type": "room", "room": "Ванна", "aspect": "humidity"})  # i18n
        self.assertEqual(self.q("что включено в салоне"), {"type": "room", "room": "Салон", "aspect": "on"})
        self.assertEqual(self.q("включён ли телевизор"), {"type": "device", "ids": ["dev_88"], "code": None})  # i18n
        self.assertEqual(self.q("включен ли свет на кухне"), {"type": "device", "ids": ["kitchen"], "code": None})
        self.assertEqual(self.q("у каких датчиков садятся батарейки"), {"type": "all", "kind": "battery"})  # i18n
        self.assertEqual(self.q("что сейчас включено"), {"type": "state", "state": "on"})
        self.assertEqual(self.q("что не в сети"), {"type": "all", "kind": "online"})  # i18n
        self.assertEqual(self.q("что открыто"), {"type": "all", "kind": "open"})
        self.assertEqual(self.q("какая сегодня погода")["type"], "weather")                 # i18n: the forecast answers it
        self.assertIsNone(self.q("какой курс доллара"))                                  # not about the house: the model decides


def pair(phrase, en=False, rooms=("Салон", "Спальня", "Кухня", "Двор")):  # i18n
    """(query, full text, spoken text) of a phrase through the fast templates and the live-state answers, in the language of the phrase."""
    q = S.quick(phrase, list(rooms), Phrases.FIND)
    st = S.State(D, ROOMS, lambda did: ST.get(did), (lambda did: NAMES.get(did)), now=lambda: NOW, lang="en" if en else "ru", sensors=SENSORS)
    return (q,) + (S.answer_pair(st, q) if q else (None, None))


class EnglishAndLists(unittest.TestCase):
    def test_the_20_10_english_question_gets_an_english_temperature_only(self):
        q, full, spoken = pair("what temperature in the bedroom?", True)
        self.assertEqual(q, {"type": "room", "room": "Спальня", "aspect": "temp"})  # i18n
        self.assertEqual((full, spoken), ("It is 22° in the bedroom", "It is 22 degrees in the bedroom"))     # shown short, said in words
        self.assertEqual(pair("how warm is it in the bedroom", True)[1], "It is 22° in the bedroom")  # i18n
        self.assertEqual(pair("what is the humidity in the bedroom", True)[1], "Humidity in the bedroom is 51 percent")
        self.assertEqual(pair("какая температура в спальне?")[1:], ("В спальне 22°", "В спальне 22 градуса"))  # i18n

    def test_aspect_words_in_both_languages(self):
        for text, aspect in (("temperature", "temp"), ("how many degrees", "temp"), ("is it hot in here", "temp"), ("is it cold", "temp"),
                             ("humidity", "humidity"), ("is it humid", "humidity"), ("влажность", "humidity"), ("жарко", "temp"),  # i18n
                             ("what is on", "on"), ("what's on", "on"), ("что включено", "on"), ("what is off", "off"), ("что выключено", "off")):
            self.assertEqual(S.aspect_of(text), aspect, text)

    def test_what_is_on_and_off(self):
        self.assertEqual(pair("что включено в салоне")[0], {"type": "room", "room": "Салон", "aspect": "on"})  # i18n
        self.assertEqual(pair("что выключено в спальне")[1], "В спальне ничего не выключено")
        self.assertEqual(pair("what is on in the living room", True)[1], "On in the living room: Hall main, ac")
        self.assertEqual(pair("what is off in the kitchen", True)[1], "Nothing is off in the kitchen")
        q, full, spoken = pair("что включено")                                              # i18n: the whole house: by room
        self.assertEqual(q, {"type": "state", "state": "on"})
        self.assertEqual(full, "Включено: Двор - Led Garden (1); Кухня - Кухня; Салон - Hall main (Салон), Кондей (салон)")  # i18n
        self.assertEqual(spoken, "Включено: Led Garden (1), Кухня, Hall main (Салон), Кондей (салон)")
        self.assertEqual(pair("что выключено в доме")[1], "Выключено: Салон - Hall main (Дверь); без комнаты - LG TV")  # i18n
        self.assertEqual(pair("what is on", True)[1], "On: yard - Led Garden (1); kitchen - kitchen; living room - Hall main, ac")
        self.assertEqual(pair("what is off in the house", True)[0], {"type": "state", "state": "off"})

    def test_nothing_on_says_so(self):
        st = S.State(D, ROOMS, lambda did: {"online": True, "values": {"state": False}, "last_seen": NOW}, lambda did: None, now=lambda: NOW)
        self.assertEqual(st.house_state("on")[0], "Ничего не включено")  # i18n
        self.assertEqual(S.State(D, ROOMS, lambda did: {"online": True, "values": {"state": False}, "last_seen": NOW}, lambda did: None,
                                 now=lambda: NOW, lang="en").house_state("on")[0], "Nothing is on")

    def test_list_the_devices(self):
        q, full, spoken = pair("перечисли устройства в салоне")  # i18n
        self.assertEqual(q, {"type": "list", "room": "Салон"})
        self.assertEqual(full, "Устройства в салоне: Hall main: включено, Кондей (салон): включено, Салон датчик: есть движение")  # i18n
        self.assertEqual(spoken, "Устройства в салоне: Hall main, Кондей (салон), Салон датчик")
        self.assertEqual(pair("какие устройства в спальне")[0], {"type": "list", "room": "Спальня"})  # i18n
        self.assertEqual(pair("list the devices in the kitchen", True)[1:], ("Devices in the kitchen: kitchen: on", "Devices in the kitchen: kitchen"))
        self.assertEqual(pair("list the devices", True)[0], {"type": "list"})
        self.assertEqual(pair("which devices are in the bedroom", True)[0], {"type": "list", "room": "Спальня"})  # i18n
        self.assertTrue(pair("перечисли устройства")[1].startswith("Устройства: Двор - "))

    def test_a_long_list_is_cut_when_read_aloud_but_complete_in_text(self):
        many = [{"id": "d%d" % i, "name": "Lamp %d" % i, "category": "switch", "controls": [{"kind": "switch", "code": "state", "label": "S"}]} for i in range(10)]
        st = S.State(many, {}, lambda did: {"online": True, "values": {"state": True}, "last_seen": NOW}, lambda did: None, now=lambda: NOW)
        full, spoken = st.list_devices()
        self.assertEqual(full.count("Lamp"), 10)
        self.assertEqual(spoken, "Устройства: Lamp 0, Lamp 1, Lamp 2, Lamp 3, Lamp 4, Lamp 5 и ещё 4")  # i18n
        self.assertTrue(S.State(many, {}, lambda did: None, lambda did: None, now=lambda: NOW, lang="en").list_devices()[1].endswith("and 4 more"))

    def test_only_registry_devices_are_listed(self):
        self.assertNotIn("Чердак", pair("перечисли устройства")[1])  # i18n
        self.assertEqual(pair("list the devices in the attic", True)[0], {"type": "list"})            # an unknown room is no room: the house list

    def test_english_questions_and_commands(self):
        self.assertTrue(S.is_query("what temperature in the bedroom?") and S.is_query("is the light on") and S.is_query("list the devices"))
        self.assertTrue(S.is_query("перечисли устройства") and S.is_query("что выключено"))  # i18n
        self.assertFalse(S.is_query("turn on the kitchen light") or S.is_query("включи свет"))


if __name__ == "__main__":
    unittest.main()

import time  # noqa: E402


class ClockAndFeel(unittest.TestCase):
    """The house clock and how a room feels are answered from the house, never by a model."""
    NOW = time.mktime((2026, 10, 27, 10, 3, 0, 0, 0, -1))      # a Tuesday, local time

    def test_the_clock_intents(self):
        rooms = ["Спальня", "Кухня"]  # i18n
        for text, what in (("сколько времени", "time"), ("который час", "time"), ("какое сегодня число", "date"),
                           ("какой сегодня день", "weekday"), ("what time is it", "time"), ("what day is it today", "weekday")):  # i18n
            self.assertTrue(S.is_query(text), text)
            self.assertEqual(S.quick(text, rooms, lambda w: []), {"type": "clock", "what": what}, text)
        self.assertEqual(S.clock("time", False, self.NOW), "Сейчас 10:03.")  # i18n
        self.assertEqual(S.clock("date", False, self.NOW), "Сегодня 27 октября.")
        self.assertEqual(S.clock("weekday", False, self.NOW), "Сегодня вторник, 27 октября.")  # i18n
        self.assertEqual(S.clock("weekday", True, self.NOW), "Today is Tuesday, 27 October.")

    def test_a_room_that_feels_cold_is_a_question_of_its_temperature(self):
        self.assertTrue(S.is_query("в спальне холодно"))  # i18n
        self.assertEqual(S.quick("в спальне холодно", ["Спальня"], lambda w: []), {"type": "room", "room": "Спальня", "aspect": "temp"})
        self.assertEqual(S.quick("кто в доме", ["Спальня"], lambda w: []), {"type": "who_home"})  # i18n
        for text in ("сделай теплее", "включи свет", "напомни закон ома"):
            self.assertFalse(S.is_query(text), text)


class WhyItChanged(unittest.TestCase):
    def test_the_latest_rule_that_acted_on_the_device(self):
        ev = [{"ts": time.mktime((2026, 10, 27, 10, 5, 0, 0, 0, -1)), "device": "dev_17", "name": "Радар в ванной", "code": "presence",  # i18n
               "gesture": "true", "target": "bath_light", "value": {"state": "ON"}},
              {"ts": 1.0, "device": "x", "name": "X", "code": "c", "gesture": "g", "target": "other", "value": {}}]
        self.assertEqual(S.why_answer(ev, ["bath_light"], "свет в ванной"), "В 10:05 радар в ванной заметил человека, и правило включило свет в ванной.")  # i18n
        off = [{"ts": ev[0]["ts"], "name": "Радар в ванной", "room": "Ванна", "code": "presence", "gesture": "false", "target": "bath_light",
                "value": {"state": "OFF"}}]
        self.assertEqual(S.why_answer(off, ["bath_light"], "свет"), "В 10:05 в ванной никого не стало, и правило выключило свет.")  # i18n
        self.assertNotRegex(S.why_answer(ev, ["bath_light"], "свет"), "[=>]|presence")
        self.assertIn("не трогали", S.why_answer(ev, ["nobody"], "бойлер"))  # i18n


class WhyTheWay(unittest.TestCase):
    def test_turned_on_finds_the_rule_that_turned_it_on(self):
        ev = [{"ts": 2.0, "name": "Радар", "code": "presence", "gesture": "false", "target": "l", "value": {"state": "OFF"}},  # i18n
              {"ts": 1.0, "name": "Радар", "code": "presence", "gesture": "true", "target": "l", "value": {"state": "ON"}}]
        self.assertEqual(S.why_of("почему включился свет"), "on")  # i18n
        self.assertEqual(S.why_of("почему погас свет"), "off")
        self.assertIn("заметил человека, и правило включило", S.why_answer(ev, ["l"], "свет", want="on"))  # i18n
        self.assertIn("перестал видеть людей, и правило выключило", S.why_answer(ev, ["l"], "свет", want="off"))
        self.assertIn("выключило", S.why_answer(ev, ["l"], "свет"))  # i18n


class ShownShort(unittest.TestCase):
    def test_a_temperature_is_shown_with_the_sign_and_said_in_words(self):
        self.assertEqual(S.screen("В салоне 27,9 градуса"), "В салоне 27,9°")  # i18n
        self.assertEqual(S.screen("В спальне 22 градуса, влажность 51 процент"), "В спальне 22°, влажность 51 процент")
        self.assertEqual(S.screen("In the bedroom 21 degrees"), "In the bedroom 21°")  # i18n
