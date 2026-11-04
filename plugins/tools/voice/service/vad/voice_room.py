"""One room, one answer: two satellites of a room that start a phrase within `window` seconds are one phrase heard twice."""


class RoomGate:
    def __init__(self, window=1.5):
        self.window = window
        self.first = {}                 # room -> (time, ip) of the stream that was admitted

    def admit(self, room, ip, now):
        """True for the first stream of a burst (and for later streams of the same address); False for another address of the
        same room that starts within the window. An address without a room is its own room."""
        if not room:
            return True
        t, who = self.first.get(room, (0.0, ""))
        if who and who != ip and now - t < self.window:
            return False
        self.first[room] = (now, ip)
        return True
