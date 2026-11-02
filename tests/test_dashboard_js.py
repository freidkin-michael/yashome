"""The dashboard's keyboard and pill code run by node: the page's own functions (sendControl included)
with a stub DOM and a fake API (CI has no browser). Skipped, and said so, when node is missing.
openModal / closeModal need the whole page: their wiring is asserted in test_review_cb DashboardText."""
import json
import re
import shutil
import subprocess
import unittest
from tools.webpage import page as _dashboard_page   # index.html with web/app.css and web/js/*.js inline

PAGE = _dashboard_page()
FUNCS = ("dimFloor", "dimCeil", "dimStep", "switchOn", "flushKeySteps", "dotKeys", "markLife", "render",
         "attachDimZones", "attachDimmerDot", "sendControl")
CONSTS = ("_keyStep", "_swIntent")

STUBS = r"""
const sent = [], pending = {}, STATE = {}, optimistic = {}, _sentWait = {}, fails = [];
let LEVEL = 50, ON = {}, FAIL = false, openModalId = null, renderPage = () => {};
const BY_ID = {dim1: {controls: [{code: 'power', kind: 'switch', confirm: true},
                                  {code: 'brightness', kind: 'bright', min: 0, max: 255, confirm: true}]}};
const HomePlugins = {holdMs: () => 0};
const i18n = s => s;
let onCards = () => {};
function expireWaitLater(){} function updateCards(){ onCards(); } function notifyFail(m){ fails.push(m); }
async function api(path, opts){
  const b = JSON.parse(opts.body); sent.push([b.code, b.value]);
  if (FAIL) throw new Error('409 the device is offline');
  return {ok: true};
}
function isOn(id, code){ return !!ON[id + '/' + code]; }
function favLevel(d, b){ const p = pending[d.id + '/' + b.code]; return p ? p.v : LEVEL; }
function setDimFill(){}
function mk(id, cls){
  const ls = {}, el = {id: id || '', tabIndex: -2, attrs: {}, kids: [], disabled: false, _face: null, style: {},
    classList: {s: new Set(cls || []), contains(c){ return this.s.has(c); }, add(c){ this.s.add(c); }},
    setAttribute(k, v){ this.attrs[k] = String(v); }, getAttribute(k){ return this.attrs[k]; },
    addEventListener(t, f){ ls[t] = f; }, fire(t, ev){ ls[t](ev); },
    getBoundingClientRect(){ return {left: 0, width: 100}; }, setPointerCapture(){}, releasePointerCapture(){},
    focus(){ document.activeElement = el; }, contains(x){ return x === el || el.kids.includes(x); },
    querySelectorAll(sel){ return el.kids.filter(k => sel.split(',').some(p => (p.trim().startsWith('.')
      ? k.classList.contains(p.trim().slice(1)) : k.tag === p.trim()))); }};
  return el;
}
const byId = {};
const document = {activeElement: null, body: {}, getElementById: id => byId[id] || null};
const key = (k, repeat) => ({key: k, repeat: !!repeat, preventDefault(){}, stopPropagation(){}});
const ptr = x => ({pointerId: 1, clientX: x, stopPropagation(){}});
const py = y => ({pointerId: 1, clientY: y, stopPropagation(){}});
const tap = (p, x) => { p.fire('pointerdown', ptr(x)); p.fire('pointerup', ptr(x)); };
const wait = ms => new Promise(r => setTimeout(r, ms));
const d = {id: 'dim1', name: 'Dimmer'}, s = {code: 'power', label: ''}, b = {code: 'brightness', floor: 15, ceil: 60};
function setup(on, offline){
  sent.length = 0; FAIL = false;
  for (const o of [pending, _swIntent, STATE]) for (const k of Object.keys(o)) delete o[k];
  ON = {'dim1/power': on}; LEVEL = 50;
  byId['c_dim1'] = mk('c_dim1', offline ? ['card', 'offline'] : ['card']);
  const w = mk(); dotKeys(w, d, s, b);
  const pill = mk(); attachDimZones(pill, d, s, b);
  return [w, pill];
}
"""

