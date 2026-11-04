"""Which model answers: the small one on the Dell always works; the big one only while healthy, never in the way of a command."""
import os
import sys
import json
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugins", "tools", "voice", "service", "vad"))
import voice_llm as L  # noqa: E402


class Fake:
    def __init__(self, name, fail=False):
        self.name, self.fail, self.asked, self.chats = name, fail, 0, 0

    def ask(self, system, phrase, timeout=None):
        self.asked += 1
        if self.fail:
            raise OSError("down")
        return "%s:%s" % (self.name, phrase)

    def chat_stream(self, messages, timeout=60.0, think=False):
        self.chats += 1
        if self.fail:
            raise OSError("down")
        yield self.name
        yield "-answer"


def brains(state, loaded=True, big_fail=False, health_fail=False):
    small, big = Fake("4b"), Fake("14b", big_fail)
    warmed = []
    ev = threading.Event()

    def health():
        if health_fail:
            raise OSError("tunnel down")
        return {"state": state, "loaded": loaded}

    def warm(system):
        warmed.append(system)
        ev.set()
    b = L.Brains(small, big, health, warm=warm, clock=lambda: 1000.0)
    b.refresh()                                                                         # what the poller thread does
    return b, small, big, warmed, ev


class Routing(unittest.TestCase):
    def test_the_big_brain_answers_while_it_is_available_and_loaded(self):
        b, small, big, _, _ = brains("available")
        self.assertEqual(b.ask("sys", "\u0441\u0432\u0435\u0442"), ("14b:\u0441\u0432\u0435\u0442", "14b"))  # i18n
        self.assertEqual((small.asked, big.asked, b.last), (0, 1, "14b"))

    def test_busy_off_or_unreachable_means_the_small_one(self):
        for state, kw in (("busy", {}), ("off", {}), ("available", {"health_fail": True})):
            b, small, big, _, _ = brains(state, **kw)
            self.assertEqual(b.ask("sys", "x")[1], "4b", state)
            self.assertEqual(big.asked, 0)

    def test_a_cold_big_brain_never_delays_a_command_it_is_warmed_meanwhile(self):
        b, small, big, warmed, ev = brains("available", loaded=False)
        self.assertTrue(ev.wait(2))                                                     # warmed by the poll alone, before any command
        self.assertEqual((warmed, big.asked), ([""], 0))
        self.assertEqual(b.ask("the system prompt", "x")[1], "4b")
        self.assertEqual(big.asked, 0)

    def test_a_failed_big_answer_is_retried_on_the_small_one_in_the_same_call(self):
        b, small, big, _, _ = brains("available", big_fail=True)
        self.assertEqual(b.ask("sys", "x"), ("4b:x", "4b"))                             # the command is not lost
        self.assertEqual((big.asked, small.asked, b.state), (1, 1, "off"))             # and the big one is left alone until the next poll
        self.assertEqual(b.ask("sys", "y")[1], "4b")
        self.assertEqual(big.asked, 1)

    def test_the_big_command_answer_has_a_2_s_limit_and_a_failure_is_retried_at_once_on_the_small_one(self):
        seen = {}

        class Timed(Fake):
            def ask(self, system, phrase, timeout=None):
                seen.setdefault(self.name, []).append(timeout)
                return super().ask(system, phrase, timeout)
        small, big = Timed("4b"), Timed("14b", fail=True)
        b = L.Brains(small, big, lambda: {"state": "available", "loaded": True}, clock=lambda: 1000.0, model="m")
        self.assertEqual(b.dsl_timeout, 2.0)
        b.refresh()
        self.assertEqual(b.ask("sys", "x", timeout=5.0), ("4b:x", "4b"))                 # the retry is in the same call
        self.assertEqual(seen["14b"], [2.0])                                             # the big one never gets the small one's 5 s
        self.assertEqual(seen["4b"], [5.0])
        b2 = L.Brains(Timed("4b"), Timed("14b"), lambda: {"state": "available", "loaded": True}, dsl_timeout=7.0, clock=lambda: 1000.0, model="m")
        b2.refresh()
        b2.ask("sys", "x")
        self.assertEqual(seen["14b"], [2.0, 7.0])                                        # the limit is the Brains setting, not a constant elsewhere

    def test_chat_goes_to_the_big_brain_and_falls_back(self):
        b, small, big, _, _ = brains("available")
        self.assertEqual(b.chat([{"role": "user", "content": "hi"}]), ("14b-answer", "14b"))
        b, small, big, _, _ = brains("available", loaded=False)                        # cold: the small one at once, never a wait for the load
        self.assertEqual(b.chat([]), ("4b-answer", "4b"))
        self.assertEqual(big.chats, 0)
        b, small, big, _, _ = brains("available", big_fail=True)
        self.assertEqual(b.chat([]), ("4b-answer", "4b"))
        b, small, big, _, _ = brains("off")
        self.assertEqual(b.chat([])[1], "4b")

    def test_the_health_lists_the_loaded_models(self):
        small, big = Fake("4b"), Fake("14b")
        for loaded, who in ((["qwen3:14b"], "14b"), ([], "4b"), (["other"], "4b")):
            b = L.Brains(small, big, lambda: {"state": "available", "loaded": loaded}, clock=lambda: 1000.0, model="qwen3:14b")
            b.refresh()
            self.assertEqual(b.ask("s", "x")[1], who)

    def test_without_a_big_brain_everything_is_the_small_one(self):
        small = Fake("4b")
        b = L.Brains(small)
        self.assertEqual(b.ask("s", "x"), ("4b:x", "4b"))
        self.assertEqual(b.chat([])[1], "4b")

    def test_a_command_or_a_chat_never_reads_the_health(self):
        polls = []
        small, big = Fake("4b"), Fake("14b")
        b = L.Brains(small, big, lambda: polls.append(1) or {"state": "available", "loaded": True}, clock=lambda: 1000.0)
        for _ in range(3):
            b.ask("s", "x")
            b.chat([])
        self.assertEqual((len(polls), b.last), (0, "4b"))                              # no poll yet: the state is "off", the small one answers
        b.refresh()
        self.assertEqual(b.ask("s", "y")[1], "14b")
        b.chat([])
        self.assertEqual(len(polls), 1)

    def test_the_poller_thread_reads_the_health_every_poll_interval(self):
        polls = []
        b = L.Brains(Fake("4b"), Fake("14b"), lambda: polls.append(1) or {"state": "available", "loaded": True}, poll_s=0.05)
        b.start()
        for _ in range(100):
            if len(polls) >= 3:
                break
            time.sleep(0.05)
        b.stop()
        self.assertGreaterEqual(len(polls), 3)
        self.assertEqual(b.state, "available")


