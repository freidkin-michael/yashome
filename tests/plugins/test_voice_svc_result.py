"""The spoken result of a voice command: after the device's own report, short, in human names; each case."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugins", "tools", "voice", "service", "vad"))
import voice_result as R  # noqa: E402

NAMES = {"Кухня/state": ("свет на кухне", "свет на кухне", "m"), "Bedroom switch/state": ("свет в спальне", "свет в спальне", "m"),  # i18n
         "ac/power": ("кондиционер в салоне", "кондиционер в салоне", "m"), "ac/temp": ("кондиционер в салоне", "кондиционер в салоне", "m"),
         "ac/mode": ("кондиционер в салоне", "кондиционер в салоне", "m"), "Shelf switch/state": ("подсветка в углу", "подсветку в углу", "f"),  # i18n
         "garden door switch/state_right": ("розетка во двор", "розетку во двор", "f"), "dev_87/volume": ("телевизор в салоне", "телевизор в салоне", "m"),
         "AC light/state": ("верхний свет в салоне", "верхний свет в салоне", "m"), "dev_125/locked": ("замок во двор", "замок во двор", "m")}  # i18n


def name(did, code, case):
    n = NAMES["%s/%s" % (did, code)]
    return n[0] if case == "nom" else n[1]


def gender(did, code):
    return NAMES["%s/%s" % (did, code)][2]


def item(did, code, value, before, ok=True):
    return {"did": did, "code": code, "value": value, "before": before, "ok": ok}


class Phrases(unittest.TestCase):
    def say(self, *items):
        return R.phrase(list(items), name, gender)

    def test_switched_on_and_off(self):
        self.assertEqual(self.say(item("Кухня", "state", "ON", False)), "Включила свет на кухне")  # i18n
        self.assertEqual(self.say(item("ac", "power", False, True)), "Выключила кондиционер в салоне")
        self.assertEqual(self.say(item("Shelf switch", "state", "on", False)), "Включила подсветку в углу")  # i18n

    def test_already_in_that_state_by_gender(self):
        self.assertEqual(self.say(item("Кухня", "state", "ON", True)), "Свет на кухне уже включён")  # i18n
        self.assertEqual(self.say(item("Shelf switch", "state", "on", True)), "Подсветка в углу уже включена")
        self.assertEqual(self.say(item("Кухня", "state", "OFF", False)), "Свет на кухне уже выключен")  # i18n

    def test_no_report_in_time(self):
        self.assertEqual(self.say(item("ac", "power", True, False, ok=False)), "Кондиционер в салоне не ответил")  # i18n
        self.assertEqual(self.say(item("garden door switch", "state_right", "on", False, ok=False)), "Розетка во двор не ответила")

    def test_settings(self):
        self.assertEqual(self.say(item("ac", "temp", 24, 20)), "Кондиционер в салоне: 24 градуса")  # i18n
        self.assertEqual(self.say(item("ac", "temp", "21", 20)), "Кондиционер в салоне: 21 градус")
        self.assertEqual(self.say(item("ac", "temp", 25, 20)), "Кондиционер в салоне: 25 градусов")  # i18n
        self.assertEqual(self.say(item("ac", "temp", 11, 20)), "Кондиционер в салоне: 11 градусов")
        self.assertEqual(self.say(item("ac", "mode", "cool", "heat")), "Кондиционер в салоне: режим охлаждение")  # i18n
        self.assertEqual(self.say(item("dev_87", "volume", 13, 10)), "Телевизор в салоне: громкость 13")

    def test_several_targets_are_one_phrase(self):
        self.assertEqual(self.say(item("Bedroom switch", "state", "on", False), item("Кухня", "state", "on", False)),  # i18n
                         "Включила свет в спальне и свет на кухне")
        self.assertEqual(self.say(item("Bedroom switch", "state", "on", False), item("Кухня", "state", "on", False), item("Shelf switch", "state", "on", False)),  # i18n
                         "Включила свет в спальне, свет на кухне и подсветку в углу")
        self.assertEqual(self.say(item("Bedroom switch", "state", "on", False), item("Кухня", "state", "on", False, ok=False)),  # i18n
                         "Включила свет в спальне. Свет на кухне не ответил")
        many = [item(d, "state", "on", False) for d in ("Кухня", "Bedroom switch", "Shelf switch", "AC light")]  # i18n
        self.assertEqual(self.say(*many), "Включила 4 устройства")                       # a count, not a long list

    def test_a_short_phrase(self):
        for items in ([item("Кухня", "state", "ON", False)], [item("ac", "temp", 24, 20)], [item("Кухня", "state", "ON", True)]):  # i18n
            self.assertLess(len(R.phrase(items, name, gender)), 45)                      # about 1.5 s of speech at most

    def test_lock(self):
        self.assertEqual(self.say(item("dev_125", "locked", "on", False)), "Заперла замок во двор")  # i18n
        self.assertEqual(self.say(item("dev_125", "locked", "off", True)), "Открыла замок во двор")


class Waiting(unittest.TestCase):
    def test_the_device_reports_after_two_polls(self):
        clock = [0.0]
        states = iter([False, False, True])
        its = R.wait_confirm([item("Кухня", "state", "on", False)], lambda d, c: next(states), 3.0, 0.25,  # i18n
                             now=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s))
        self.assertTrue(its[0]["ok"]) and self.assertAlmostEqual(clock[0], 0.5)

    def test_no_report_within_three_seconds(self):
        clock = [0.0]
        its = R.wait_confirm([item("Кухня", "state", "on", False)], lambda d, c: False, 3.0, 0.25,  # i18n
                             now=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s))
        self.assertFalse(its[0]["ok"])
        self.assertGreaterEqual(clock[0], 3.0)
        self.assertLess(clock[0], 3.5)

    def test_matching(self):
        self.assertTrue(R.matches(True, "ON") and R.matches(False, False) and R.matches(24.0, 24.4, "temp") and R.matches(40.0, 45))
        self.assertFalse(R.matches(True, False) or R.matches(24.0, 26, "temp") or R.matches(True, None))
        self.assertTrue(R.matches("cool", "COOL"))


if __name__ == "__main__":
    unittest.main()


class ProblemsOnly(unittest.TestCase):
    def test_a_command_that_worked_or_was_already_so_is_silent_and_a_failure_is_said(self):
        import voice_result as V
        name = lambda did, code, case: {"a": "свет на кухне", "b": "свет в спальне"}[did]         # i18n: noqa: E731
        gender = lambda did, code: "m"                                                              # noqa: E731
        ok = {"did": "a", "code": "state", "value": "on", "before": False, "ok": True}
        already = {"did": "b", "code": "state", "value": "on", "before": True, "ok": True}
        bad = {"did": "b", "code": "state", "value": "on", "before": False, "ok": False}
        self.assertEqual(V.phrase([dict(ok)], name, gender, "ru", problems_only=True), "")
        self.assertEqual(V.phrase([dict(already)], name, gender, "ru", problems_only=True), "")
        self.assertEqual(V.phrase([dict(ok), dict(bad)], name, gender, "ru", problems_only=True), "Свет в спальне не ответил")        # i18n: a partial failure
        self.assertIn("Включила", V.phrase([dict(ok)], name, gender, "ru"))                                                              # the full phrase is unchanged
        self.assertEqual(V.phrase([dict(bad)], lambda d, c, k: "the light", gender, "en", problems_only=True), "the light did not respond")
