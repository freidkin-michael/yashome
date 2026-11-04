"""The decision rules on the EXACT strings of the live test of 20.10 18:46-18:48."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugins", "tools", "voice", "service", "vad"))
from voice_svc_fixtures import FIXTURES  # noqa: E402
import voice_llm as L  # noqa: E402
import voice_rules as R  # noqa: E402

load = lambda n: json.loads(json.dumps(FIXTURES[n]))  # noqa: E731
REG = L.Registry(load("registry.json"), [tuple(x) for x in load("zone_words.json")])
STEMS = R.verb_stems(["включ", "вруб", "зажг", "запус"], ["выключ", "выруб", "погас", "отключ", "туши", "потуши"])  # i18n
ZONES = {"кухн": ("z2m", "Кухня", "state", None), "спальн": ("z2m", "Bedroom switch", "state", None)}


class LiveStrings(unittest.TestCase):
    def test_1_a_device_that_was_not_said_is_never_asked_about(self):
        # i18n: free='как из включи свет на и' -> the model said `ac power=on`: the phrase names no device, so no question about the air conditioner
        words = R.strip_lead("как из включи свет на и".split(), STEMS)
        self.assertEqual(words, ["включи", "свет", "на", "и"])  # i18n
        parsed = L.parse_dsl("ac power=on", REG.names, REG.qnames, REG.room_slugs)
        plan = L.plan(REG, parsed, words)
        self.assertIn("guess:ac", plan["ask"])
        reply = R.guess_reply(parsed, plan, words, STEMS)
        self.assertTrue(reply.startswith("Где"))  # i18n
        self.assertNotIn("кондиционер", reply.lower())
        # a named device is not a guess
        words = "включи свет в спальне".split()  # i18n
        plan = L.plan(REG, L.parse_dsl("bedroom_switch state=on", REG.names), words)
        self.assertIsNone(R.guess_reply({"action": "set"}, plan, words, STEMS))
        # all_off except a guessed name: said so, no question about another device
        parsed = L.parse_dsl("all_off except=ac_light", REG.names)
        plan = L.plan(REG, parsed, "выключи всё кроме ночника".split())  # i18n
        self.assertEqual(R.guess_reply(parsed, plan, "выключи всё кроме ночника".split(), STEMS), "Не поняла, кроме чего выключить.")

    def test_2_a_command_while_a_question_waits_is_not_an_answer(self):
        # i18n: heard='включи свет на кухне' used to be "no" -> "Отменено."
        self.assertEqual(R.answer_kind("включи свет на кухне", L.confirmation), "command")
        self.assertEqual(R.answer_kind("да", L.confirmation), "yes")  # i18n
        self.assertEqual(R.answer_kind("да давай", L.confirmation), "yes")
        self.assertEqual(R.answer_kind("нет", L.confirmation), "no")  # i18n
        self.assertEqual(R.answer_kind("", L.confirmation), "empty")
        self.assertEqual(R.answer_kind("   ", L.confirmation), "empty")

    def test_3_the_grammar_wins_when_the_free_pass_lost_only_the_verb(self):
        # i18n: free='крис выжить но кухня', grammar='выключить кухня'
        gw, fw = "выключить кухня".split(), "крис выжить но кухня".split()
        self.assertTrue(R.accept_grammar(gw, fw, STEMS, 0.7))
        # not when the free pass heard another verb, nor when the zone was not heard
        self.assertFalse(R.accept_grammar(gw, "включить спальня".split(), STEMS, 0.7))  # i18n
        self.assertFalse(R.accept_grammar(gw, "что то там".split(), STEMS, 0.7))
        self.assertFalse(R.accept_grammar(["кухня"], "кухня".split(), STEMS, 1.1))  # i18n
        self.assertTrue(R.accept_grammar(["выключи"], ["выпить"], STEMS, 0.7))                   # 22.10: a clear verb in the grammar, none in the free pass
        self.assertFalse(R.accept_grammar(["выключи"], ["выключить", "кухня"], STEMS, 1.1))        # i18n: the free pass has a verb of its own: it decides        # no verb in the grammar text: nothing to rescue
        # the stray words before the verb ('крис', 'джарвис', 'как из') are dropped
        self.assertEqual(R.strip_lead("крис включи свет в спальне".split(), STEMS), ["включи", "свет", "в", "спальне"])  # i18n
        self.assertEqual(R.strip_lead("крис выключи кухня".split(), STEMS), ["выключи", "кухня"])
        self.assertEqual(R.strip_lead("привет как дела у тебя включи".split(), STEMS), "привет как дела у тебя включи".split())   # i18n: a verb far away stays

    def test_4_a_zone_stem_line_of_the_model_is_repaired_or_refused(self):
        # i18n: line='кухн=on' used to end in an error
        self.assertEqual(R.repair_line("кухн=on", ZONES, REG),
                         {"action": "set", "except": [], "intents": [{"device": "Кухня", "code": "state", "value": "on"}]})  # i18n
        self.assertEqual(R.repair_line("спальн=off", ZONES, REG)["intents"][0]["device"], "Bedroom switch")
        for bad in ("зюзя=on", "кухн", "кухн=maybe", "", "ac power=on"):  # i18n
            self.assertIsNone(R.repair_line(bad, ZONES, REG), bad)
        # the repaired line goes through the same checks as any other
        plan = L.plan(REG, R.repair_line("кухн=on", ZONES, REG), "включи кухню".split())  # i18n
        self.assertEqual(plan["actions"], [("Кухня", "state", "on")])


class EndOfSpeech(unittest.TestCase):
    def test_a_pause_after_the_wake_word_does_not_end_the_phrase(self):
        """20.10: "hey jarvis" (1.0 s), the chime, then the command: 0.45 s of silence used to end it as just "джарвис"."""  # i18n
        import voice_rules as R
        self.assertEqual(R.end_quiet_frames(100), 133)        # the command has not begun: wait 4 s for the first word
        self.assertEqual(R.end_quiet_frames(600), 20)         # it has begun: 0.6 s ends it


class WakePhraseAndSounds(unittest.TestCase):
    def test_the_wake_phrase_of_either_model_and_its_mishearings_are_stripped_from_a_transcript(self):
        s = lambda t: R.strip_wake_phrase(t.split())                                       # noqa: E731
        self.assertEqual(s("эй берлога включи свет"), ["включи", "свет"])  # i18n
        self.assertEqual(s("эй верлога включи свет в салоне"), ["включи", "свет", "в", "салоне"])
        self.assertEqual(s("берлог включи свет"), ["включи", "свет"])  # i18n
        self.assertEqual(s("hey jarvis включи свет"), ["включи", "свет"])
        self.assertEqual(s("эй крис выключи свет"), ["выключи", "свет"])  # i18n
        self.assertEqual(s("джарвис включи свет"), ["включи", "свет"])
        self.assertEqual(s("включи свет на кухне"), ["включи", "свет", "на", "кухне"])       # i18n: no wake word: left as it is
        self.assertEqual(s("здесь берлога включи верхний свет"), ["включи", "верхний", "свет"])        # 22.10: "эй" heard as "здесь"
        self.assertEqual(s("эй включи свет"), ["эй", "включи", "свет"])                      # i18n: a filler alone is no wake phrase
        self.assertEqual(s("включи берлогу"), ["включи", "берлогу"])                         # a wake-like word later in the phrase stays

    def test_an_understood_command_is_silent_a_failure_says_why_and_a_miss_says_ne_ponyala_without_a_beep(self):
        self.assertEqual(R.final_message(True, None), (None, True))                          # the beep was its answer: no second sound
        self.assertEqual(R.final_message(True, "Свет на кухне не ответил"), ("Свет на кухне не ответил", False))  # i18n
        self.assertEqual(R.final_message(False, None), ("Не поняла.", False))
        self.assertEqual(R.final_message(True, "В спальне 22 градуса"), ("В спальне 22 градуса", False))     # i18n: a question is answered aloud
        self.assertEqual(R.THINKING_AFTER_S, 1.5)


class MisheardWakePhrases(unittest.TestCase):
    def test_a_word_close_to_the_wake_word_is_stripped_and_unknown_words_before_a_question_or_a_verb_are_dropped(self):
        s = lambda t: R.strip_lead(R.strip_wake_phrase(t.split()), STEMS + list(R.QUESTION_STEMS))                   # noqa: E731
        self.assertEqual(s("эй дорога какая температура в салоне"), ["какая", "температура", "в", "салоне"])  # i18n
        self.assertEqual(s("ведь берлога какая температура в салоне"), ["какая", "температура", "в", "салоне"])
        self.assertEqual(s("никакого какая температура в салоне"), ["какая", "температура", "в", "салоне"])  # i18n
        self.assertEqual(s("взрывного включи верхний свет"), ["включи", "верхний", "свет"])
        self.assertEqual(s("включи верхний свет"), ["включи", "верхний", "свет"])  # i18n
        self.assertEqual(R.close_to_wake("дорога"), True)
        self.assertEqual(R.close_to_wake("включи"), False)  # i18n

    def test_the_room_of_a_model_line_is_written_the_way_the_parser_reads_it(self):
        slug = lambda x: x.lower()                                                                                   # noqa: E731
        self.assertEqual(R.norm_room_tokens("ask room=Салон temp", slug), "ask room=салон temp")  # i18n
        self.assertEqual(R.norm_room_tokens("set лампа=on", slug), "set лампа=on")


class TimeOfDay(unittest.TestCase):
    def test_what_time_is_answered_from_the_clock_and_other_phrases_are_not(self):
        import time
        now = time.struct_time((2026, 10, 25, 12, 5, 0, 6, 298, 0))
        self.assertEqual(R.time_answer("сколько времени", now), "Сейчас 12:05.")  # i18n
        self.assertEqual(R.time_answer("который сейчас час", now), "Сейчас 12:05.")
        self.assertEqual(R.time_answer("сколько градусов в салоне", now), None)  # i18n


class RoomOfTheSatellite(unittest.TestCase):
    def test_the_model_may_not_choose_a_room_the_phrase_does_not_name(self):
        q = {"type": "room", "room": "Ванна", "aspect": "temp"}  # i18n
        self.assertEqual(R.pin_room(q, "ты берлога какая температура и".split(), "Салон")["room"], "Салон")        # 22.10: the model invented the bath
        self.assertEqual(R.pin_room(q, "какая температура в ванной".split(), "Салон")["room"], "Ванна")              # i18n: named: left as it is
        self.assertEqual(R.pin_room({"type": "room", "room": "Салон"}, "какая температура в салоне".split(), "Спальня")["room"], "Салон")
        self.assertEqual(R.pin_room(q, "какая температура".split(), "")["room"], "Ванна")                             # i18n: no room known for the satellite: nothing to pin
        self.assertEqual(R.pin_room({"type": "who_home"}, ["кто"], "Салон"), {"type": "who_home"})
        self.assertEqual(R.strip_wake_phrase("ты берлога какая температура".split()), ["какая", "температура"])  # i18n


if __name__ == "__main__":
    unittest.main()


class OneClock(unittest.TestCase):
    def test_the_voice_path_has_the_date_and_weekday_too(self):
        import time
        now = time.strptime("2026-10-27 09:05", "%Y-%m-%d %H:%M")
        self.assertEqual(R.time_answer("сколько времени", now), "Сейчас 9:05.")  # i18n
        self.assertEqual(R.time_answer("какой сегодня день", now), "Сегодня вторник, 27 октября.")
