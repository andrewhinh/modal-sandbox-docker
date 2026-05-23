from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import mean

import modal

from modal_app.compose import (
    compose_down,
    compose_logs,
    compose_ps,
    compose_pull,
    compose_up,
    copy_project,
    copy_project_archive,
    project_archive,
    wait_for_health,
    write_env,
)
from modal_app.metadata import (
    PreviewMetadata,
    delete_preview,
    delete_pr_preview,
    get_preview,
    get_pr_preview,
    list_previews,
    list_pr_previews,
    now_seconds,
    preview_id as make_preview_id,
    save_preview,
    save_pr_preview,
)
from modal_app.sandbox import APP_NAME, create_sandbox, wait_for_docker
from modal_app.tunnels import get_tunnel_urls


TIMING_ORDER = [
    ("sandbox_create_seconds", "Sandbox create/start"),
    ("docker_ready_seconds", "Docker daemon ready"),
    ("file_sync_seconds", "File sync/write"),
    ("compose_pull_seconds", "Compose pull"),
    ("compose_up_seconds", "Compose build/up"),
    ("health_ready_seconds", "Health ready"),
    ("tunnel_ready_seconds", "Tunnel ready"),
    ("total_seconds", "Total"),
]


@dataclass(frozen=True)
class PreviewHandle:
    metadata: PreviewMetadata
    sandbox: modal.Sandbox


def print_timings(timings: dict[str, object]) -> None:
    print("Startup timings:")
    for key, label in TIMING_ORDER:
        value = timings.get(key)
        if isinstance(value, int | float):
            print(f"  {label:<22} {value:>7.2f}s")


def validate_project_path(path: str) -> Path:
    project_path = Path(path).resolve()
    if not project_path.exists():
        raise FileNotFoundError(project_path)
    if not (project_path / "compose.yaml").exists() and not (project_path / "docker-compose.yml").exists():
        raise FileNotFoundError(f"No compose.yaml or docker-compose.yml found in {project_path}")
    return project_path


def print_preview_table() -> None:
    previews = list_previews()
    if not previews:
        print("No previews found.")
        return

    print("PREVIEW ID  STATUS    BRANCH        AGE(s)  API URL")
    now = now_seconds()
    for pid, metadata in previews:
        api_url = metadata.urls.get("8000", "")
        age = now - metadata.created_at
        print(f"{pid:<10}  {metadata.status:<8}  {metadata.branch:<12}  {age:<6}  {api_url}")


def print_preview_summary(metadata: PreviewMetadata) -> None:
    print("Compose status:")
    print(metadata.service_status)
    print(f"Preview URL: {metadata.urls.get('8000', '(missing API tunnel)')}")
    if "8025" in metadata.urls:
        print(f"MailHog URL: {metadata.urls['8025']}")
    print(f"Teardown: modal run preview.py --stop {metadata.preview_id}")
    print_timings(metadata.timings)
    print("Metadata:")
    print(json.dumps(metadata.to_dict(), indent=2))


