"""The language of a request, the fixed phrases in both languages, English results."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugins", "tools", "voice", "service", "vad"))
import voice_lang as L  # noqa: E402
import voice_result as R  # noqa: E402


class Language(unittest.TestCase):
    def test_the_language_of_the_question_is_the_language_of_the_reply(self):
        self.assertEqual([L.lang_of(t) for t in ("what temperature in the bedroom?", "\u043a\u0430\u043a\u0430\u044f \u0442\u0435\u043c\u043f\u0435\u0440\u0430\u0442\u0443\u0440\u0430 \u0432 \u0441\u043f\u0430\u043b\u044c\u043d\u0435", "tv", "24", "")],  # i18n
                         ["en", "ru", "ru", "ru", "ru"])

    def test_fixed_phrases(self):
        self.assertEqual(L.tr("\u041d\u0435 \u043f\u043e\u043d\u044f\u043b\u0430.", "en"), "Sorry, I did not understand.")  # i18n
        self.assertEqual(L.tr("\u0421\u0434\u0435\u043b\u0430\u043d\u043e: kitchen: state=on", "en"), "Done: kitchen: state=on")
        self.assertEqual(L.tr("\u041d\u0435 \u043f\u043e\u043d\u044f\u043b\u0430.", "ru"), "\u041d\u0435 \u043f\u043e\u043d\u044f\u043b\u0430.")  # i18n
        self.assertIsNone(L.tr(None, "en"))
        self.assertEqual(L.tr("Already English", "en"), "Already English")

    def test_english_words_give_the_russian_stems_the_house_knows(self):
        self.assertEqual(L.en_stems("turn on the kitchen light"), ["\u043a\u0443\u0445\u043d", "\u0441\u0432\u0435\u0442"])  # i18n


class EnglishResults(unittest.TestCase):
    def test_spoken_results(self):
        name = lambda did, code, case: {"k": "the kitchen light", "b": "the bedroom lamp"}[did]          # noqa: E731
        item = lambda did, value, before, ok: {"did": did, "code": "state", "value": value, "before": before, "ok": ok}          # noqa: E731
        self.assertEqual(R.phrase([item("k", "on", False, True)], name, None, "en"), "Turned on the kitchen light")
        self.assertEqual(R.phrase([item("k", "off", True, True), item("b", "off", True, True)], name, None, "en"),
                         "Turned off the kitchen light and the bedroom lamp")
        self.assertEqual(R.phrase([item("k", "on", True, True)], name, None, "en"), "the kitchen light is already on")
        self.assertEqual(R.phrase([item("k", "on", False, False)], name, None, "en"), "the kitchen light did not respond")


if __name__ == "__main__":
    unittest.main()