SCENARIOS = r"""
(async () => {
  const out = {};
  let [w, pill] = setup(true);
  w.fire('keydown', key('ArrowUp')); await wait(300); out.up = sent.slice();
  [w, pill] = setup(true); w.fire('keydown', key('ArrowDown')); await wait(300); out.down = sent.slice();
  [w, pill] = setup(false); w.fire('keydown', key('ArrowUp')); await wait(300); out.up_off = sent.slice();
  [w, pill] = setup(true); w.fire('keydown', key('Enter')); w.fire('keydown', key('Enter', true)); w.fire('keydown', key(' ', true));
  out.enter_held = sent.slice();
  [w, pill] = setup(true, true); w.fire('keydown', key('ArrowUp')); w.fire('keydown', key('Enter')); await wait(300);
  out.offline = sent.slice();
  [w, pill] = setup(true); w.fire('keydown', key('ArrowUp')); byId['c_dim1'].classList.add('offline'); await wait(300);
  out.gone_meanwhile = [sent.slice(), 'dim1/brightness' in pending];
  [w, pill] = setup(true); w.fire('keydown', key('ArrowUp')); w.fire('keydown', key('Enter')); await wait(300);
  out.up_then_enter = sent.slice();
  [w, pill] = setup(true); w.fire('keydown', key('Enter')); w.fire('keydown', key('ArrowUp')); await wait(300);
  out.enter_then_up = sent.slice();
  [w, pill] = setup(true); for (let i = 0; i < 5; i++) w.fire('keydown', key('ArrowUp', i > 0)); await wait(300);
  out.held_up = sent.slice();
  [w, pill] = setup(true); LEVEL = 60; w.fire('keydown', key('ArrowUp')); await wait(300); out.at_top = sent.slice();
  // a report after the command (no cmd_wait, past 1.5 s) wins over what was asked
  [w, pill] = setup(true); w.fire('keydown', key('Enter')); await wait(1600); sent.length = 0;
  w.fire('keydown', key('Enter')); out.report_wins = sent.slice();
  [w, pill] = setup(true); w.fire('keydown', key('Enter')); STATE.dim1 = {cmd_wait: {power: false}}; await wait(1600);
  sent.length = 0; w.fire('keydown', key('Enter')); out.in_flight = sent.slice();
  // a failed command is not what was asked: the next Enter tries the same again
  [w, pill] = setup(true); FAIL = true; w.fire('keydown', key('Enter')); await wait(20); FAIL = false;
  w.fire('keydown', key('Enter')); await wait(20); out.failed = [sent.slice(), fails.length > 0];
  // success, failure, success: the failed one gives back the record it replaced
  [w, pill] = setup(true); w.fire('keydown', key('Enter')); FAIL = true; w.fire('keydown', key('Enter')); await wait(20);
  FAIL = false; sent.length = 0; w.fire('keydown', key('Enter')); out.fail_restores = sent.slice();
  // a late failure does not take away a newer record
  [w, pill] = setup(false); FAIL = true; const late = sendControl('dim1', 'power', false); FAIL = false;
  _swIntent['dim1/power'] = {on: true, t: Date.now()}; await late; out.late_keeps = switchOn('dim1', 'power');
  // only a true / false command is a record
  [w, pill] = setup(true); sendControl('dim1', 'brightness', 0); sendControl('dim1', 'power', 'true'); sendControl('dim1', 'power', 0);
  await wait(20); out.records = Object.keys(_swIntent);
  // after Enter (off), a pill step switches the light on again
  [w, pill] = setup(true); w.fire('keydown', key('Enter')); tap(pill, 90); await wait(20); out.enter_then_step = sent.slice();
  // the pill: a waiting arrow goes first; its centre builds on what was asked by any path
  [w, pill] = setup(true); w.fire('keydown', key('ArrowUp')); tap(pill, 50); await wait(300); out.pill_flush = sent.slice();
  [w, pill] = setup(true); tap(pill, 50); w.fire('keydown', key('Enter')); out.pill_then_enter = sent.slice();
  [w, pill] = setup(true); w.fire('keydown', key('Enter')); tap(pill, 50); out.enter_then_pill = sent.slice();
  [w, pill] = setup(true); LEVEL = 15; tap(pill, 10); await wait(20); out.pill_at_floor = sent.slice();
  [w, pill] = setup(true); tap(pill, 90); await wait(20); out.pill_right = sent.slice();
  // markLife: an offline card takes the focus of its control, the controls leave the tab order
  const card = mk('c_x', ['card']), dot = mk('', ['dotwrap']), inp = mk(); inp.tag = 'input';
  card.kids = [dot, inp]; dot.focus();
  markLife(card, {name: 'X'}, 'offline');
  out.life = [document.activeElement === card, dot.tabIndex, dot.attrs['aria-disabled'], inp.disabled, card.attrs['aria-label']];
  markLife(card, {name: 'X'}, 'live');
  out.live = [dot.tabIndex, dot.attrs['aria-disabled'], inp.disabled, card.attrs['aria-label']];
  const face = mk('c_f', ['card']), fdot = mk('', ['dotwrap']); face._face = {}; face.kids = [fdot];
  markLife(face, {name: 'F'}, 'offline'); out.face = [fdot.tabIndex, fdot.attrs['aria-disabled']];
  // render(): the focused element, drawn anew, gets the focus back; not while a window is open
  const old = mk('sw_a'); old.tabIndex = 0; old.focus(); byId['sw_a'] = old;
  renderPage = () => { const n = mk('sw_a'); n.tabIndex = 0; byId['sw_a'] = n; document.activeElement = document.body; };
  render(); out.render = document.activeElement === byId['sw_a'];
  // a card redrawn for an offline device is marked at once: the focus does not go back to a dot that is off
  const gone = mk('sw_b'); gone.tabIndex = 0; gone.focus(); byId['sw_b'] = gone; openModalId = null;
  renderPage = () => { const n = mk('sw_b'); n.tabIndex = 0; byId['sw_b'] = n; document.activeElement = document.body; };
  onCards = () => { byId['sw_b'].tabIndex = -1; };
  render(); out.render_offline = document.activeElement === byId['sw_b']; onCards = () => {};
  // the dot's drag or tap sends the waiting arrow step first; the touch is the last word
  [w] = setup(true); const dotA = mk(); attachDimmerDot(dotA, d, b, () => sent.push(['toggle']), null);
  w.fire('keydown', key('ArrowUp')); dotA.fire('pointerdown', py(100)); dotA.fire('pointermove', py(60)); dotA.fire('pointerup', py(60));
  await wait(300); out.dot_drag = sent.slice();
  [w] = setup(true); const dotB = mk(); attachDimmerDot(dotB, d, b, () => sent.push(['toggle']), null);
  w.fire('keydown', key('ArrowUp')); dotB.fire('pointerdown', py(100)); dotB.fire('pointerup', py(100));
  await wait(300); out.dot_tap = sent.slice();
  byId['sw_a'].focus(); openModalId = 'x'; render(); out.render_modal = document.activeElement === document.body;
  // the waiting steps are kept per device: flushing 'dim' leaves 'dim1' alone
  [w, pill] = setup(true); w.fire('keydown', key('ArrowUp')); flushKeySteps('dim'); out.prefix = sent.slice();
  await wait(300);
  console.log(JSON.stringify(out));
})();
"""


