"""Build and smoke-test the distributable Course Harness wheel."""

from __future__ import annotations

import os
import socket
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for(url: str, *, timeout: float = 30) -> bytes:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:  # noqa: S310
                if response.status == 200:
                    return response.read()
        except OSError:
            time.sleep(0.1)
    raise TimeoutError(f"Timed out waiting for {url}")


def main() -> None:
    repository = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix="course-harness-distribution-") as directory:
        temporary = Path(directory)
        artifacts = temporary / "dist"
        build_environment = os.environ.copy()
        build_environment["UV_CACHE_DIR"] = str(temporary / "uv-build-cache")
        subprocess.run(
            ["uv", "build", "--out-dir", str(artifacts)],
            cwd=repository,
            env=build_environment,
            check=True,
        )
        wheels = list(artifacts.glob("course_harness-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"Expected one wheel, found {len(wheels)}")
        wheel = wheels[0]

        with zipfile.ZipFile(wheel) as archive:
            names = set(archive.namelist())
            if "course_harness/static/index.html" not in names:
                raise RuntimeError("Wheel does not contain the production frontend entry point")
            if not any(name.startswith("course_harness/static/assets/") for name in names):
                raise RuntimeError("Wheel does not contain production frontend assets")
            metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
            metadata = archive.read(metadata_name).decode("utf-8")
            if "License-Expression: MIT" not in metadata:
                raise RuntimeError("Wheel metadata does not declare the MIT license")

        workspace = temporary / "course"
        workspace.mkdir()
        port = _available_port()
        environment = os.environ.copy()
        environment.update(
            {
                "XDG_CACHE_HOME": str(temporary / "cache"),
                "XDG_CONFIG_HOME": str(temporary / "config"),
                "XDG_DATA_HOME": str(temporary / "data"),
                "XDG_STATE_HOME": str(temporary / "state"),
                "UV_CACHE_DIR": str(temporary / "uvx-cache"),
            }
        )
        command = [
            "uvx",
            "--python",
            "3.14",
            "--from",
            str(wheel),
            "course-harness",
            str(workspace),
            "--no-browser",
            "--port",
            str(port),
        ]
        process = subprocess.Popen(
            command,
            cwd=temporary,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        terminated_by_verifier = False
        try:
            health = _wait_for(f"http://127.0.0.1:{port}/api/health")
            if health != b'{"status":"ok"}':
                raise RuntimeError(f"Unexpected health response: {health!r}")
            index = _wait_for(f"http://127.0.0.1:{port}/")
            if b"Course Harness" not in index:
                raise RuntimeError("Installed wheel did not serve the production frontend")
        finally:
            if process.poll() is None:
                terminated_by_verifier = True
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if not terminated_by_verifier and process.returncode != 0:
            output = process.stdout.read() if process.stdout is not None else ""
            raise RuntimeError(f"Installed Course Harness exited unexpectedly:\n{output}")

        print(f"Verified wheel: {wheel.name}")


if __name__ == "__main__":
    main()
