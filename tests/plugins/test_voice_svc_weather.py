"""Weather questions, Russian and English: classified by words, answered from the forecast only, no data -> says so."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugins", "tools", "voice", "service", "vad"))
import voice_llm as L  # noqa: E402
import voice_state as S  # noqa: E402
import voice_weather as W  # noqa: E402

HOURS = ["2026-10-21T%02d:00" % h for h in range(10, 24)] + ["2026-10-22T%02d:00" % h for h in range(0, 10)]
FORECAST = {
    "current": {"temperature_2m": 24.3, "relative_humidity_2m": 50, "apparent_temperature": 25, "is_day": 1, "precipitation": 0.0,
                "weather_code": 3, "wind_speed_10m": 3.2},
    "hourly": {"time": HOURS, "temperature_2m": [24 - abs(i - 6) * 0.5 for i in range(24)], "weather_code": [3] * 24,
               "precipitation_probability": [10] * 12 + [35] * 12},
    "daily": {"time": ["2026-10-21", "2026-10-22"] + ["2026-10-%d" % d for d in range(23, 28)], "weather_code": [3, 61, 0, 0, 0, 0, 0],
              "temperature_2m_max": [26.2, 29.4, 27, 27, 27, 27, 27], "temperature_2m_min": [15.1, 18.2, 16, 16, 16, 16, 16],
              "precipitation_probability_max": [10, 70, 5, 5, 5, 5, 5]},
}
ROOMS = ["Салон", "Спальня"]  # i18n


def ask(phrase, lang):
    q = S.quick(phrase, ROOMS, lambda words: [])
    return q, (W.answer(FORECAST, q, lang) if q and q["type"] == "weather" else None)


class Weather(unittest.TestCase):
    def test_questions_are_classified_by_their_words(self):
        for phrase in ("какая погода", "какая погода завтра", "будет дождь?", "сколько градусов на улице", "what's the weather tomorrow?",  # i18n
                       "will it rain today", "how hot is it outside"):
            self.assertEqual(ask(phrase, "ru")[0]["type"], "weather", phrase)
        self.assertTrue(S.is_query("will it rain today") and S.is_query("будет дождь?"))  # i18n
        self.assertTrue(S.is_query("будет дождь завтра") and S.is_query("погода на завтра") and S.is_query("weather tomorrow"))
        self.assertFalse(S.is_query("включи свет на улице") or S.is_query("turn on the light outside"))        # i18n: a command is no question
        self.assertEqual(S.quick("сколько градусов в спальне", ROOMS, lambda w: [])["type"], "room")          # a room, not the street

    def test_a_place_word_alone_is_no_weather_question(self):
        for phrase in ("горит ли свет на улице", "включен ли свет на дворе", "is the light on outside", "открыта ли дверь на улице"):  # i18n
            q = S.quick(phrase, ROOMS, lambda words: [])
            self.assertFalse(q and q["type"] == "weather", phrase)
            self.assertFalse(W.is_weather(phrase), phrase)
        for phrase in ("какая погода на улице", "сколько градусов на улице", "какая температура на улице", "холодно на улице", "how cold is it outside",  # i18n
                       "what is the weather outside", "идет ли дождь на улице"):
            self.assertEqual(ask(phrase, "ru")[0]["type"], "weather", phrase)

    def test_flags(self):
        self.assertEqual(W.flags_of("какая погода завтра"), {"day": "tomorrow", "rain": False, "temp": False})  # i18n
        self.assertEqual(W.flags_of("будет дождь вечером"), {"day": "evening", "rain": True, "temp": False})
        self.assertEqual(W.flags_of("сколько градусов на улице"), {"day": None, "rain": False, "temp": True})  # i18n
        self.assertEqual(W.flags_of("what is the weather today"), {"day": "today", "rain": False, "temp": False})

    def test_russian_answers_come_from_the_forecast(self):
        full, spoken = ask("какая погода", "ru")[1]  # i18n
        self.assertEqual((full, spoken), ("Сейчас 24°, пасмурно, без дождя", "Сейчас 24 градуса, пасмурно, без дождя"))     # only what was asked
        self.assertEqual(ask("какая сейчас погода?", "ru")[1][0], "Сейчас 24°, пасмурно, без дождя")  # i18n
        self.assertEqual(ask("какая погода сегодня", "ru")[1][0], "Сегодня: от 15 до 26, пасмурно, без дождя (10%)")
        self.assertEqual(ask("какая погода завтра", "ru")[1], ("Завтра: от 18 до 29, дождь (70%)", "Завтра до 29, дождь"))  # i18n
        self.assertEqual(ask("сколько градусов на улице", "ru")[1], ("На улице 24°", "На улице 24 градуса"))
        self.assertEqual(ask("какая погода вечером", "ru")[1][1], "Вечером от 20 до 24, возможен дождь")  # i18n

    def test_rain(self):
        self.assertEqual(ask("будет дождь?", "ru")[1][1], "Сегодня: без дождя. Завтра: дождь")  # i18n
        self.assertEqual(ask("будет дождь завтра", "ru")[1][0], "Завтра: дождь (70%)")

    def test_english_answers(self):
        self.assertEqual(ask("what's the weather tomorrow?", "en")[1], ("Tomorrow: 18 to 29, rain expected (70%)", "Tomorrow up to 29, rain expected"))
        self.assertEqual(ask("how hot is it outside", "en")[1], ("It is 24° outside", "It is 24 degrees outside"))  # i18n
        self.assertEqual(ask("will it rain today", "en")[1][1], "Today: no rain")
        self.assertEqual(ask("what is the weather", "en")[1][1], "Now 24 degrees, overcast, no rain")

    def test_no_data_says_so(self):
        self.assertEqual(W.answer(None, {"day": None}, "ru"), ("Нет данных о погоде",) * 2)  # i18n
        self.assertEqual(W.answer({}, {"day": "tomorrow"}, "en"), ("I have no weather data right now",) * 2)

    def test_the_model_only_names_the_kind_of_question(self):
        q = L.parse_dsl("ask weather tomorrow", {}, None, {})["query"]
        self.assertEqual(q, {"type": "weather", "day": "tomorrow", "rain": False, "temp": False})
        self.assertEqual(L.parse_dsl("ask weather rain", {}, None, {})["query"]["rain"], True)
        with self.assertRaises(ValueError):
            L.parse_dsl("ask weather someday", {}, None, {})

    def test_a_question_that_names_a_device_is_not_the_forecast(self):
        def find(words):                                       # the house has a rain sensor and a light outside, found by word prefixes
            return [d for d, stems in (("rain", ("датч", "дожд")), ("yard", ("улиц",))) if any(w.startswith(st) for w in words for st in stems)]  # i18n
        q = S.quick("сработал ли датчик дождя", ROOMS, find)
        self.assertEqual((q or {}).get("type"), "device")
        self.assertEqual(q["ids"], ["rain"])
        for phrase in ("будет ли дождь", "какая погода на улице", "сколько градусов на улице", "будет ли дождь завтра"):  # i18n
            self.assertEqual(S.quick(phrase, ROOMS, find)["type"], "weather", phrase)


if __name__ == "__main__":
    unittest.main()


class Intensity(unittest.TestCase):
    def test_a_slight_shower_is_no_downpour(self):
        w = {**FORECAST, "current": {**FORECAST["current"], "weather_code": 80, "precipitation": 0.1, "temperature_2m": 25.7}}
        self.assertEqual(W.answer(w, {"day": "now"}, "ru")[0], "Сейчас 26°, небольшой дождь местами")  # i18n
        self.assertEqual(W.answer(w, {"day": "now"}, "en")[0], "Now 26°, light showers")
        for c in (51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96, 99):
            self.assertIn(c, W.CODE_RU)
            self.assertIn(c, W.CODE_EN)
        self.assertNotEqual(W.CODE_RU[80], W.CODE_RU[82])
