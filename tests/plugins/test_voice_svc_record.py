"""Sample and background recording: raw audio saved as it is; only what a person armed; owner-only files; nothing recognised."""
import os
import sys
import tempfile
import threading
import time
import unittest
import wave
from collections import namedtuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugins", "tools", "voice", "service", "vad"))
TMP = tempfile.mkdtemp()
os.environ["VA_SAMPLES_DIR"] = TMP
os.environ["VA_WAKE_PHRASES"] = os.path.join(TMP, "p.json")
import voice_record as R  # noqa: E402

DU = namedtuple("DU", "total used free")
TONE = (b"\x00\x20" + b"\x00\xe0") * 8000          # one second of a loud square wave


class Samples(unittest.TestCase):
    def test_speaker_names_are_safe_folders(self):
        self.assertEqual(R.slug("Михаил"), "mihail")  # i18n
        self.assertEqual(R.slug("../../etc"), "etc")
        self.assertEqual(R.slug(""), "speaker")

    def test_an_armed_stream_is_saved_and_nothing_else_is(self):
        self.assertFalse(R.feed("192.0.2.9", TONE))                                    # not armed: not taken
        rid = R.arm("192.0.2.9", "anna", "positive", "respeaker")
        self.assertEqual(R.status(rid)["state"], "armed")
        for _ in range(4):
            R.feed("192.0.2.9", TONE)                                                  # 4 s offered, 3 s needed
        st = R.status(rid)
        self.assertEqual(st["state"], "saved")
        self.assertEqual(st["secs"], 3.0)
        self.assertFalse(st["quiet"])
        with wave.open(os.path.join(TMP, st["file"])) as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()), (16000, 1, 2, 48000))
        self.assertFalse(R.feed("192.0.2.9", TONE))                                    # done: the next stream is ordinary again

    def test_a_silent_sample_says_so(self):
        rid = R.arm("192.0.2.8", "anna", "negative", "respeaker")
        R.feed("192.0.2.8", b"\x00\x00" * 48000)
        self.assertEqual(R.status(rid), {"state": "quiet", "quiet": True})                 # nobody spoke: nothing is kept

    def test_listing_and_deleting(self):
        R.save_pcm(TONE, "boris", "positive", "phone", "Boris")
        R.save_pcm(TONE, "boris", "positive", "phone")
        self.assertEqual(R.listing()["boris"]["positive"], 2)
        self.assertEqual(R.listing()["boris"]["name"], "Boris")
        newest = sorted(os.listdir(os.path.join(TMP, "boris", "positive")))[-1]
        self.assertEqual(R.delete(speaker="boris", label="positive")["deleted"], os.path.join("boris", "positive", newest))
        self.assertEqual(R.listing()["boris"]["positive"], 1)
        with self.assertRaises(ValueError):
            R.delete("../../etc/passwd")
        with self.assertRaises(ValueError):
            R.arm("192.0.2.7", "anna", "other", "x")                                   # only positive | negative

    def test_reserved_names_and_the_background_folder_are_not_speakers(self):
        for bad in ("_background", "background", "Background"):
            with self.assertRaises(ValueError):
                R.arm("192.0.2.3", bad, "positive", "x")
        os.makedirs(os.path.join(TMP, "_background", "d"), exist_ok=True)
        self.assertNotIn("_background", R.listing())

    def test_negative_phrases(self):
        open(os.environ["VA_WAKE_PHRASES"], "w", encoding="utf-8").write('{"negative": ["a", "b", "c"]}')
        self.assertEqual(sorted(R.phrases(5)), ["a", "b", "c"])


