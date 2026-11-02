"""Print app.py and core/*.py as one text, in the order app.py runs them (app.CORE_PARTS): the core is one
namespace, so `make lint` checks this text (undefined names, a name defined twice) the way it checked one file."""
import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
app = (ROOT / "app.py").read_text(encoding="utf-8")
parts = next(ast.literal_eval(n.value) for n in ast.parse(app).body
             if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "CORE_PARTS")
sys.stdout.write(app + "".join("\n\n" + (ROOT / "core" / f"{p}.py").read_text(encoding="utf-8") for p in parts))
