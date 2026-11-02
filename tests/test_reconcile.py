"""The first live change after a start is an edge (the broker's retained value seeds it), and a rule keeps its origin through its echo."""
import unittest
from unittest import mock

import app


class FirstLiveEdge(unittest.TestCase):
    """After a restart the broker replays the radar's last value (retained); the first live change from it is an edge and fires."""
    CFG = {"controls": [{"kind": "sensor", "code": "presence", "vtype": "bool"}]}

    def run_it(self, retained, live):
        calls = []
        with mock.patch.dict(app._sensor_last, {}, clear=True), mock.patch.dict(app._sensor_since, {}, clear=True), \
                mock.patch.object(app._bg, "submit", lambda fn, *a: calls.append(a)):
            if retained is not None:
                app._seed_sensor_last("radar", self.CFG, {"presence": retained})
            app._fire_sensor_triggers("radar", self.CFG, {"presence": live})
        return calls

    def test_the_first_live_true_after_a_retained_false_fires_the_on_rule(self):
        assert self.run_it(False, True) == [("radar", "presence", "true")]
        assert self.run_it(True, False) == [("radar", "presence", "false")]

    def test_a_live_value_equal_to_the_retained_one_fires_nothing_and_no_retained_value_is_a_first_reading(self):
        assert self.run_it(True, True) == [] and self.run_it(None, True) == []

    def test_a_retained_value_never_replaces_a_live_one(self):
        with mock.patch.dict(app._sensor_last, {"radar/presence": True}, clear=True), mock.patch.dict(app._sensor_since, {}, clear=True):
            app._seed_sensor_last("radar", self.CFG, {"presence": False})
            assert app._sensor_last["radar/presence"] is True


class QuietAfterAConfigWrite(unittest.TestCase):
    """A radar sees a phantom target after a settings write: its presence=true is not trusted for a while, then judged again."""
    CFG = {"controls": [{"kind": "sensor", "code": "presence", "vtype": "bool"}]}

    def run_it(self, quiet_for, first, then=None, wait=False):
        import time
        calls = []
        with mock.patch.dict(app._sensor_last, {"radar/presence": False}, clear=True), mock.patch.dict(app._sensor_since, {}, clear=True), \
                mock.patch.dict(app._QUIET, {"radar": (time.time() + quiet_for, "number/x/command")} if quiet_for else {}, clear=True), \
                mock.patch.object(app, "_quiet_pending", set()), mock.patch.object(app._bg, "submit", lambda fn, *a: calls.append(a)):
            app._fire_sensor_triggers("radar", self.CFG, {"presence": first})
            if then is not None:
                app._sensor_last["radar/presence"] = then
            if wait:
                app._QUIET.clear()
                app._after_quiet("radar")
        return calls

    def test_a_true_edge_in_the_quiet_time_fires_nothing_but_a_false_one_does(self):
        assert self.run_it(30, True) == []
        assert self.run_it(30, False) == []                                              # nothing changed (it was false already)
        with mock.patch.dict(app._sensor_last, {"radar/presence": True}, clear=True), mock.patch.dict(app._QUIET, {"radar": (__import__("time").time() + 30, "w")}, clear=True), \
                mock.patch.object(app._bg, "submit", lambda fn, *a: self.__dict__.setdefault("c", []).append(a)):
            app._fire_sensor_triggers("radar", self.CFG, {"presence": False})
        assert self.__dict__["c"] == [("radar", "presence", "false")]                    # the light may still go off

    def test_without_a_quiet_time_a_true_edge_fires_as_always(self):
        assert self.run_it(0, True) == [("radar", "presence", "true")]

    def test_a_phantom_that_is_gone_when_the_time_ends_fires_nothing_a_real_presence_fires_then(self):
        assert self.run_it(30, True, then=False, wait=True) == []
        assert self.run_it(30, True, then=True, wait=True) == [("radar", "presence", "true")]


class Origin(unittest.TestCase):
    """Which rule switched a channel (the watchdog's evidence that a state is a rule's, not a hand's)."""

    def test_the_rule_stays_the_owner_through_its_echo_and_loses_the_channel_to_a_later_change(self):
        import time
        now = time.time()
        for age, owned in ((2, True), (60, False)):
            with mock.patch.dict(app._bind_origin, {("light", "state"): {"src": "radar", "code": "presence", "g": "true", "ts": now - age}}, clear=True):
                app._cmd_report("light", {"state": False}, {"state": True})
                assert (("light", "state") in app._bind_origin) is owned


if __name__ == "__main__":
    unittest.main()
