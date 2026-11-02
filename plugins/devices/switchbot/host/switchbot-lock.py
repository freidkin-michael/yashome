#!/usr/bin/env python3
"""SwitchBot lock <-> MQTT bridge: reads the lock's BLE adverts, locks and unlocks over its encrypted
BLE session. One lock per instance (switchbot-lock@<id>), topics switchbot/<id>/... (README.md)."""
import asyncio
import json
import logging
import os
import re
import signal
import sys
import time

import paho.mqtt.client as mqtt
from bleak import BleakScanner
from switchbot import parse_advertisement_data
from switchbot.const import SwitchbotModel
from switchbot.devices import lock as sblock

log = logging.getLogger("switchbot-lock")

MODELS = {"lock": SwitchbotModel.LOCK, "lock_pro": SwitchbotModel.LOCK_PRO, "lock_ultra": SwitchbotModel.LOCK_ULTRA}
HEARTBEAT_S = 60
STALE_S = 180
DEAD_SCAN_S = 2 * STALE_S
ID_RE = r"[a-z0-9][a-z0-9_-]{0,31}"


def refuse(why):
    print(f"switchbot-lock: {why}", file=sys.stderr)
    raise SystemExit(2)


def config(env=os.environ) -> dict:
    """The instance's settings; a wrong one exits 2 (the unit does not restart on it)."""
    miss = [k for k in ("SWITCHBOT_LOCK_ID", "SWITCHBOT_LOCK_MAC", "SWITCHBOT_LOCK_KEY_ID", "SWITCHBOT_LOCK_ENC_KEY")
            if not env.get(k)]
    if miss:
        refuse(f"missing {', '.join(miss)} in the env file")
    lid, mac = env["SWITCHBOT_LOCK_ID"], env["SWITCHBOT_LOCK_MAC"].upper()
    model = (env.get("SWITCHBOT_LOCK_MODEL") or "lock_ultra").lower()
    if not re.fullmatch(ID_RE, lid):
        refuse("SWITCHBOT_LOCK_ID: lowercase latin letters, digits, - and _, as in the dashboard")
    if not re.fullmatch(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}", mac):
        refuse("SWITCHBOT_LOCK_MAC: AA:BB:CC:DD:EE:FF")
    if not re.fullmatch(r"[0-9a-fA-F]{32}", env["SWITCHBOT_LOCK_ENC_KEY"]):
        refuse("SWITCHBOT_LOCK_ENC_KEY: 32 hex digits (switchbot-get-key.py)")
    if model not in MODELS:
        refuse("SWITCHBOT_LOCK_MODEL: " + " | ".join(MODELS))
    return {"id": lid, "mac": mac, "key_id": env["SWITCHBOT_LOCK_KEY_ID"], "enc_key": env["SWITCHBOT_LOCK_ENC_KEY"],
            "model": MODELS[model], "base": f"switchbot/{lid}",
            "host": env.get("MQTT_HOST") or "localhost", "port": int(env.get("MQTT_PORT") or 1883),
            "user": env.get("MQTT_SWITCHBOT_USER") or "switchbot", "password": env.get("MQTT_SWITCHBOT_PASSWORD") or ""}


def command(payload: str):
    """lock | unlock (also LOCKED/UNLOCKED, on/off), else None."""
    cmd = payload.strip().lower()
    cmd = {"locked": "lock", "unlocked": "unlock", "on": "lock", "off": "unlock"}.get(cmd, cmd)
    return cmd if cmd in ("lock", "unlock") else None


def state_of(d: dict) -> dict:
    """The decoded advert as the bridge publishes it; door is None without a door sensor."""
    door = d.get("door_open")
    return {"state": str(d.get("status", "")).split(".")[-1], "battery": d.get("battery"),
            "door": None if door is None else ("open" if door else "closed"), "calibrated": d.get("calibration")}


