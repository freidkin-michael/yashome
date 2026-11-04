import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugins", "tools", "voice", "service", "vad"))
from voice_room import RoomGate  # noqa: E402


class Gate(unittest.TestCase):
    def test_the_first_stream_of_a_room_wins(self):
        g = RoomGate(1.5)
        self.assertTrue(g.admit("bedroom", "192.0.2.1", 100.0))
        self.assertFalse(g.admit("bedroom", "192.0.2.2", 100.8))           # the other satellite, 0.8 s later: the same phrase
        self.assertTrue(g.admit("bedroom", "192.0.2.1", 101.0))            # the winner may go on
        self.assertTrue(g.admit("bedroom", "192.0.2.2", 105.0))            # a new burst: anyone

    def test_other_rooms_and_no_room_are_independent(self):
        g = RoomGate(1.5)
        self.assertTrue(g.admit("salon", "192.0.2.3", 100.0))
        self.assertTrue(g.admit("bedroom", "192.0.2.1", 100.1))
        self.assertTrue(g.admit("", "192.0.2.4", 100.2))
        self.assertTrue(g.admit("", "192.0.2.5", 100.3))


if __name__ == "__main__":
    unittest.main()
