"""
Yashome backend: devices, transports, rules, REST + WebSocket for the dashboard.

Everything is local and goes through MQTT:
- generic MQTT      -> mqtt_devices.json (ESPHome, Tasmota, your own firmware)
- anything else     -> plug-ins (Zigbee, TVs, IR ...: docs/plugins.md)

Rules (bindings, schedules, auto-off) run here, on the server, not on the devices.

Run:
    uvicorn app:app --host 0.0.0.0 --port 8765
"""
import asyncio
import contextlib
import copy
import hashlib
import ipaddress
import json
import contextvars
import logging
import math
import operator
import os
import re
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
import hmac
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.routing import Match
from pydantic import BaseModel, ConfigDict, Field


# The core lives in core/*.py, one file per area, run here in this order in this module's own namespace:
# one module as before (plug-ins do `import app as core`, tests patch app.<name>), split only to be read.
CORE_PARTS = ("base", "hooks", "devices_file", "migrations", "status", "timers", "bindings_run", "http_auth", "mqtt", "mqtt_devices", "startup", "api_devices", "rules", "rooms", "device_keys", "esphome", "web", "plugin_api", "services", "guest", "loader", "wiring")
for _part in CORE_PARTS:
    _path = Path(__file__).resolve().parent / "core" / f"{_part}.py"
    exec(compile(_path.read_text(encoding="utf-8"), str(_path), "exec"), globals())
del _part, _path


def core_source() -> str:
    """app.py and every part of core/ as one text, in the order they run (what one file used to be)."""
    here = Path(__file__).resolve().parent
    return "".join([(here / "app.py").read_text(encoding="utf-8")]
                   + [(here / "core" / f"{p}.py").read_text(encoding="utf-8") for p in CORE_PARTS])
