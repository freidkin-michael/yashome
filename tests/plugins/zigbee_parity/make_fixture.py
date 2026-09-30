# regenerate: python3 make_fixture.py bridge_devices.json, then run.py through the core that had Zigbee built in for expected.json
"""A made-up zigbee2mqtt bridge/devices with the shapes a house has (nobody's real devices)."""
import json
import sys


def bin_(p, access=1):
    return {"type": "binary", "property": p, "access": access}


def num(p, access=1, **kw):
    return {"type": "numeric", "property": p, "access": access, **kw}


def dev(i, name, exposes, typ="Router", model="MODEL", clusters=("genBasic", "genOnOff")):
    return {"friendly_name": name, "ieee_address": f"0x00000000000001{i:02x}", "type": typ, "network_address": 100 + i,
            "definition": {"model": model, "vendor": "Example", "exposes": exposes},
            "endpoints": {"1": {"clusters": {"input": list(clusters), "output": []}}}}


devs = [
    {"friendly_name": "Coordinator", "type": "Coordinator", "ieee_address": "0x0000000000000000"},
    dev(1, "Hall 1 gang", [{"type": "switch", "features": [bin_("state", 7)]}], model="SW1"),
    dev(2, "Hall 3 gang", [{"type": "switch", "endpoint": e, "features": [bin_(f"state_{e}", 7)]} for e in ("left", "center", "right")], model="SW3"),
    dev(3, "Living dimmer", [{"type": "light", "features": [bin_("state_l1", 7), num("brightness_l1", 7, value_max=1000),
                                                           bin_("state_l2", 7), num("brightness_l2", 7, value_max=1000),
                                                           num("min_brightness_l1", 7)]}], model="DIM2"),
    dev(4, "Heater plug", [{"type": "switch", "features": [bin_("state", 7)]}, num("power"), num("current"), num("voltage"),
                           num("energy"), num("linkquality")], model="PLUG"),
    dev(5, "Tuya dimmer", [{"type": "light", "features": [bin_("state", 7), num("brightness", 7, value_max=254)]}],
        model="EF00DIM", clusters=("genBasic", "manuSpecificTuya")),
    dev(6, "\u043f\u0443\u043b\u044c\u0442 1", [{"type": "enum", "property": "action", "values": ["1_single", "1_double", "2_hold"]},
                              num("battery"), num("linkquality")], typ="EndDevice", model="REMOTE4"),
    dev(7, "Corridor motion", [bin_("occupancy"), num("illuminance"), num("battery"), num("occupancy_timeout", 7)], typ="EndDevice", model="PIR"),
    dev(8, "Kitchen climate", [num("temperature"), num("humidity"), num("battery")], typ="EndDevice", model="TH"),
    dev(9, "Door contact", [bin_("contact"), num("battery")], typ="EndDevice", model="DOOR"),
    dev(10, "Sink leak", [bin_("water_leak"), bin_("tamper"), num("battery")], typ="EndDevice", model="LEAK"),
    dev(11, "0x000000000000010b", [num("linkquality")], model=None),
    dev(12, "USB router", [], model="ROUTER"),
    dev(13, "bad|name", [{"type": "switch", "features": [bin_("state", 7)]}], model="SW1"),
    dev(14, "Garden lights", [{"type": "switch", "features": [bin_("state", 7)]}, {"type": "enum", "property": "action", "values": ["single"]}], model="SWBTN"),
]
json.dump(devs, open(sys.argv[1], "w"), ensure_ascii=True, indent=1)
print(len(devs), "devices")
