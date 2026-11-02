#!/usr/bin/env python3
"""Plug-in discovery and `requires` resolution: one implementation for the backend (app.py), the broker
accounts (mkpasswd.py), the lint (check_plugins.py) and install/update (tools/envlib.sh runs
`python3 tools/pluginlib.py dirs|all ...`). Standard library only."""
import re
import sys
from pathlib import Path

NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
REQUIRED = ("map",)          # always on, listed in PLUGINS or not: the rooms of the house are the map's


def roots(d: Path) -> list:
    """d and each group folder in it (plugins/transports/ ...: a directory without __init__.py)."""
    d = Path(d)
    return [d] + [g for g in sorted(d.iterdir()) if g.is_dir() and not g.name.startswith((".", "_"))
                  and not (g / "__init__.py").exists()]


def discover(bases) -> tuple:
    """Plug-in packages under the bases in order, a name taken once: (found, [(skipped, first)])."""
    found, by_name, dup = [], {}, []
    for base in bases:
        if not Path(base).is_dir():
            continue
        for r in roots(base):
            for q in sorted(r.iterdir()):
                if q.name.startswith("_") or not (q / "__init__.py").is_file():
                    continue
                if q.name in by_name:
                    dup.append((q, by_name[q.name]))
                    continue
                by_name[q.name] = q
                found.append(q)
    return found, dup


def read_requires(q: Path) -> list:
    """The file `requires`: one plug-in name per line, # comments; ValueError when unusable."""
    f = Path(q) / "requires"
    if not f.is_file():
        return []
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as e:
        raise ValueError(f"requires: {type(e).__name__}") from None
    names = [ln.split("#", 1)[0].strip() for ln in lines]
    wrong = [n for n in names if n and not NAME.match(n)]
    if wrong:
        raise ValueError(f"requires: {wrong[0]!r} is not one plug-in name per line")
    return [n for n in dict.fromkeys(names) if n and n != Path(q).name]


def requirements(found: list) -> tuple:
    """({name: requires}, {name: why its requires file is unusable})."""
    reqs, bad = {}, {}
    for q in found:
        try:
            reqs[q.name] = read_requires(q)
        except ValueError as e:
            reqs[q.name], bad[q.name] = [], str(e)
    return reqs, bad


def closure(want: set, reqs: dict) -> dict:
    """want and everything it requires, transitively: {name: the plug-in that brought it in, None = listed}."""
    why = {n: None for n in want}
    todo = list(want)
    while todo:
        n = todo.pop()
        for dep in reqs.get(n, ()):
            if dep not in why:
                why[dep] = n
                todo.append(dep)
    return why


def circles(names, reqs: dict) -> set:
    """Every name that sits in a circle of requirements (a strongly connected group of two or more)."""
    index, low, stack, on, out, n = {}, {}, [], set(), set(), [0]

    def visit(v):
        index[v] = low[v] = n[0]
        n[0] += 1
        stack.append(v)
        on.add(v)
        for w in (d for d in reqs.get(v, ()) if d in names):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            group = []
            while True:
                w = stack.pop()
                on.discard(w)
                group.append(w)
                if w == v:
                    break
            if len(group) > 1:
                out.update(group)
    for v in sorted(names):
        if v not in index:
            visit(v)
    return out


def order(found: list, reqs: dict) -> tuple:
    """Alphabetical, a requirement before what requires it; and every name that sits in a circle."""
    by_name = {q.name: q for q in found}
    out, done, busy = [], set(), set()

    def visit(q):
        if q.name in done or q.name in busy:
            return
        busy.add(q.name)
        for dep in reqs.get(q.name, ()):
            if dep in by_name:
                visit(by_name[dep])
        busy.discard(q.name)
        done.add(q.name)
        out.append(q)
    for q in sorted(found, key=lambda x: x.name):
        visit(q)
    return out, circles(set(by_name), reqs)


def plan(found: list, want) -> dict:
    """What loads, in order, and why the rest of the wanted ones do not (static view: before imports).
    want None = every plug-in found. A plug-in whose requirement is missing, unusable, in a circle or
    itself off is off."""
    reqs, bad = requirements(found)
    names = {q.name for q in found}
    why = closure(set(names) if want is None else set(want) | set(REQUIRED), reqs)
    for n in REQUIRED:
        if want is not None and n not in want:
            why[n] = "core"
    seq, circle = order(found, reqs)
    errors, on = {}, []
    for n in sorted(set(why) - names):
        errors[n] = (f"required by {why[n]} but not found" if why[n] else "listed in PLUGINS but not found") \
            + " in plugins/ or PLUGINS_EXTRA_DIR"
    for q in seq:
        n = q.name
        if n not in why:
            continue
        if not NAME.match(n):
            errors[n] = "directory name must be lowercase letters, digits and _ (starting with a letter)"
            continue
        if n in bad:
            errors[n] = bad[n]
            continue
        missing = [d for d in reqs[n] if d not in on]
        if missing:
            errors[n] = "requires " + ", ".join(
                f"{d} ({'not found' if d not in names else 'circular' if d in circle and n in circle else 'off'})"
                for d in missing)
            continue
        on.append(n)
    return {"on": on, "errors": errors, "why": why, "reqs": reqs, "bad": bad, "circle": circle}


def _main(argv):
    if argv and argv[0] == "all":
        for q in discover([Path(b) for b in argv[1:]])[0]:
            if NAME.match(q.name):
                print(q)
        return 0
    if len(argv) >= 2 and argv[0] == "dirs":
        raw = re.sub(r"\s#.*$", "", argv[1])
        listed = {x.strip().strip("\"'") for x in raw.split(",")} - {""}
        found, _ = discover([Path(b) for b in argv[2:]])
        p = plan(found, None if "*" in listed else listed)
        by_name = {q.name: q for q in found}
        for n in p["on"]:
            print(by_name[n])
        for n, e in sorted(p["errors"].items()):
            print(f"plug-in {n}: {e}", file=sys.stderr)
        return 0
    if argv == ["required"]:
        print("\n".join(REQUIRED))
        return 0
    print("usage: pluginlib.py dirs <PLUGINS> <plug-in dir>... | all <plug-in dir>... | required", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
