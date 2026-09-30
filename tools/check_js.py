"""node --check every inline <script> of the given HTML files, plus plain JS files.
The dashboard has no build step, so this is the only syntax gate it gets."""
import pathlib
import re
import subprocess
import sys
import tempfile

rc = 0
for arg in sys.argv[1:]:
    p = pathlib.Path(arg)
    if p.suffix == '.html':
        blocks = re.findall(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', p.read_text(), re.S)
        js = '\n;\n'.join(blocks)
    else:
        js = p.read_text()
    with tempfile.NamedTemporaryFile('w', suffix=p.suffix if p.suffix == '.mjs' else '.js', delete=False) as t:
        t.write(js)
    r = subprocess.run(['node', '--check', t.name], capture_output=True, text=True)
    print(f'{p}: {"ok" if r.returncode == 0 else "FAIL " + r.stderr.strip()[:300]}')
    rc |= r.returncode
sys.exit(rc)
