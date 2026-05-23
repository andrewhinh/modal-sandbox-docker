from __future__ import annotations

import modal

from modal_app.lifecycle import (
    benchmark as run_benchmark,
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
    cleanup_stale_hours: int = 0,
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
    if benchmark:
        run_benchmark(path, branch, runs)
        return

    create_preview(path, branch, leave_running)
