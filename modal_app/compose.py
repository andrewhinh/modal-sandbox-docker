from __future__ import annotations

import shutil
import tarfile
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import modal

from modal_app.sandbox import WORKDIR, run


IGNORED_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
}


def _ignore(_directory: str, names: list[str]) -> set[str]:
    ignored = set()
    for name in names:
        if name in IGNORED_NAMES or name.endswith(".egg-info"):
            ignored.add(name)
    return ignored


@contextmanager
def project_archive(local_path: Path) -> Iterator[Path]:
    local_path = local_path.resolve()
    with tempfile.TemporaryDirectory() as tmpdir:
        staged = Path(tmpdir) / "workspace"
        archive = Path(tmpdir) / "workspace.tar.gz"
        shutil.copytree(local_path, staged, ignore=_ignore)
        with tarfile.open(archive, "w:gz") as tar:
            for item in staged.rglob("*"):
                tar.add(item, arcname=item.relative_to(staged))
        yield archive


def copy_project_archive(sb: modal.Sandbox, archive: Path) -> None:
    sb.filesystem.make_directory(WORKDIR)
    sb.filesystem.copy_from_local(archive, "/tmp/workspace.tar.gz")
    run(
        sb,
        "tar",
        "-xzf",
        "/tmp/workspace.tar.gz",
        "-C",
        WORKDIR,
        label="extract project",
    )


def copy_project(sb: modal.Sandbox, local_path: Path) -> None:
    with project_archive(local_path) as archive:
        copy_project_archive(sb, archive)


def write_env(sb: modal.Sandbox, preview_id: str, branch: str) -> None:
    sb.filesystem.write_text(
        f"PREVIEW_ID={preview_id}\nBRANCH={branch}\n",
        f"{WORKDIR}/.env",
    )


def compose_pull(sb: modal.Sandbox) -> None:
    run(
        sb,
        "bash",
        "-lc",
        "docker compose pull || true",
        label="compose pull",
        workdir=WORKDIR,
        echo=True,
        check=False,
    )


def compose_up(sb: modal.Sandbox) -> None:
    run(
        sb,
        "docker",
        "compose",
        "up",
        "--build",
        "-d",
        label="compose up",
        workdir=WORKDIR,
        echo=True,
        timeout=20 * 60,
    )


def compose_down(sb: modal.Sandbox) -> None:
    run(
        sb,
        "docker",
        "compose",
        "down",
        "-v",
        label="compose down",
        workdir=WORKDIR,
        echo=True,
        check=False,
    )


def compose_ps(sb: modal.Sandbox) -> str:
    return run(
        sb,
        "docker",
        "compose",
        "ps",
        label="compose ps",
        workdir=WORKDIR,
    ).stdout


def compose_logs(sb: modal.Sandbox, tail: int = 120) -> str:
    return run(
        sb,
        "docker",
        "compose",
        "logs",
        "--tail",
        str(tail),
        label="compose logs",
        workdir=WORKDIR,
        check=False,
    ).stdout


def wait_for_health(sb: modal.Sandbox, url: str = "http://127.0.0.1:8000/health", timeout_seconds: int = 120) -> str:
    deadline = time.monotonic() + timeout_seconds
    last_output = ""
    while time.monotonic() < deadline:
        result = run(
            sb,
            "curl",
            "-fsS",
            url,
            label="health",
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        last_output = result.stderr or result.stdout
        time.sleep(2)
    raise TimeoutError(f"Timed out waiting for health check. Last output:\n{last_output}")
