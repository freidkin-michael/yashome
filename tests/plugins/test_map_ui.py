"""The map screen in a real (headless) Chrome against stubs: draw an LED strip, edit its path, refuse a one-point strip, cancel, save over a stale revision."""
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).parent
CHROME = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
@pytest.mark.parametrize("query,banner,size,rot", [("", "false", "1000,900", "false"), ("?stale=1", "true", "1000,900", "false"), ("?portrait=1", "false", "412,915", "true")])
def test_an_led_strip_is_drawn_edited_and_cancelled_in_the_screen(query, banner, size, rot):
    with tempfile.TemporaryDirectory() as d:
        shutil.copy(HERE / "map_ui" / "harness.html", d)
        shutil.copy(HERE.parent.parent / "plugins" / "tools" / "map" / "ui.js", d); shutil.copy(HERE.parent.parent / "display.js", d)
        page = subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", f"--window-size={size}", "--virtual-time-budget=60000",
                               "--dump-dom", f"file://{d}/harness.html{query}"], capture_output=True, text=True, timeout=90).stdout
    log = page.split('<pre id="out">', 1)[-1].split("</pre>", 1)[0]
    assert "DONE" in log, log
    assert "FAIL" not in log and "REJECTED" not in log and "ReferenceError" not in log, log
    assert f"rot: {rot}" in log                                                         # a portrait phone turns the scheme: every tap, strip, lift and zone corner is checked turned
    assert "grid: true blank: false" in log and "underlay: true 1" in log                       # a house of before: its scheme over the grid, as before
    assert f"banner: {banner}" in log                                                  # a fresh load never says the screen is old; a real mismatch does
    assert 'items: [["led",3,false,[0.3,0.3,0.3]]] puts: 1' in log      # three taps and Done: straight, the points snapped to the horizontal, saved once
    assert "after edit: [2] puts: 2" in log                              # re-tapped three points, undid one: two points, saved again
    assert "one point: mode still path" in log and "after cancel: bar none" in log
    assert "lamp lifted: true no sheet: true" in log and "lamp esc: true" in log and "strip point dragged: true points: 2" in log     # a long press lifts, Esc cancels
    assert "state badges: \u25cf\u25cb | 27\u00b0 45% | 22\u00b0" in log and "ac cool border: true" in log                    # the live state on the icons
    assert "only the changed thing repainted: true true true" in log and "offline: true" in log
    assert 'zones: ["Alpha","Gamma","Narrowcorridor"]' in log and "clash: true" in log and 'renamed: ["Beta","Gamma","Narrowcorridor"]' in log      # zones: named, a clash refused, renamed by its label
    assert 'merged: ["Beta","Beta","Narrowcorridor"] labels: Beta,Narrowcorridor' in log and "edit mode: true" in log                         # one name = one room: one label
    assert 'drives: [[true,"state_right"]]' in log and "turns on: true" in log and "new lamp: 2 true" in log        # what a switch turns on
    assert "mouse shaky drag: moved true panned false" in log and "touch early slide: moved false panned false" in log        # a press on a thing never pans
    assert "touch shaky press then drag: moved true panned false" in log
    assert "label turned in a narrow room: true true" in log                                    # the long name runs along the long side, a wide room keeps it upright
    assert "strip on the yard edge: [340,340]" in log                                           # a shape marked data-snap is a snap target like a wall
    assert "corner dragged: true points 4" in log and "corner added: 5" in log and "delete then undo: 5" in log and "corner deleted: 4" in log   # the outline editor
    assert "cancel keeps the outline: true mode plain" in log
    assert "menu icon: What it turns on|Move|Blink|Remove" in log and "menu room: Rename|Change the outline|Merge with...|Delete the room|Place a device here|New lamp here|New room here" in log
    assert "menu empty: Place a device here|New lamp here|New room here" in log and "menu corner: Delete the point" in log      # the menu of the right button
    assert "right button is no tap: true" in log                                                  # the menu of the right button does not open the sheet as well
    assert "labels clear: true" in log                                                            # a room's name moves off the icons and badges on it
    assert "free walls: 1 lines 1" in log and "wall sheet: true true" in log                                   # the Walls tool: walls only
    assert "opening sheet: true stored 0" in log and 'door: ["door","right",true,true,true,true] drawn 1' in log      # the Opening tool: a stroke beside the wall lands on it
    assert "tap opens it: true" in log and "far from walls: true 1" in log and "undo: right" in log and "undo all: 0" in log
    assert "fan: true true true" in log and "fan closed: true true" in log                      # icons on one spot fan out on a tap of their badge, a tap beside closes it
    assert 'after a stale revision: ["mine","other tab","strip 1"]' in log        # a 409: the other tab's change and this tab's both end up saved


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_a_house_without_a_drawing_gets_a_grid_and_a_working_map():
    with tempfile.TemporaryDirectory() as d:
        shutil.copy(HERE / "map_ui" / "harness.html", d)
        shutil.copy(HERE.parent.parent / "plugins" / "tools" / "map" / "ui.js", d); shutil.copy(HERE.parent.parent / "display.js", d)
        page = subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=1000,900", "--virtual-time-budget=60000",
                               "--dump-dom", f"file://{d}/harness.html?noscheme=1"], capture_output=True, text=True, timeout=90).stdout
    log = page.split('<pre id="out">', 1)[-1].split("</pre>", 1)[0]
    assert "grid: true blank: false" in log, log                                       # the scheme answers 404: a grid, not an error page
    assert 'items: [["led",3,false,[0.3,0.3,0.3]]] puts: 1' in log and "walls: [false,true,true,true]" in log     # strips, zones and walls work as with a drawing


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_the_plan_of_the_flat_is_an_underlay_set_from_its_sheet():
    with tempfile.TemporaryDirectory() as d:
        shutil.copy(HERE / "map_ui" / "harness.html", d)
        shutil.copy(HERE.parent.parent / "plugins" / "tools" / "map" / "ui.js", d); shutil.copy(HERE.parent.parent / "display.js", d)
        page = subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=1000,900", "--virtual-time-budget=60000",
                               "--dump-dom", f"file://{d}/harness.html?underlay=1"], capture_output=True, text=True, timeout=90).stdout
    log = page.split('<pre id="out">', 1)[-1].split("</pre>", 1)[0]
    assert "underlay: true 0.5" in log, log                                            # shown, half see-through
    assert "strip on the yard edge: [340,340]" in log                                  # its SVG walls and shapes are snap targets, placed on the frame
    assert "underlay set: 0.4 430 215 0.4 50%" in log                                  # opacity, and the size about its middle, saved and drawn


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
@pytest.mark.parametrize("query,first", [("?layers=1", "[true,true,[2,1]]"), ("?layers=1&was-off=1", "[false,true,[0,1]]")])
def test_wires_and_ir_and_radio_are_two_layers_kept_per_browser(query, first):
    with tempfile.TemporaryDirectory() as d:
        shutil.copy(HERE / "map_ui" / "harness.html", d)
        shutil.copy(HERE.parent.parent / "plugins" / "tools" / "map" / "ui.js", d); shutil.copy(HERE.parent.parent / "display.js", d)
        page = subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=1000,900", "--virtual-time-budget=60000",
                               f"--user-data-dir={d}/chrome", "--dump-dom", f"file://{d}/harness.html{query}"], capture_output=True, text=True, timeout=90).stdout
    log = page.split('<pre id="out">', 1)[-1].split("</pre>", 1)[0]
    errors = [ln for ln in log.splitlines() if ln.startswith("ERROR") and "ResizeObserver loop" not in ln]     # that one is the browser's note
    assert "DONE" in log and not errors and "REJECTED" not in log, log
    assert f"layers: {first}" in log                          # wires: the wiring entry and the lamp's link (solid); IR: the blaster line (dashed), the rule none
    if "was-off" not in query:
        assert 'wires off: [[0,1],"0"]' in log and 'radio off: [[0,0],"0"]' in log and "both on: [2,1]" in log


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_a_scene_draws_a_copy_and_stores_nothing():
    with tempfile.TemporaryDirectory() as d:
        shutil.copy(HERE / "map_ui" / "harness.html", d)
        shutil.copy(HERE.parent.parent / "plugins" / "tools" / "map" / "ui.js", d); shutil.copy(HERE.parent.parent / "display.js", d)
        page = subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=1000,900", "--virtual-time-budget=20000",
                               "--dump-dom", f"file://{d}/harness.html?readonly"], capture_output=True, text=True, timeout=90).stdout
    log = page.split('<pre id="out">', 1)[-1].split("</pre>", 1)[0]
    assert "DONE" in log and "FAIL" not in log and "ERROR" not in log, log
    assert "scene: true 1 taps off: true" in log, log
    assert "scene writes: [] house map untouched: true live view kept: true" in log, log