class FakeServer(BaseHTTPRequestHandler):
    seen = []

    def do_GET(self):
        FakeServer.seen.append(("GET", self.path, None))
        data = json.dumps({"state": "available", "loaded": []}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        FakeServer.seen.append(("POST", self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        data = json.dumps({"message": {"content": "ok"}}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class OverHttp(unittest.TestCase):
    def test_the_cold_model_is_warmed_with_a_long_keep_alive_and_commands_make_no_health_get(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeServer)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:%d" % srv.server_address[1]
        FakeServer.seen = []
        big = L.Ollama(url, "qwen3:14b", keep_alive="30m")
        health = lambda: json.load(urllib.request.urlopen(url + "/health", timeout=1))        # noqa: E731
        b = L.Brains(Fake("4b"), big, health, warm=lambda s: big.ask(s, "x"), model="qwen3:14b")
        b.refresh()
        for _ in range(100):
            if any(m == "POST" for m, _, _ in FakeServer.seen):
                break
            time.sleep(0.05)
        srv.shutdown()
        gets = [s for s in FakeServer.seen if s[0] == "GET"]
        posts = [s for s in FakeServer.seen if s[0] == "POST"]
        self.assertEqual(len(gets), 1)                                                  # the poll only
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2]["keep_alive"], "30m")
        self.assertEqual(b.ask("s", "x")[1], "4b")                                      # still cold in the health: the small one, no GET
        self.assertEqual(len([s for s in FakeServer.seen if s[0] == "GET"]), 1)


class WarmUp(unittest.TestCase):
    def test_the_first_warm_up_gets_the_prompt_the_brains_were_made_with(self):
        warmed = []
        b = L.Brains(Fake("4b"), Fake("14b"), lambda: {"state": "available", "loaded": False}, warm=warmed.append, system="REAL PROMPT")
        b.refresh()
        for _ in range(100):
            if warmed:
                break
            time.sleep(0.02)
        self.assertEqual(warmed, ["REAL PROMPT"])

    def test_many_callers_start_one_warm_up_only(self):
        class Wide(L.Brains):                              # a slow read of the flag: a check-then-set without a lock lets everyone in
            @property
            def _warming(self):
                v = self.__dict__.get("_w", False)
                time.sleep(0.02)
                return v

            @_warming.setter
            def _warming(self, v):
                self.__dict__["_w"] = v
        calls, gate = [], threading.Event()
        b = Wide(Fake("4b"), Fake("14b"), lambda: {}, warm=lambda s: (calls.append(s), gate.wait(2)))
        ts = [threading.Thread(target=b._warm, args=("s",)) for _ in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        time.sleep(0.05)
        gate.set()
        self.assertEqual(len(calls), 1)

    def test_a_cold_load_may_take_two_minutes_but_a_command_keeps_its_timeout(self):
        self.assertGreaterEqual(L.WARM_TIMEOUT, 120.0)
        self.assertLess(L.Brains(Fake("4b")).dsl_timeout, 10.0)


if __name__ == "__main__":
    unittest.main()


class ThinkingChat(unittest.TestCase):
    def test_the_big_one_thinks_and_its_thinking_is_dropped(self):
        seen = []

        class Big(Fake):
            def chat_stream(self, messages, timeout=60.0, think=False):
                seen.append((timeout, think))
                yield "<think>Ohm...</think>"
                yield "U = I*R."
        small = Fake("4b")
        b = L.Brains(small, Big("14b"), lambda: {"state": "available", "loaded": True}, clock=lambda: 1000.0, model="m")
        b.refresh()
        self.assertEqual(b.chat([{"role": "user", "content": "напомни закон Ома"}]), ("U = I*R.", "14b"))  # i18n
        self.assertEqual(seen, [(60.0, True)])
        self.assertEqual(small.chats, 0)
