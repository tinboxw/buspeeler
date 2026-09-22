import os
from pathlib import Path
import subprocess
import sys


def test_launcher_reconfigures_redirected_cp1252_streams():
    code = r'''
import sys
assert sys.stdout.encoding.lower() == "cp1252"
assert sys.stderr.encoding.lower() == "cp1252"
from buspeeler.launcher import main
sys.argv = ["Buspeeler", "--help"]
try:
    main()
except SystemExit as error:
    assert error.code == 0
print("\u9519\u8bef\u65e5\u5fd7", file=sys.stderr)
'''
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONIOENCODING": "cp1252:strict", "PYTHONUTF8": "0"},
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    assert "Buspeeler 本机工作台" in result.stdout.decode("utf-8")
    assert "错误日志" in result.stderr.decode("utf-8")