def _extract():
    parts = []
    for c in CONSTS:
        parts.append(re.search(r"^const %s\b.*$" % c, PAGE, re.M).group(0))
    for f in FUNCS:
        m = re.search(r"^(async )?function %s\(.*?^}" % f, PAGE, re.S | re.M)
        assert m, f
        parts.append(m.group(0))
    return "\n".join(parts)


def _node():
    node = shutil.which("node")
    if not node:
        print("SKIPPED: no node - the dashboard's key code was not run")
    return node


class DashboardKeys(unittest.TestCase):
    def test_the_keys_the_pill_and_the_life_marks_behave(self):
        node = _node()
        if not node:
            self.skipTest("no node")
        r = subprocess.run([node, "-e", _extract() + STUBS + SCENARIOS], capture_output=True, text=True, timeout=40)
        self.assertEqual(r.returncode, 0, r.stderr)
        o = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(o["up"], [["brightness", 56]])                      # a tenth of the 15-60 window, up
        self.assertEqual(o["down"], [["brightness", 47]])
        self.assertEqual(o["up_off"], [["brightness", 56], ["power", True]])  # a step switches an OFF channel on
        self.assertEqual(o["enter_held"], [["power", False]])                # a held key acts once
        self.assertEqual(o["offline"], [])
        self.assertEqual(o["gone_meanwhile"], [[], False])                   # offline before it went out: dropped
        self.assertEqual(o["up_then_enter"], [["brightness", 56], ["power", False]])   # the step first, then off
        self.assertEqual(o["enter_then_up"], [["power", False], ["brightness", 56], ["power", True]])
        self.assertEqual(o["held_up"], [["brightness", 60]])                 # five presses, one send, the window's top
        self.assertEqual(o["at_top"], [])                                    # at the window's top: nothing to send
        self.assertEqual(o["report_wins"], [["power", False]])               # the device still says on: off again
        self.assertEqual(o["in_flight"], [["power", True]])                  # still waiting: builds on what was asked
        self.assertEqual(o["failed"], [[["power", False], ["power", False]], True])   # a refused one is tried again
        self.assertEqual(o["fail_restores"], [["power", True]])               # the first (off) counts again
        self.assertTrue(o["late_keeps"])
        self.assertEqual(o["records"], [])
        self.assertEqual(o["enter_then_step"], [["power", False], ["brightness", 56], ["power", True]])
        self.assertEqual(o["pill_flush"], [["brightness", 56], ["power", False]])     # the arrow's level goes first
        self.assertEqual(o["pill_then_enter"], [["power", False], ["power", True]])   # Enter builds on the tap
        self.assertEqual(o["enter_then_pill"], [["power", False], ["power", True]])   # the tap builds on Enter
        self.assertEqual(o["pill_at_floor"], [])                             # the left zone at the floor: nothing
        self.assertEqual(o["pill_right"], [["brightness", 56]])
        self.assertEqual(o["life"], [True, -1, "true", True, "X, offline"])
        self.assertEqual(o["live"], [0, "false", False, "X"])
        self.assertEqual(o["face"], [-2, None])                               # a plug-in face is left alone
        self.assertTrue(o["render"])
        self.assertTrue(o["render_modal"])
        self.assertFalse(o["render_offline"])                                # marked offline before the focus returns
        self.assertEqual(o["dot_drag"], [["brightness", 56], ["brightness", 81]])   # the arrow's level, then the drag's
        self.assertEqual(o["dot_tap"], [["brightness", 56], ["toggle"]])      # the tap acts on what the arrow asked
        self.assertEqual(o["prefix"], [])                                    # another device's flush: not this one


