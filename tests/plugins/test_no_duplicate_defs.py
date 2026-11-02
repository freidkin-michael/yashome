"""No module of the core, a plug-in or tools/ defines the same top-level function or class twice: the second one silently
replaces the first for every caller (a second _gateway() in panels once shadowed the camera check's)."""
import ast
import collections
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_no_top_level_name_is_defined_twice():
    found = {}
    import app                         # the core is one namespace over app.py and core/*.py: checked as one text
    sources = [("app.py + core/", app.core_source())] + [(str(f.relative_to(ROOT)), f.read_text(encoding="utf-8"))
                                                         for f in [*ROOT.glob("plugins/*/*/*.py"), *ROOT.glob("tools/**/*.py")]]
    for name, text in sources:
        tree = ast.parse(text)
        n = collections.Counter(d.name for d in tree.body if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))
        if dup := sorted(k for k, v in n.items() if v > 1):
            found[name] = dup
    assert found == {}
