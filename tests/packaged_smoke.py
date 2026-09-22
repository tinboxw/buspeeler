"""CI check of the archive and frozen HTTP release gates; synthetic inputs only."""
import argparse
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    windows = os.name == "nt"
    archives = list(args.dist.glob("*.zip" if windows else "*.tar.gz"))
    assert len(archives) == 1, "Expected exactly one platform archive"
    archive = archives[0]
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    checksum = archive.with_name(archive.name + ".sha256").read_text().split()
    assert checksum == [digest, archive.name], "Archive checksum mismatch"
    entry = "Buspeeler/Buspeeler" + (".exe" if windows else "")
    executable = args.dist.resolve() / entry
    with executable.open("rb") as stream:
        executable_hash = hashlib.file_digest(stream, "sha256").hexdigest()
    if windows:
        with zipfile.ZipFile(archive) as package:
            assert package.testzip() is None, "ZIP integrity failed"
            assert hashlib.sha256(package.read(entry)).hexdigest() == executable_hash
    else:
        with tarfile.open(archive) as package:
            assert package.getmember(entry).mode & 0o111, "Executable permission lost"
            assert hashlib.file_digest(package.extractfile(entry), "sha256").hexdigest() == executable_hash
            for member in package.getmembers():
                if member.isfile():
                    with package.extractfile(member) as stream:
                        while stream.read(1024 * 1024):
                            pass
    args.work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="packaged-", dir=args.work.resolve()) as temporary:
        work = Path(temporary)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = {key: value for key, value in os.environ.items() if key not in ("PYTHONHOME", "PYTHONPATH")}
        env["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32") if windows else os.defpath
        with (work / "application.log").open("w+", encoding="utf-8") as log:
            process = subprocess.Popen([str(executable), "--no-browser", "--port", str(port),
                                        "--data-dir", str(work / "data")], cwd=work, env=env,
                                       stdout=log, stderr=subprocess.STDOUT)
            try:
                deadline = time.monotonic() + 90
                local_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                last_error = None
                while time.monotonic() < deadline:
                    assert process.poll() is None, "Packaged application exited during startup"
                    try:
                        with local_http.open(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except (OSError, urllib.error.URLError) as error:
                        last_error = error
                        time.sleep(.2)
                else:
                    raise TimeoutError(f"Packaged application did not become ready: {last_error}")
                with local_http.open(f"http://127.0.0.1:{port}/", timeout=5) as response:
                    assert b'id="app"' in response.read(), "Bundled page missing"
                subprocess.run([sys.executable, str(Path(__file__).with_name("frozen_gate_smoke.py")),
                                str(work / "data/instance.json")], check=True, timeout=120)
                assert process.poll() is None, "Packaged application exited during validation"
            except Exception:
                log.seek(0)
                print(log.read(), file=sys.stderr)
                raise
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=15)
    print("Archive integrity, bundled page, frozen startup and DBC release gates passed.")


if __name__ == "__main__":
    main()
