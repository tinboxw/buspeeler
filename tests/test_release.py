import hashlib
import subprocess

import pytest

from scripts.release import publish, validate_tag, verified_assets


def test_release_tag_requires_exact_stable_project_version():
    validate_tag("v0.1.0", "0.1.0")
    for tag in ("0.1.0", "v0.2.0", "v00.1.0", "v0.1.0-rc.1", "v0.1.0/other"):
        with pytest.raises(ValueError):
            validate_tag(tag, "0.1.0")


def test_release_requires_both_platforms_and_correct_checksums(tmp_path):
    for name in ("Buspeeler-0.1.0-Windows-x64.zip", "Buspeeler-0.1.0-Linux-x64.tar.gz"):
        content = name.encode()
        (tmp_path / name).write_bytes(content)
        (tmp_path / (name + ".sha256")).write_text(hashlib.sha256(content).hexdigest() + "  " + name)
    assert len(verified_assets(tmp_path, "0.1.0")) == 4
    package = tmp_path / "Buspeeler-0.1.0-Windows-x64.zip"
    package.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Checksum"):
        verified_assets(tmp_path, "0.1.0")
    package.unlink()
    with pytest.raises(ValueError, match="exactly"):
        verified_assets(tmp_path, "0.1.0")


def test_release_does_not_publish_after_failed_upload_or_overwrite(monkeypatch):
    monkeypatch.setenv("GH_REPO", "example/buspeeler")
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: "same-commit\n")
    calls = []

    def failed_create(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(subprocess, "run", failed_create)
    with pytest.raises(subprocess.CalledProcessError):
        publish("v0.1.0", ["package.zip"])
    assert len(calls) == 1 and calls[0][1:3] == ["release", "create"]
    assert "--draft" in calls[0] and "--verify-tag" in calls[0]

    calls.clear()
    monkeypatch.setattr(subprocess, "run", lambda command, **kwargs: calls.append(command))
    publish("v0.1.0", ["package.zip"])
    assert calls[1] == ["gh", "release", "edit", "v0.1.0", "--draft=false"]


def test_release_refuses_moved_tag_before_any_write(monkeypatch):
    monkeypatch.setenv("GH_REPO", "example/buspeeler")
    results = iter(["remote-commit", "build-commit"])
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: next(results))
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("Unexpected release write"))
    with pytest.raises(ValueError, match="Remote tag"):
        publish("v0.1.0", [])