def start_preview_stack(
    path: str,
    branch: str,
    *,
    preview_id: str = "",
    repo: str = "",
    pr_number: int = 0,
    ref: str = "",
    commit: str = "",
    status: str = "running",
    workspace_archive: Path | None = None,
) -> PreviewHandle:
    started_at = time.monotonic()
    project_path = validate_project_path(path)
    pid = preview_id or make_preview_id()

    print(f"Preview ID: {pid}")
    print(f"Project: {project_path}")

    sb: modal.Sandbox | None = None
    try:
        print("Creating Docker-enabled Modal sandbox...")
        sandbox_start_at = time.monotonic()
        sandbox_app = modal.App.lookup(APP_NAME, create_if_missing=True)
        sb = create_sandbox(sandbox_app, encrypted_ports=[8000, 8025])
        sandbox_created_at = time.monotonic()

        wait_for_docker(sb)
        docker_ready_at = time.monotonic()

        print("Copying project into sandbox...")
        copy_start_at = time.monotonic()
        if workspace_archive is None:
            copy_project(sb, project_path)
        else:
            copy_project_archive(sb, workspace_archive)
        write_env(sb, pid, branch)
        copy_done_at = time.monotonic()

        print("Building compose stack...")
        pull_start_at = time.monotonic()
        compose_pull(sb)
        pull_at = time.monotonic()
        up_start_at = time.monotonic()
        compose_up(sb)
        compose_at = time.monotonic()

        print("Waiting for API health...")
        health_start_at = time.monotonic()
        health = wait_for_health(sb)
        healthy_at = time.monotonic()
        print(f"API healthy: {health}")

        tunnel_start_at = time.monotonic()
        urls = get_tunnel_urls(sb, [8000, 8025])
        tunnel_at = time.monotonic()
        now = now_seconds()
        timings = {
            "sandbox_create_seconds": round(sandbox_created_at - sandbox_start_at, 2),
            "docker_ready_seconds": round(docker_ready_at - sandbox_created_at, 2),
            "file_sync_seconds": round(copy_done_at - copy_start_at, 2),
            "compose_pull_seconds": round(pull_at - pull_start_at, 2),
            "compose_up_seconds": round(compose_at - up_start_at, 2),
            "health_ready_seconds": round(healthy_at - health_start_at, 2),
            "tunnel_ready_seconds": round(tunnel_at - tunnel_start_at, 2),
            "total_seconds": round(tunnel_at - started_at, 2),
        }
        metadata = PreviewMetadata(
            preview_id=pid,
            sandbox_id=sb.object_id,
            branch=branch,
            path=str(project_path),
            urls=urls,
            created_at=now,
            updated_at=now,
            status=status,
            health=health,
            service_status=compose_ps(sb),
            repo=repo,
            pr_number=pr_number,
            ref=ref,
            commit=commit,
            timings=timings,
        )
        return PreviewHandle(metadata=metadata, sandbox=sb)
    except Exception:
        if sb is not None:
            print("Preview failed; collecting compose logs before teardown.")
            try:
                print(compose_logs(sb))
            finally:
                sb.terminate(wait=True)
        raise


def stop_preview_handle(
    metadata: PreviewMetadata,
    sb: modal.Sandbox,
    *,
    delete_preview_record: bool,
    delete_pr_index: bool,
) -> None:
    try:
        compose_down(sb)
    finally:
        sb.terminate(wait=True)
    if delete_preview_record:
        delete_preview(metadata.preview_id)
    if delete_pr_index and metadata.repo and metadata.pr_number:
        delete_pr_preview(metadata.repo, metadata.pr_number)


def stop_preview(preview_id: str, *, delete_pr_index: bool = True) -> bool:
    metadata = get_preview(preview_id)
    if metadata is None:
        print(f"Preview not found: {preview_id}")
        return False

    if metadata.sandbox_id:
        try:
            sb = modal.Sandbox.from_id(metadata.sandbox_id)
            stop_preview_handle(
                metadata,
                sb,
                delete_preview_record=True,
                delete_pr_index=delete_pr_index,
            )
        except Exception as exc:
            print(f"Sandbox cleanup skipped or failed for {metadata.sandbox_id}: {exc}")
            delete_preview(metadata.preview_id)
    else:
        delete_preview(metadata.preview_id)
    if delete_pr_index and metadata.repo and metadata.pr_number:
        delete_pr_preview(metadata.repo, metadata.pr_number)
    print(f"Stopped preview {preview_id}")
    return True


def cleanup_pr_preview(repo: str, pr_number: int) -> None:
    metadata = get_pr_preview(repo, pr_number)
    if metadata is None:
        print(f"No PR preview found for {repo}#{pr_number}.")
        return

    if metadata.preview_id:
        stop_preview(metadata.preview_id, delete_pr_index=False)
    elif metadata.sandbox_id:
        try:
            modal.Sandbox.from_id(metadata.sandbox_id).terminate(wait=True)
        except Exception as exc:
            print(f"Sandbox cleanup skipped or failed for {metadata.sandbox_id}: {exc}")
    delete_pr_preview(repo, pr_number)
    print(f"Deleted PR preview metadata for {repo}#{pr_number}.")


