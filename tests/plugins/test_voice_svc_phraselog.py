import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugins", "tools", "voice", "service", "vad"))
import voice_phraselog as pl  # noqa: E402


class PhraseLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = (pl.DIR, pl.KEEP, pl._month["name"])
        pl.DIR, pl.KEEP, pl._month["name"] = self.tmp.name, 24, None

    def tearDown(self):
        pl.DIR, pl.KEEP, pl._month["name"] = self.old
        self.tmp.cleanup()

    def lines(self):
        out = []
        for name in sorted(os.listdir(self.tmp.name)):
            with open(os.path.join(self.tmp.name, name), encoding="utf-8") as f:
                out += [json.loads(x) for x in f]
        return out

    def test_one_line_per_phrase_in_russian(self):
        self.assertTrue(pl.record(src="voice", free="включи свет", engine="llm", ok=False, llm_line="ac power=on", reply=None))  # i18n
        (line,) = self.lines()
        self.assertEqual(line["free"], "включи свет")  # i18n
        self.assertEqual(line["llm_line"], "ac power=on")
        self.assertNotIn("reply", line)                          # None is left out
        self.assertIn("ts", line)

    def test_off_when_dir_empty(self):
        pl.DIR = ""
        self.assertFalse(pl.record(free="x"))

    def test_never_raises(self):
        pl.DIR = os.path.join(self.tmp.name, "file")
        open(pl.DIR, "w").close()                                # a file where the folder should be
        self.assertFalse(pl.record(free="x"))

    def test_long_value_cut(self):
        pl.record(reply="я" * 5000)  # i18n
        self.assertLess(len(self.lines()[0]["reply"]), 2100)

    def test_old_months_pruned(self):
        pl.KEEP = 2
        for m in ("2025-01", "2025-02", "2025-03"):
            open(os.path.join(self.tmp.name, "phrases-%s.jsonl" % m), "w").close()
        pl.record(free="x")
        self.assertEqual(len(os.listdir(self.tmp.name)), 2)

    def test_threads_do_not_mix_lines(self):
        ts = [threading.Thread(target=lambda i=i: [pl.record(free="фраза %d" % i, n=k) for k in range(50)]) for i in range(8)]  # i18n
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(self.lines()), 400)


if __name__ == "__main__":
    unittest.main()