class Bridge:
    def __init__(self, cfg):
        self.cfg, self.base = cfg, cfg["base"]
        self.loop = self.device = self.last_parsed = self.scanner = None
        self.last_adv = self.last_pub = 0.0
        self.last_state, self.online, self.dead = {}, False, False
        self.cmd_q = asyncio.Queue()
        self.mq = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"switchbot-{cfg['id']}")
        self.mq.username_pw_set(cfg["user"], cfg["password"])
        self.mq.will_set(self.base + "/availability", "offline", retain=True)
        self.mq.on_connect = self._on_mq_connect
        self.mq.on_message = self._on_mq_message

    def _on_mq_connect(self, client, userdata, flags, rc, props=None):
        log.info("mqtt connected rc=%s", rc)
        client.subscribe(self.base + "/set")
        client.publish(self.base + "/availability", "online" if self.online else "offline", retain=True)

    def _on_mq_message(self, client, userdata, msg):
        if msg.retain:                      # a stale command left on the broker never moves the bolt
            return
        cmd = command(msg.payload.decode(errors="ignore"))
        if cmd:
            self.loop.call_soon_threadsafe(self.cmd_q.put_nowait, cmd)
        else:
            log.warning("unknown command %r (want lock|unlock)", msg.payload[:20])

    def pub(self, topic, payload, retain=True):
        self.mq.publish(self.base + topic, payload, retain=retain)

    def set_online(self, on):
        if on != self.online:
            self.online = on
            self.pub("/availability", "online" if on else "offline")
            log.info("availability -> %s", "online" if on else "offline")

    def publish_state(self, d):
        cur = state_of(d)
        now = time.time()
        if cur == self.last_state and now - self.last_pub < HEARTBEAT_S:
            return
        self.last_state, self.last_pub = cur, now
        self.pub("/state", cur["state"])
        self.pub("/battery", str(cur["battery"]))
        if cur["door"] is not None:
            self.pub("/door", cur["door"])
        self.pub("", json.dumps({**cur, "ts": int(now)}))

    def _on_adv(self, device, adv):
        if device.address.upper() != self.cfg["mac"]:
            return
        parsed = parse_advertisement_data(device, adv, self.cfg["model"])
        if not parsed or not parsed.data.get("data"):
            return
        self.device, self.last_parsed, self.last_adv = device, parsed, time.time()
        self.set_online(True)
        self.publish_state(parsed.data["data"])

    async def start_scan(self):
        self.scanner = BleakScanner(detection_callback=self._on_adv)
        await self.scanner.start()

    async def stop_scan(self):
        if self.scanner:
            try:
                await self.scanner.stop()
            except Exception as e:
                log.debug("scanner stop: %s", e)
            self.scanner = None

    def result(self, cmd, ok, err=None):
        r = {"cmd": cmd, "ok": bool(ok), "ts": int(time.time())}
        if err:
            r["err"] = err[:200]
        self.pub("/result", json.dumps(r), retain=False)

    async def do_cmd(self, cmd):
        if self.device is None:
            return self.result(cmd, False, "the lock was not heard on BLE yet")
        await self.stop_scan()
        try:
            wl = sblock.SwitchbotLock(self.device, self.cfg["key_id"], self.cfg["enc_key"], model=self.cfg["model"])
            # pySwitchbot 2.7 polls a lock with no status first and fails on an Ultra's answer: seed it from the advert
            if self.last_parsed is not None:
                wl.update_from_advertisement(self.last_parsed)
            ok = await (wl.lock() if cmd == "lock" else wl.unlock_without_unlatch())
            log.info("cmd %s -> %s", cmd, ok)
            self.result(cmd, ok)
        except Exception as e:
            log.exception("cmd %s failed", cmd)
            self.result(cmd, False, str(e))
        finally:
            self.last_state = {}            # the next advert is published, whatever the command did
            await self.start_scan()

    async def run(self):
        self.loop = asyncio.get_running_loop()
        self.mq.connect_async(self.cfg["host"], self.cfg["port"], keepalive=30)
        self.mq.loop_start()
        await self.start_scan()
        log.info("bridge up: lock %s at %s", self.cfg["id"], self.cfg["mac"])
        self.last_adv = time.time()
        stop = asyncio.Event()
        for s in (signal.SIGINT, signal.SIGTERM):
            self.loop.add_signal_handler(s, stop.set)
        while not stop.is_set():
            try:
                await self.do_cmd(await asyncio.wait_for(self.cmd_q.get(), timeout=5))
            except asyncio.TimeoutError:
                pass
            if self.online and time.time() - self.last_adv > STALE_S:
                self.set_online(False)
            if time.time() - self.last_adv > DEAD_SCAN_S:      # a dead scan (bluetoothd restarted): systemd restarts us
                log.error("no advert for %d s: exiting for a fresh scan", DEAD_SCAN_S)
                self.dead = True
                break
        await self.stop_scan()
        self.pub("/availability", "offline")
        self.mq.loop_stop()
        self.mq.disconnect()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    bridge = Bridge(config())
    asyncio.run(bridge.run())
    sys.exit(1 if bridge.dead else 0)
