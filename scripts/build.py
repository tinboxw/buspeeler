"""One-command native build; installs dependencies only into a reusable venv."""
import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tomllib
import venv

ROOT = Path(__file__).resolve().parents[1]


def run(*args, **kwargs):
    subprocess.run(list(map(str, args)), cwd=ROOT, check=True, **kwargs)


def main():
    windows = sys.platform == "win32"
    parser = argparse.ArgumentParser(description="Install locked dependencies, test, build and archive Buspeeler.")
    default_cache = Path("D:/dev-cache/buspeeler/shared") if windows else (
        Path("/mnt/d/dev-cache/buspeeler/shared") if Path("/mnt/d/dev-cache").is_dir()
        else Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "buspeeler"
    )
    parser.add_argument("--cache", type=Path, default=default_cache)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist" / ("windows" if windows else "linux"))
    parser.add_argument("--check", action="store_true", help="Check build tools without installing or building")
    args = parser.parse_args()
    args.dist = args.dist.resolve()
    if sys.version_info < (3, 12):
        parser.error("Python 3.12 or newer is required")
    if sys.platform not in ("win32", "linux") or platform.machine().lower() not in ("amd64", "x86_64"):
        parser.error("Only native Windows/Linux x64 builds are supported")
    required = ["node", "npm.cmd" if windows else "npm", "cmake"]
    missing = [name for name in required if not shutil.which(name)]
    if not (shutil.which("g++") or shutil.which("clang++") or (windows and shutil.which("cl"))):
        missing.append("C++17 compiler (g++, clang++, or cl in a developer terminal)")
    if missing:
        parser.error("Missing build tools: " + ", ".join(missing))
    node_version = subprocess.check_output(["node", "--version"], text=True).strip()
    if int(node_version.lstrip("v").split(".")[0]) < 18:
        parser.error("Node.js 18 or newer is required; release baseline is Node.js 22")
    print(f"Build tools ready: Python {platform.python_version()}, Node {node_version}", flush=True)
    if args.check:
        return
    cache = args.cache.resolve()
    environment = cache / ("python312" if windows else "venv-linux")
    python = environment / ("Scripts/python.exe" if windows else "bin/python")
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(environment)
    temporary = cache / ("tmp-windows" if windows else "tmp-linux")
    temporary.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PIP_CACHE_DIR=str(cache / "pip"),
               TMP=str(temporary), TEMP=str(temporary), TMPDIR=str(temporary))
    run(python, "-m", "pip", "install", "-r", ROOT / "requirements.lock", env=env)
    run(python, "-m", "pip", "check", env=env)
    # WSL mounted caches do not reliably support pytest's unlinked fd capture files.
    run(python, "-m", "pytest", "-q", "--capture=sys", env=env)
    run(python, ROOT / "scripts/build_release.py", "--cache",
        cache / ("release-windows" if windows else "release-linux"), "--dist", args.dist, env=env)
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    filename = f"Buspeeler-{version}-{'Windows' if windows else 'Linux'}-x64.{'zip' if windows else 'tar.gz'}"
    archive = args.dist.resolve() / filename
    run(python, ROOT / "scripts/package_release.py", "--package", args.dist.resolve() / "Buspeeler",
        "--output", archive, env=env)
    print(f"Build completed: {archive}\nSHA-256: {archive}.sha256", flush=True)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from error
