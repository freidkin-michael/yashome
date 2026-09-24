"""hello: the smallest complete Yashome plug-in. Copy the directory into HOME_PLUGINS_DIR
(default: plugins/ next to app.py), restart the backend, and it is live.

It shows every kind of hook once: a transport with a device of its own, a rule action,
a settings section, a REST route and an MQTT topic. ui.js adds a tab; i18n.json translates it."""
import app as core

PLUGIN = {"name": "Hello", "version": "1.0.0"}

# state this plug-in keeps in settings.json under its own key
_seen: dict = dict(core._cfg_section("hello", {"greetings": 0}))
core.SETTINGS_SECTIONS["hello"] = lambda: _seen


# 1) a transport: how to switch a device whose "transport" is "hello"
def _apply(cfg, code, val, kind):
    core.log.info(f"[hello] {cfg['id']}/{code} -> {val}")
    st = core._status_copy(cfg["id"], {"online": True, "values": {}, "raw": {}})
    st["values"][code] = bool(val)
    st["online"] = True
    core._store_status(cfg["id"], st)


core.TRANSPORTS["hello"] = _apply
core.register_device({"id": "hello_lamp", "name": "Hello lamp", "transport": "hello", "category": "switch",
                      "controls": [{"kind": "switch", "code": "power", "label": "Power"}]})


# 2) a rule action: "greet" - a button can say hello
def _validate(body):
    return {"target": "hello", "code": "greet", "action": "greet", "text": str(body.get("text") or "hello")[:64]}


def _run(b):
    _seen["greetings"] = _seen.get("greetings", 0) + 1
    core.log.info(f"[hello] {b.get('text')} (#{_seen['greetings']})")
    return {"action": "greet", "text": b.get("text")}


core.BINDING_ACTIONS["greet"] = {
    "validate": _validate, "run": _run,
    "describe": lambda b: {"type": "hello", "action": "greet", "text": b.get("text"), "title": f"say {b.get('text')}"},
}
core.RULE_THEN["hello"] = lambda then: _run(then)


# 3) a route and an MQTT topic
@core.app.get("/api/hello")
def hello():
    return {"greetings": _seen.get("greetings", 0)}


core.MQTT_SUBSCRIPTIONS.append("hello/ping")
core.MQTT_HANDLERS.append((("hello", "ping"), lambda parts, payload, retain: _run({"text": "ping"})))