@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_a_wire_only_the_wiring_knows_is_drawn_and_a_bound_load_is_its_device():
    with tempfile.TemporaryDirectory() as d:
        shutil.copy(HERE / "map_ui" / "harness.html", d)
        shutil.copy(HERE.parent.parent / "plugins" / "tools" / "map" / "ui.js", d); shutil.copy(HERE.parent.parent / "display.js", d)
        page = subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=1000,900", "--virtual-time-budget=60000",
                               f"--user-data-dir={d}/chrome", "--dump-dom", f"file://{d}/harness.html?layers=1&onlywire=1"], capture_output=True, text=True, timeout=90).stdout
    log = page.split('<pre id="out">', 1)[-1].split("</pre>", 1)[0]
    assert "DONE" in log, log
    assert "onlywire: [true,false,true]" in log           # the corridor lamp's wire drawn; the bound load no icon; its wire ends at the AC
    assert "layers: [true,true,[4,1]]" in log              # solid: the AC feed, the lamp's link, the corridor lamp, the bound load; dashed: IR only


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_a_name_with_no_free_side_drops_its_room_before_it_hides():
    with tempfile.TemporaryDirectory() as d:
        shutil.copy(HERE / "map_ui" / "harness.html", d)
        shutil.copy(HERE.parent.parent / "plugins" / "tools" / "map" / "ui.js", d); shutil.copy(HERE.parent.parent / "display.js", d)
        page = subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=2100,1500", "--virtual-time-budget=60000",
                               f"--user-data-dir={d}/chrome", "--dump-dom", f"file://{d}/harness.html?labels=1"], capture_output=True, text=True, timeout=90).stdout
    log = page.split('<pre id="out">', 1)[-1].split("</pre>", 1)[0]
    assert "DONE" in log, log
    assert 'labels: big true' in log and '["k4","Kettle socket",true],["k5","Hood",true]] overlap false title Kitchen kettle socket' in log, log    # shorter, on free ground, not hidden; the icon keeps the full name


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
def test_restricted_a_watcher_drags_nothing_the_session_let_edit_moves_and_saves():
    with tempfile.TemporaryDirectory() as d:
        shutil.copy(HERE / "map_ui" / "harness.html", d)
        shutil.copy(HERE.parent.parent / "plugins" / "tools" / "map" / "ui.js", d); shutil.copy(HERE.parent.parent / "display.js", d)
        page = subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--window-size=1000,900", "--virtual-time-budget=60000",
                               f"--user-data-dir={d}/chrome", "--dump-dom", f"file://{d}/harness.html?restricted=1"], capture_output=True, text=True, timeout=90).stdout
    log = page.split('<pre id="out">', 1)[-1].split("</pre>", 1)[0]
    assert "DONE" in log, log
    assert "watcher: [[false,0],[false,0],[false,false,false,false]]" in log, log     # mouse, finger: no move, no save; no owner's tool
    assert "editor: [[true,1],[true,1],[true,true,true,false]]" in log, log          # each drag moved and saved once; the tools, never the plan picture