def cleanup_stale_previews(max_age_hours: int) -> None:
    if max_age_hours <= 0:
        raise ValueError("--cleanup-stale-hours must be greater than 0")

    cutoff = now_seconds() - (max_age_hours * 60 * 60)
    stopped = 0
    for pid, metadata in list_previews():
        timestamp = metadata.updated_at or metadata.created_at
        if timestamp and timestamp > cutoff:
            continue
        if stop_preview(pid):
            stopped += 1

    deleted_pr_metadata = 0
    for _key, metadata in list_pr_previews():
        timestamp = metadata.updated_at or metadata.created_at
        stale = not timestamp or timestamp <= cutoff
        missing_preview = bool(metadata.preview_id) and get_preview(metadata.preview_id) is None
        if (stale or missing_preview) and metadata.repo and metadata.pr_number:
            delete_pr_preview(metadata.repo, metadata.pr_number)
            deleted_pr_metadata += 1

    print(f"Stopped {stopped} stale previews.")
    print(f"Deleted {deleted_pr_metadata} stale PR metadata records.")


def show_logs(preview_id: str) -> None:
    metadata = get_preview(preview_id)
    if metadata is None:
        print(f"Preview not found: {preview_id}")
        return
    if not metadata.sandbox_id:
        print(f"Preview has no sandbox id: {preview_id}")
        return
    sb = modal.Sandbox.from_id(metadata.sandbox_id)
    print(compose_logs(sb))


def create_preview(
    path: str,
    branch: str,
    leave_running: bool,
    *,
    repo: str = "",
    pr_number: int = 0,
    ref: str = "",
    commit: str = "",
    workspace_archive: Path | None = None,
) -> PreviewMetadata:
    old_metadata = get_pr_preview(repo, pr_number) if repo and pr_number else None
    handle = start_preview_stack(
        path,
        branch,
        repo=repo,
        pr_number=pr_number,
        ref=ref,
        commit=commit,
        workspace_archive=workspace_archive,
    )
    save_preview(handle.metadata)
    if repo and pr_number:
        save_pr_preview(repo, pr_number, handle.metadata)

    try:
        if repo and pr_number and old_metadata is not None and old_metadata.preview_id != handle.metadata.preview_id:
            print(f"Stopping previous PR preview {old_metadata.preview_id}.")
            stop_preview(old_metadata.preview_id, delete_pr_index=False)

        print_preview_summary(handle.metadata)
        if not leave_running:
            print("Stopping preview because --leave-running was not set.")
            stop_preview_handle(
                handle.metadata,
                handle.sandbox,
                delete_preview_record=True,
                delete_pr_index=bool(repo and pr_number),
            )
            print(f"Stopped preview {handle.metadata.preview_id}")
        return handle.metadata
    except Exception:
        delete_preview(handle.metadata.preview_id)
        if repo and pr_number:
            delete_pr_preview(repo, pr_number)
        raise


def benchmark(path: str, branch: str, runs: int) -> None:
    if runs <= 0:
        raise ValueError("--runs must be greater than 0")

    project_path = validate_project_path(path)
    print(f"Benchmark project: {project_path}")
    print(f"Runs: {runs}")
    print("Each run creates a fresh Sandbox and tears it down after health/tunnel readiness.")

    snapshot_started_at = time.monotonic()
    with project_archive(project_path) as archive:
        snapshot_seconds = round(time.monotonic() - snapshot_started_at, 2)
        print(f"Workspace snapshot archive prepared in {snapshot_seconds:.2f}s.")
        results = []
        for index in range(1, runs + 1):
            print(f"\nBenchmark run {index}/{runs}")
            metadata = create_preview(
                str(project_path),
                f"{branch}-bench-{index}",
                False,
                workspace_archive=archive,
            )
            results.append(metadata)

    timings = [metadata.timings for metadata in results]
    print("\nBenchmark summary:")
    print(f"  Workspace snapshot prep {snapshot_seconds:.2f}s")
    for key, label in TIMING_ORDER:
        values = [float(timing[key]) for timing in timings if isinstance(timing.get(key), int | float)]
        if values:
            print(f"  {label:<22} avg={mean(values):>7.2f}s min={min(values):>7.2f}s max={max(values):>7.2f}s")
