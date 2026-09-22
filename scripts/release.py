"""Validate stable version tags and publish the two verified native packages."""
import argparse
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def validate_tag(tag, version):
    if not re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", tag):
        raise ValueError("Release tags must use vX.Y.Z (stable versions only)")
    if tag != "v" + version:
        raise ValueError(f"Tag {tag} does not match project version {version}")


def verified_assets(directory, version):
    packages = [f"Buspeeler-{version}-Windows-x64.zip", f"Buspeeler-{version}-Linux-x64.tar.gz"]
    names = [name + suffix for name in packages for suffix in ("", ".sha256")]
    if {path.name for path in directory.iterdir()} != set(names):
        raise ValueError("Expected exactly the Windows/Linux packages and their two SHA-256 files")
    for name in packages:
        with (directory / name).open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if (directory / (name + ".sha256")).read_text(encoding="utf-8").split() != [digest, name]:
            raise ValueError(f"Checksum mismatch: {name}")
    return [directory / name for name in names]


def publish(tag, assets):
    # Refuse moved tags and existing releases; never overwrite published assets.
    remote = subprocess.check_output(
        ["gh", "api", f"repos/{os.environ['GH_REPO']}/commits/{tag}", "--jq", ".sha"], text=True).strip()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if remote != head:
        raise ValueError("Remote tag no longer points to the commit used for this build")
    subprocess.run(["gh", "release", "create", tag, *map(str, assets), "--verify-tag", "--draft",
                    "--title", f"Buspeeler {tag}", "--generate-notes"], check=True)
    subprocess.run(["gh", "release", "edit", tag, "--draft=false"], check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    validate_tag(args.tag, version)
    if args.publish and args.assets is None:
        parser.error("--publish requires --assets")
    assets = verified_assets(args.assets.resolve(), version) if args.assets else []
    if args.publish:
        publish(args.tag, assets)
    print(f"{'Published' if args.publish else 'Validated'} {args.tag}")


if __name__ == "__main__":
    main()
