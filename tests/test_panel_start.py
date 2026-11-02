"""tools/panel-start/panel_start.py, the host helper of the panels' launcher watchdog, with adb stubbed: it refuses every
other verb, argument count, id and address, and runs only `adb [connect] ... am start` of the launcher."""
import importlib.util
import os
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("panel_start", os.path.join(ROOT, "tools", "panel-start", "panel_start.py"))
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)

DOCK, VPN = "172.31", "10.99"         # private, but a docker bridge and a VPN (built: no LAN address in the code)
IP_OUT = f"""1: lo    inet 127.0.0.1/8 scope host lo
2: docker0    inet {DOCK}.0.1/16 brd {DOCK}.255.255 scope global docker0
3: br-0123456789ab    inet {DOCK}.1.1/24 scope global br-0123456789ab
4: wg0    inet {VPN}.0.3/24 scope global wg0
5: ethusb0    inet 192.0.2.206/24 brd 192.0.2.255 scope global ethusb0
"""
PAIRED = {"tab1": {"status": "bound", "ip": "192.0.2.51", "caps": {"adb": {"port": 33217}}},
          "tab2": {"status": "pending", "ip": "192.0.2.52"},
          "far": {"status": "bound", "ip": "8.8.8.8"},
          "dock": {"status": "bound", "ip": f"{DOCK}.1.5"},
          "vpn": {"status": "bound", "ip": f"{VPN}.0.7"}}


class Helper(unittest.TestCase):
    def setUp(self):
        self.calls, self.devices = [], "List of devices attached\n192.0.2.51:33217\tdevice\n"
        self.connect_ok, self.am = False, (0, "Starting: Intent { cmp=dev.freidkin.panel/.Launcher }")
        self.nets = ps.lan_nets(IP_OUT)
        self._orig = ps.run_adb
        ps.run_adb = self.fake

    def tearDown(self):
        ps.run_adb = self._orig

    def fake(self, args, timeout):
        self.calls.append(args)
        if args == ["devices"]:
            return subprocess.CompletedProcess(args, 0, self.devices, "")
        if args[0] == "connect":
            if self.connect_ok:
                self.devices += f"{args[1]}\tdevice\n"
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, self.am[0], self.am[1], "")

    def ask(self, line):
        return ps.handle(line, PAIRED, self.nets)

    def test_the_house_lan_is_the_private_net_of_a_real_interface_only(self):
        self.assertEqual([str(n) for n in self.nets], ["192.0.2.0/24"])

    def test_anything_else_is_refused_before_adb(self):
        for line in ("", "start-launcher", "start-launcher tab1 extra", "stop-launcher tab1", "start-launcher ../x",
                     "start-launcher nobody", "start-launcher tab2", "start-launcher far", "start-launcher dock", "start-launcher vpn"):
            self.assertTrue(self.ask(line).get("refused"), line)
        self.assertEqual(self.calls, [])

    def test_a_bound_tablet_gets_am_start_of_the_launcher_only(self):
        self.assertEqual(self.ask("start-launcher tab1"), {"ok": True})
        self.assertEqual(self.calls, [["devices"], ["-s", "192.0.2.51:33217", "shell", "am", "start", "-a", "android.intent.action.MAIN", "-c", "android.intent.category.HOME"]])

    def test_not_connected_it_connects_on_5555_then_its_own_port(self):
        self.devices, self.connect_ok = "List of devices attached\n", True
        self.assertEqual(self.ask("start-launcher tab1"), {"ok": True})
        self.assertEqual(self.calls[:3], [["devices"], ["connect", "192.0.2.51:5555"], ["devices"]])
        self.assertEqual(self.calls[-1][:2], ["-s", "192.0.2.51:5555"])

    def test_no_connection_is_no_way_in_and_an_error_is_a_failed_try(self):
        self.devices = "List of devices attached\n"
        r = self.ask("start-launcher tab1")
        self.assertTrue(r["noadb"])
        self.assertEqual([c for c in self.calls if c[0] == "connect"], [["connect", "192.0.2.51:5555"], ["connect", "192.0.2.51:33217"]])
        self.devices, self.calls, self.am = "List of devices attached\n192.0.2.51:33217\tdevice\n", [], (0, "Error: Activity class does not exist")
        self.assertEqual(self.ask("start-launcher tab1")["ok"], False)

    def test_one_short_line_within_its_time(self):
        r, w = os.pipe()
        os.write(w, b"start-launcher tab1 k\n")
        self.assertEqual(ps.read_line(r), "start-launcher tab1 k")
        os.write(w, b"x" * 300 + b"\n")
        self.assertIsNone(ps.read_line(r))                                  # longer than 256 bytes
        os.close(w)
        os.close(r)
        r, w = os.pipe()
        old, ps.READ_S = ps.READ_S, 0.2
        try:
            self.assertIsNone(ps.read_line(r))                              # nothing within the time
        finally:
            ps.READ_S = old
            os.close(w)
            os.close(r)


if __name__ == "__main__":
    unittest.main()
