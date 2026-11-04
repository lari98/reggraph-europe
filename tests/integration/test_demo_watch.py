"""``reggraph demo``: stable key, self-restart on a code change, tokens that survive a restart."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess  # nosec B404
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from reggraph import demo
from reggraph.cli import main


def test_demo_key_is_created_once_and_kept_private(tmp_path: Path) -> None:
    first = demo.demo_key(tmp_path)
    assert len(first) >= 32 and demo.demo_key(tmp_path) == first  # same key on the next start
    key_file = tmp_path / ".reggraph" / "demo.key"
    if os.name == "posix":
        assert key_file.stat().st_mode & 0o077 == 0  # owner only


def test_demo_key_falls_back_to_a_random_key_when_the_disk_is_read_only(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    a, b = demo.demo_key(blocker), demo.demo_key(blocker)  # home is a file: cannot create a folder
    assert len(a) >= 32 and a != b


def test_a_short_or_damaged_key_file_is_replaced(tmp_path: Path) -> None:
    folder = tmp_path / ".reggraph"
    folder.mkdir()
    (folder / "demo.key").write_bytes(b"short")
    assert len(demo.demo_key(tmp_path)) >= 32


def test_snapshot_sees_code_changes_but_not_page_files(tmp_path: Path, monkeypatch: Any) -> None:
    (tmp_path / "a.py").write_text("x = 1")
    (tmp_path / "page.js").write_text("1")
    monkeypatch.setattr(demo, "_PACKAGE_DIR", tmp_path)
    before = demo._snapshot()
    assert {Path(p).name for p in before} == {"a.py"}  # page files never force a restart
    os.utime(tmp_path / "a.py", ns=(1, 1))
    assert demo._snapshot() != before


def test_watched_demo_refuses_a_public_address(capsys: Any) -> None:
    assert main(["demo", "--host", "0.0.0.0"]) == 2  # noqa: S104
    assert "loopback" in capsys.readouterr().err


def test_no_watch_flag_selects_the_plain_runner(monkeypatch: Any) -> None:
    calls: list[str] = []
    monkeypatch.setattr(demo, "run_demo", lambda *a: calls.append("plain") or 0)
    monkeypatch.setattr(demo, "run_demo_watched", lambda *a: calls.append("watched") or 0)
    assert main(["demo", "--no-watch"]) == 0 and main(["demo"]) == 0
    assert calls == ["plain", "watched"]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _get(url: str, token: str | None = None) -> Any:
    req = urllib.request.Request(url)  # noqa: S310 - loopback test server
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=5) as res:  # noqa: S310  # nosec B310
        return json.loads(res.read())


def _wait_up(port: int, tries: int = 150) -> None:
    for _ in range(tries):
        try:
            _get(f"http://127.0.0.1:{port}/dashboard/version")
            return
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.2)
    raise AssertionError("demo server did not start")


def test_demo_restarts_itself_and_the_old_token_still_works(tmp_path: Path) -> None:
    port = _free_port()
    env = {**os.environ, "HOME": str(tmp_path), "USERPROFILE": str(tmp_path)}
    out = tmp_path / "out.txt"
    watched = Path(demo.__file__).with_name("i18n") / "catalogs" / "mt.json"
    original = watched.stat().st_mtime_ns
    with out.open("w") as log:
        proc = subprocess.Popen(  # noqa: S603  # nosec B603
            [sys.executable, "-m", "reggraph.cli", "demo", "--port", str(port)],
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        try:
            _wait_up(port)
            token = re.search(r"#token=(\S+)", out.read_text()).group(1)  # type: ignore[union-attr]
            base = f"http://127.0.0.1:{port}"
            assert _get(base + "/v1/dashboard", token)["view"]
            os.utime(watched, None)  # a "code change": the file is touched, nothing else
            for _ in range(100):
                if "restarting" in out.read_text():
                    break
                time.sleep(0.2)
            else:
                pytest.fail("the demo did not notice the change")
            _wait_up(port)
            assert _get(base + "/v1/dashboard", token)["view"]  # same key: still signed in
        finally:
            os.utime(watched, ns=(original, original))
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:  # pragma: no cover
                proc.kill()
