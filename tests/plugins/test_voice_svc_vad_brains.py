"""The service's router of the model's questions (voice_vad._brains): one poller, the real prompt at the first warm-up, a long warm-up timeout."""
import os
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugins", "tools", "voice", "service", "vad"))
import voice_llm  # noqa: E402
import voice_vad as V  # noqa: E402


class Slow:
    made = []

    def __init__(self, url, model, threads=4, timeout=5.0, extra=None, keep_alive=-1):
        self.model, self.asked = model, []
        time.sleep(0.05)                                   # a window for a second caller
        Slow.made.append(self)

    def ask(self, system, phrase, timeout=None):
        self.asked.append((system, timeout))
        return ""


class RouterOfQuestions(unittest.TestCase):
    def setUp(self):
        Slow.made = []
        self.saved = dict(V._llm)
        V._llm.update({"ollama": None, "reg": None, "brains": None})
        self.patches = [mock.patch.object(voice_llm, "Ollama", Slow), mock.patch.object(voice_llm.Brains, "start", lambda self: None),
                        mock.patch.object(V, "BIG_URL", "http://big"), mock.patch.object(V, "BIG_HEALTH", "http://big/health")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        V._llm.clear()
        V._llm.update(self.saved)

    def test_two_callers_get_the_same_router(self):
        got = []
        ts = [threading.Thread(target=lambda: got.append(V._brains())) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertEqual(len({id(b) for b in got}), 1)
        self.assertEqual(len(Slow.made), 2)                # the small and the big model: once each

    def test_the_first_warm_up_has_the_real_prompt_and_the_long_timeout(self):
        V._llm["reg"] = mock.Mock(system="THE PROMPT")
        b = V._brains()
        self.assertEqual(b.system, "THE PROMPT")
        b._warm_fn(b.system)
        big = Slow.made[1]
        self.assertEqual(big.asked, [("THE PROMPT", voice_llm.WARM_TIMEOUT)])


if __name__ == "__main__":
    unittest.main()


class ChatReply(unittest.TestCase):
    """The chat's text: the model's answer, or what happened to the model; a house question never reaches it."""
    def reply(self, text, chat):
        with mock.patch.object(V, "_brains", lambda: type("B", (), {"chat": staticmethod(chat)})()):
            return V.chat_reply([{"role": "user", "content": text}])

    def test_the_answer_a_late_model_and_a_down_model(self):
        self.assertEqual(self.reply("напомни закон Ома", lambda m: ("U = I*R.", "14b")), "U = I*R.")  # i18n
        import socket

        def late(m):
            raise socket.timeout("timed out")

        def down(m):
            raise ConnectionRefusedError("refused")
        self.assertEqual(self.reply("напомни закон Ома", late), "Модель не успела ответить.")  # i18n
        self.assertEqual(self.reply("напомни закон Ома", down), "Модель сейчас недоступна.")
        self.assertEqual(self.reply("what is Ohm's law", down), "The model is not available now.")

    def test_a_house_question_never_reaches_the_model(self):
        def boom(m):
            raise AssertionError("the model was called")
        self.assertEqual(self.reply("в спальне тепло?", boom), voice_llm.HOUSE_REFUSAL)  # i18n


class ModelFailed(unittest.TestCase):
    def test_a_late_or_absent_model_is_said_as_such(self):
        import socket
        self.assertIn("загружается", V.model_failed(socket.timeout("timed out")))  # i18n
        self.assertIn("недоступна", V.model_failed(ConnectionRefusedError()))
        self.assertIsNone(V.model_failed(ValueError("bad line")))


class Loading(unittest.TestCase):
    def test_a_question_while_the_model_starts_is_told_so_at_once(self):
        saved = V._llm.get("ready")
        V._llm["ready"] = False
        try:
            r = V.llm_handle("кто в доме", "t1", "text")  # i18n
        finally:
            V._llm["ready"] = saved
        self.assertEqual(r["decision"], "loading")
        self.assertIn("загружается", r["said"])  # i18n

    def test_an_unreadable_line_asks_back(self):
        self.assertIn("Не поняла, о чём вы", V.NOT_SURE)  # i18n


class OneNamePerThing(unittest.TestCase):
    def test_the_spoken_name_of_a_device_stays_a_function(self):
        self.assertTrue(callable(V._spoken))                                       # a dict of that name once replaced it
        self.assertIsInstance(V._last_said, dict)
