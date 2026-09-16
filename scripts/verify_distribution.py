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


def _run_server(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    label: str,
) -> None:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    terminated_by_verifier = False
    try:
        port = int(command[command.index("--port") + 1])
        health = _wait_for(f"http://127.0.0.1:{port}/api/health")
        if health != b'{"status":"ok"}':
            raise RuntimeError(f"Unexpected health response from {label}: {health!r}")
        index = _wait_for(f"http://127.0.0.1:{port}/")
        if b"Course Harness" not in index:
            raise RuntimeError(f"{label} did not serve the production frontend")
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
        raise RuntimeError(f"{label} exited unexpectedly:\n{output}")


def _install_tool(
    package: str,
    *,
    environment: dict[str, str],
    temporary: Path,
    label: str,
) -> Path:
    subprocess.run(
        [
            "uv",
            "tool",
            "install",
            "--python",
            "3.14",
            "--no-cache",
            "--force",
            package,
        ],
        cwd=temporary,
        env=environment,
        check=True,
    )
    bin_directory = Path(
        subprocess.check_output(
            ["uv", "tool", "dir", "--bin"], cwd=temporary, env=environment, text=True
        ).strip()
    )
    executable_name = "course-harness.exe" if os.name == "nt" else "course-harness"
    executable = bin_directory / executable_name
    if not executable.is_file():
        raise RuntimeError(f"{label} did not install {executable}")
    return executable


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
        environment = os.environ.copy()
        environment.update(
            {
                "HOME": str(temporary / "home"),
                "XDG_CACHE_HOME": str(temporary / "cache"),
                "XDG_CONFIG_HOME": str(temporary / "config"),
                "XDG_DATA_HOME": str(temporary / "data"),
                "XDG_STATE_HOME": str(temporary / "state"),
                "UV_CACHE_DIR": str(temporary / "uvx-cache"),
            }
        )
        wheel_port = _available_port()
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
            str(wheel_port),
        ]
        _run_server(command, cwd=temporary, environment=environment, label="Installed wheel")

        tool_home = temporary / "tool-home"
        tool_environment = environment | {
            "HOME": str(tool_home),
            "XDG_CACHE_HOME": str(temporary / "tool-cache"),
            "XDG_CONFIG_HOME": str(temporary / "tool-config"),
            "XDG_DATA_HOME": str(temporary / "tool-data"),
            "UV_CACHE_DIR": str(temporary / "uv-tool-cache"),
        }
        installed_executable = _install_tool(
            str(wheel),
            environment=tool_environment,
            temporary=temporary,
            label="uv tool wheel installation",
        )
        tool_workspace = temporary / "tool-course"
        tool_workspace.mkdir()
        tool_port = _available_port()
        _run_server(
            [
                str(installed_executable),
                str(tool_workspace),
                "--no-browser",
                "--port",
                str(tool_port),
            ],
            cwd=temporary,
            environment=tool_environment,
            label="uv tool-installed wheel",
        )

        git_workspace = temporary / "git-course"
        git_workspace.mkdir()
        git_port = _available_port()
        _run_server(
            [
                "uvx",
                "--python",
                "3.14",
                "--no-cache",
                "--from",
                f"git+{repository.as_uri()}",
                "course-harness",
                str(git_workspace),
                "--no-browser",
                "--port",
                str(git_port),
            ],
            cwd=temporary,
            environment=environment,
            label="direct Git revision",
        )

        print(
            f"Verified uvx wheel, uv tool wheel, and direct local Git installations: {wheel.name}"
        )


if __name__ == "__main__":
    main()
