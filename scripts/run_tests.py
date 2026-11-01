"""Run all server and calendar checks using isolated data."""
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-v'], cwd=root)
if result.returncode:
    raise SystemExit(result.returncode)
node = shutil.which('node')
if node:
    raise SystemExit(subprocess.run([node, '--test', 'tests/dates.test.cjs'], cwd=root).returncode)
print('Node.js unavailable; server tests passed, browser calendar tests were skipped.')