class DashboardStep(unittest.TestCase):
    def test_a_step_moves_inside_the_window_and_only_into_it(self):
        node = _node()
        if not node:
            self.skipTest("no node")
        js = _extract() + r"""
const w = {floor: 15, ceil: 60}, full = {}, narrow = {floor: 0, ceil: 3}, five = {floor: 0, ceil: 5}, low = {floor: 0, ceil: 40};
const out = [];
let v = 50; for (let i = 0; i < 8; i++) { v = dimStep(w, v, 1); out.push(v); }
let u = 50; for (let i = 0; i < 8; i++) { u = dimStep(w, u, -1); out.push(u); }
out.push(dimStep(full, 50, 1), dimStep(full, 1, -1), dimStep(full, 100, 1), dimStep(full, 47, 1));
out.push(dimStep(narrow, 1, 1), dimStep(narrow, 2, 1), dimStep(narrow, 3, 1), dimStep(five, 5, -1), dimStep(five, 2, -1));
out.push(dimStep(w, 5, -1), dimStep(w, 5, 1), dimStep(w, 70, 1), dimStep(w, 70, -1), dimStep(w, 0, -1));
out.push(dimStep(full, 0, 1), dimStep(full, 0, -1), dimStep(full, 1, 1), dimStep(low, 0, 1), dimStep(low, 0, -1), dimStep(low, 1, -1));
console.log(JSON.stringify(out));"""
        r = subprocess.run([node, "-e", js], capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(got[:8], [56, 60, 60, 60, 60, 60, 60, 60])          # up to the ceiling, not past it
        self.assertEqual(got[8:16], [47, 42, 38, 33, 29, 24, 20, 15])        # down to the floor
        self.assertEqual(got[16:20], [60, 1, 100, 60])                        # a tenth of 0-100, never below 1
        self.assertEqual(got[20:25], [2, 3, 3, 4, 1])                         # a narrow window moves and ends at its edges
        self.assertEqual(got[25:30], [5, 15, 70, 60, 0])                      # outside: only into the window
        self.assertEqual(got[30:36], [10, 0, 10, 4, 0, 1])                    # from 0: a tenth up, nothing down


def _braced(marker, tail):
    """The page's source from marker (it ends with the opening brace) to its matching brace, plus tail."""
    i = PAGE.index(marker)
    depth, j = 0, i + len(marker) - 1
    while True:
        depth += {"{": 1, "}": -1}.get(PAGE[j], 0)
        if depth == 0:
            return PAGE[i:j + 1] + tail
        j += 1


MODAL_TOGGLE = r"""
const handlers = {};
document.addEventListener = (t, f) => { handlers[t] = f; };
function closeModal(){}
const off = new Set(['devoff']);
const tg = mk('sw_dim1_power', ['toggle']);
tg.dataset = {code: 'power'}; tg.closest = () => null; tg.click = () => tg.onclick();
tg.classList.toggle = function(c, f){ f === false ? this.s.delete(c) : this.s.add(c); };
const md = {classList: {contains: c => off.has(c)}, querySelectorAll: () => [tg]};
%s
%s
const out = {};
const press = async (k, repeat) => {
  sent.length = 0; ON = {'dim1/power': false};
  for (const o of [pending, _swIntent, STATE]) for (const x of Object.keys(o)) delete o[x];
  tg.focus(); handlers.keydown({key: k, repeat: !!repeat, target: tg, preventDefault(){}});
  await wait(20); return sent.slice();
};
(async () => {
  out.devoff_enter = await press('Enter'); out.devoff_space = await press(' ');
  off.delete('devoff'); out.live_enter = await press('Enter'); out.live_space = await press(' ');
  out.live_held = await press('Enter', true);
  tg.closest = () => ({}); out.offline_card = await press('Enter');
  console.log(JSON.stringify(out));
})();
"""


class ModalToggleKeys(unittest.TestCase):
    def test_a_focused_modal_toggle_acts_by_key_only_while_the_device_is_live(self):
        node = _node()
        if not node:
            self.skipTest("no node")
        wiring = _braced("""md.querySelectorAll('[data-act="toggle"],[data-act="set_bool"]').forEach(elx=>{""", ");")
        keys = _braced("document.addEventListener('keydown', e => {", ");")
        r = subprocess.run([node, "-e", _extract() + STUBS + MODAL_TOGGLE % (wiring, keys)],
                           capture_output=True, text=True, timeout=40)
        self.assertEqual(r.returncode, 0, r.stderr)
        o = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(o["devoff_enter"], [])                              # offline window: Enter sends nothing
        self.assertEqual(o["devoff_space"], [])
        self.assertEqual(o["live_enter"], [["power", True]])                 # the same key on a live one switches on
        self.assertEqual(o["live_space"], [["power", True]])
        self.assertEqual(o["live_held"], [])                                 # a held key acts once
        self.assertEqual(o["offline_card"], [])                              # a toggle inside an offline card: nothing