class Background(unittest.TestCase):
    def test_a_session_chains_streams_and_files_are_owner_only(self):
        sent = []
        R.PUB = lambda topic, payload: sent.append((topic, payload))
        out = R.bg_start("192.0.2.5", "quiet", "respeaker/listen_once/command", "10", 60)
        self.assertEqual((out["state"], sent), ("recording", [("respeaker/listen_once/command", "10")]))
        R.bg_feed("192.0.2.5", TONE)                                                   # a stream
        R.bg_conn_closed("192.0.2.5")                                                  # it ended: the next is asked for at once
        self.assertEqual(sent[-1], ("respeaker/listen_once/command", "10"))
        self.assertEqual(len(sent), 2)
        R.bg_feed("192.0.2.5", TONE)
        done = R.bg_stop("192.0.2.5")
        self.assertEqual((done["state"], done["files"], done["seconds"]), ("stopped", 2, 2.0))     # a stream that ended closes its file: the next stream is a new file
        path = os.path.join(TMP, done["dir"])
        files = os.listdir(path)
        self.assertEqual(len(files), 2)
        self.assertEqual(oct(os.stat(os.path.join(path, files[0])).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o700")

    def test_a_session_ends_by_its_timeout_and_asks_again_when_a_stream_is_late(self):
        sent = []
        R.PUB = lambda topic, payload: sent.append(payload)
        R.bg_start("192.0.2.6", "tv", "t", "10", 0.2)
        n = len(sent)
        R._bg["192.0.2.6"]["kick"] -= 10
        R._bg["192.0.2.6"]["started"] -= 10                                            # nothing came for a long time
        R._bg["192.0.2.6"]["until"] = time.time() + 60
        R.bg_tick()
        self.assertEqual(len(sent), n + 1)
        R._bg["192.0.2.6"]["until"] = time.time() - 1
        R.bg_tick()
        self.assertEqual(R.bg_status("192.0.2.6")["state"], "done")

    def test_a_stop_between_two_feeds_drops_the_second_and_leaks_no_file(self):
        R.PUB = lambda topic, payload: None
        R.bg_start("192.0.2.7", "stopfeed", "t", "10", 60)
        self.assertTrue(R.bg_feed("192.0.2.7", TONE))
        R.bg_stop("192.0.2.7")
        self.assertFalse(R.bg_feed("192.0.2.7", TONE))                                 # after a stop: dropped, no writer, no new file
        self.assertEqual(R.bg_status("192.0.2.7")["files"], 1)

    def test_a_stop_in_the_middle_of_a_feed_waits_for_it(self):
        R.PUB = lambda topic, payload: None
        out = R.bg_start("192.0.2.8", "midfeed", "t", "10", 60)
        path = os.path.join(TMP, out["dir"])
        real, th = R._unique, []

        def racing(p):                                                                 # the stop arrives while the feed opens its file
            t = threading.Thread(target=R.bg_stop, args=("192.0.2.8",))
            t.start()
            t.join(0.3)
            th.append(t)
            return real(p)
        R._unique = racing
        try:
            R.bg_feed("192.0.2.8", TONE)
        finally:
            R._unique = real
        th[0].join(5)
        files = os.listdir(path)
        self.assertEqual(len(files), 1)
        f = os.path.join(path, files[0])
        self.assertEqual(oct(os.stat(f).st_mode & 0o777), "0o600")
        with wave.open(f, "rb") as w:                                                  # closed properly: a valid header with all the frames
            self.assertEqual(w.getnframes(), 16000)
        self.assertEqual(R.bg_status("192.0.2.8")["state"], "stopped")

    def test_a_full_disk_refuses_and_stops_a_background_session(self):
        R.PUB = lambda topic, payload: None
        real = R.shutil.disk_usage
        try:
            R.shutil.disk_usage = lambda p: DU(100 << 30, 99 << 30, R.MIN_FREE_BYTES - 1)
            self.assertEqual(R.bg_start("192.0.2.10", "full", "t")["state"], "disk_low")
            self.assertEqual(R.bg_status("192.0.2.10")["state"], "disk_low")
            self.assertNotIn("192.0.2.10", R._bg)
            R.shutil.disk_usage = lambda p: DU(100 << 30, 1 << 30, R.MIN_FREE_BYTES + 1)
            self.assertEqual(R.bg_start("192.0.2.10", "full", "t")["state"], "recording")
            R.shutil.disk_usage = lambda p: DU(100 << 30, 99 << 30, R.MIN_FREE_BYTES - 1)
            R.bg_tick()
            self.assertEqual(R.bg_status("192.0.2.10")["state"], "disk_low")
            R.shutil.disk_usage = lambda p: DU(100 << 30, 1 << 30, R.MIN_FREE_BYTES + 1)
            R.bg_start("192.0.2.10", "full", "t")
            R.bg_feed("192.0.2.10", TONE)
            R.bg_conn_closed("192.0.2.10")
            R.shutil.disk_usage = lambda p: DU(100 << 30, 99 << 30, R.MIN_FREE_BYTES - 1)
            self.assertFalse(R.bg_feed("192.0.2.10", TONE))                            # a new file would need space: the session ends
            self.assertEqual(R.bg_status("192.0.2.10")["state"], "disk_low")
        finally:
            R.shutil.disk_usage = real
            R.bg_stop("192.0.2.10")

    def test_a_bad_label_is_refused(self):
        with self.assertRaises(ValueError):
            R.bg_start("192.0.2.4", "../x", "t")


class HttpCodes(unittest.TestCase):
    """A bad request is 400 so the phone drops it; 500 would keep the file in its upload queue forever."""

    def test_a_reserved_or_bad_name_is_400_on_every_post(self):
        for path, body in (("/record/arm", {"ip": "192.0.2.1", "speaker": "background", "label": "positive"}),
                           ("/record/arm", {"ip": "192.0.2.1", "speaker": "anna", "label": "maybe"}),
                           ("/record/arm", {"ip": "192.0.2.1", "speaker": "_x", "label": "positive"}),
                           ("/record/delete", {"file": "../../etc/passwd"}),
                           ("/record/delete", {"speaker": "background", "label": "positive"}),
                           ("/record/bg", {"ip": "192.0.2.1", "action": "start", "label": "../x"}),
                           ("/record/bg", {"ip": "192.0.2.1", "action": "start", "label": "tv", "max_s": "abc"}),
                           ("/record/arm", {"ip": "192.0.2.1"})):
            self.assertEqual(R.http_post(path, body)[0], 400, (path, body))
        code, _ = R.http_post("/record/save?speaker=background&label=positive", {}, TONE)
        self.assertEqual(code, 400)

    def test_a_bad_get_is_400_a_missing_one_404_and_a_good_one_200(self):
        self.assertEqual(R.http_get("/record/file", {"f": "../../etc/passwd"})[0], 400)
        self.assertEqual(R.http_get("/record/file", {"f": "nobody/positive/none.wav"})[0], 404)
        self.assertEqual(R.http_get("/record/phrases", {"n": "x"})[0], 400)
        self.assertEqual(R.http_get("/record/list", {})[0], 200)
        self.assertEqual(R.http_post("/record/nothing", {})[0], 404)
        self.assertEqual(R.http_post("/record/bg", {"ip": "192.0.2.1"})[0], 200)

    def test_a_missing_file_is_404_and_a_full_disk_507(self):
        import errno
        from unittest import mock
        self.assertEqual(R.http_post("/record/delete", {"file": "nobody/positive/none.wav"})[0], 404)
        full = OSError(errno.ENOSPC, "no space")
        with mock.patch.object(R, "save_pcm", side_effect=full):
            self.assertEqual(R.http_post("/record/save?speaker=anna&label=positive", {}, TONE)[0], 507)
        with mock.patch.object(R.os, "makedirs", side_effect=full):
            self.assertEqual(R.http_post("/record/bg", {"ip": "192.0.2.9", "action": "start", "label": "tv"})[0], 507)
        with mock.patch.object(R, "save_pcm", side_effect=PermissionError(errno.EACCES, "no")):
            with self.assertRaises(PermissionError):
                R.http_post("/record/save?speaker=anna&label=positive", {}, TONE)

    def test_post_bodies_are_validated(self):
        ok = {"ip": "192.0.2.1", "action": "start", "label": "tv"}
        for path, body in (("/record/arm", []), ("/record/bg", "x"), ("/record/delete", [1]),
                           ("/record/arm", {"ip": " ", "speaker": "anna", "label": "positive"}),
                           ("/record/bg", dict(ok, ip="")),
                           ("/record/bg", dict(ok, max_s="nan")), ("/record/bg", dict(ok, max_s="inf")),
                           ("/record/bg", dict(ok, max_s=-5)), ("/record/bg", dict(ok, max_s=43201)),
                           ("/record/bg", dict(ok, max_s=[1]))):
            self.assertEqual(R.http_post(path, body)[0], 400, (path, body))
        self.assertEqual(R.http_post("/record/bg", dict(ok, max_s=43200))[0], 200)
        self.assertEqual(R.http_post("/record/bg", {"ip": "192.0.2.1", "action": "stop"})[0], 200)


if __name__ == "__main__":
    unittest.main()
