from __future__ import annotations

import modal

from modal_app.github import upsert_pr_comment, write_github_outputs
from modal_app.lifecycle import (
    benchmark as run_benchmark,
    cleanup_pr_preview,
    cleanup_stale_previews,
    create_preview,
    print_preview_table,
    show_logs,
    stop_preview,
)
from modal_app.sandbox import APP_NAME


app = modal.App(APP_NAME)


@app.local_entrypoint()
def main(
    path: str = "./demo",
    branch: str = "local",
    leave_running: bool = False,
    list: bool = False,
    stop: str = "",
    logs: str = "",
    repo: str = "",
    pr: int = 0,
    stop_pr: int = 0,
    ref: str = "",
    commit: str = "",
    cleanup_pr: int = 0,
    cleanup_stale_hours: int = 0,
    comment: bool = False,
    benchmark: bool = False,
    runs: int = 3,
) -> None:
    if list:
        print_preview_table()
        return
    if stop:
        stop_preview(stop)
        return
    if logs:
        show_logs(logs)
        return
    if cleanup_stale_hours:
        cleanup_stale_previews(cleanup_stale_hours)
        return
    if stop_pr:
        if not repo:
            raise ValueError("--repo is required with --stop-pr")
        cleanup_pr_preview(repo, stop_pr)
        return
    if cleanup_pr:
        if not repo:
            raise ValueError("--repo is required with --cleanup-pr")
        cleanup_pr_preview(repo, cleanup_pr)
        return
    if pr and not repo:
        raise ValueError("--repo is required with --pr")
    if benchmark:
        run_benchmark(path, branch, runs)
        return

    metadata = create_preview(
        path,
        branch,
        leave_running or bool(pr),
        repo=repo,
        pr_number=pr,
        ref=ref,
        commit=commit,
    )
    write_github_outputs(metadata)
    if comment:
        if not repo or not pr:
            raise ValueError("--repo and --pr are required with --comment")
        upsert_pr_comment(repo, pr, metadata)
