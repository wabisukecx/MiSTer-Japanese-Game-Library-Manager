"""Run offline tests and record their unmodified output as UTF-8."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
env = dict(os.environ, PYTHONPATH=str(root / 'src'), PYTHONIOENCODING='utf-8')
result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                        cwd=root, env=env, capture_output=True, encoding='utf-8')
report = 'Python: ' + sys.version + '\n' + result.stdout + result.stderr
output = root / 'work' / 'verification.txt'
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(report, encoding='utf-8')
print(report, end='')
raise SystemExit(result.returncode)