@pytest.mark.skipif(CHROME is None, reason="no Chrome here")
@pytest.mark.parametrize("query,size", [("?zoom=1", "1000,900"), ("?zoom=1&portrait=1", "412,915")])
def test_a_pan_or_a_zoom_never_takes_the_plan_off_the_pane(query, size):
    with tempfile.TemporaryDirectory() as d:
        shutil.copy(HERE / "map_ui" / "harness.html", d)
        shutil.copy(HERE.parent.parent / "plugins" / "tools" / "map" / "ui.js", d); shutil.copy(HERE.parent.parent / "display.js", d)
        page = subprocess.run([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", f"--window-size={size}", "--virtual-time-budget=60000",
                               f"--user-data-dir={d}/chrome", "--dump-dom", f"file://{d}/harness.html{query}"], capture_output=True, text=True, timeout=90).stdout
    log = page.split('<pre id="out">', 1)[-1].split("</pre>", 1)[0]
    errors = [ln for ln in log.splitlines() if ln.startswith(("ERROR", "FAIL")) and "ResizeObserver loop" not in ln]
    assert "DONE" in log and not errors, log
    z = json.loads(log.split("zoom: ", 1)[1].splitlines()[0])
    assert z["flung"] >= 0.49 and z["inCover"] >= 0.49, z            # a fling or a zoom in leaves half the pane on the plan (a phone showed a black pane)
    assert 0.45 <= z["outW"] <= 0.55 and z["inW"] <= 8.05, z          # zoom out stops at half the fitted plan, in at 8 times it
    assert z["jump"] <= 6 and not z["nan"], z                          # one finger of a pinch lifted: the other pans 3 px on, the plan does not jump
