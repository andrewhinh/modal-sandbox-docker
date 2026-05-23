from __future__ import annotations

import json
import shlex
import time

import modal

from modal_app.compose import compose_down, compose_logs, compose_ps, compose_up, wait_for_health
from modal_app.lifecycle import start_preview_stack, stop_preview_handle
from modal_app.metadata import delete_preview, get_preview, now_seconds, save_preview
from modal_app.sandbox import WORKDIR, run


AGENT_EVAL_TIMING_ORDER = [
    ("sandbox_create_seconds", "Sandbox create/start"),
    ("docker_ready_seconds", "Docker daemon ready"),
    ("file_sync_seconds", "File sync/write"),
    ("initial_compose_seconds", "Initial compose up"),
    ("initial_health_seconds", "Initial health ready"),
    ("agent_task_seconds", "Agent task"),
    ("compose_restart_seconds", "Compose rebuild/restart"),
    ("post_restart_health_seconds", "Post-restart health"),
    ("integration_check_seconds", "Integration checks"),
    ("tunnel_ready_seconds", "Tunnel ready"),
    ("total_seconds", "Total"),
]


def print_agent_eval_timings(timings: dict[str, object]) -> None:
    print("Agent eval timings:")
    for key, label in AGENT_EVAL_TIMING_ORDER:
        value = timings.get(key)
        if isinstance(value, int | float):
            print(f"  {label:<23} {value:>7.2f}s")


def default_agent_eval_command(eval_task: str) -> str:
    if eval_task == "noop":
        return "echo 'No-op agent task; running integration checks only.'"
    if eval_task != "demo-title":
        raise ValueError("--eval-task must be demo-title or noop when --eval-command is omitted")

    return """
set -euo pipefail
if [ ! -f api/main.py ]; then
  echo "default demo patch expects api/main.py in the Compose project" >&2
  exit 1
fi
if grep -Fq 'Compose Preview Lab API - agent eval' api/main.py; then
  echo "Default patch already applied."
else
  sed -i 's/Compose Preview Lab API/Compose Preview Lab API - agent eval/' api/main.py
  echo "Updated FastAPI title in api/main.py."
fi
""".strip()


def agent_eval_command(eval_task: str, eval_command: str) -> str:
    if eval_command:
        return eval_command
    return default_agent_eval_command(eval_task)


def integration_check_command(title: str) -> str:
    payload = shlex.quote(json.dumps({"title": title}))
    needle = shlex.quote(title)
    return f"""
set -euo pipefail
echo "Checking health"
curl -fsS http://127.0.0.1:8000/health
echo
echo "Creating todo"
created="$(curl -fsS -X POST http://127.0.0.1:8000/todos \\
  -H 'content-type: application/json' \\
  -d {payload})"
echo "$created"
todo_id="$(printf '%s' "$created" | sed -n 's/.*"id":\\([0-9][0-9]*\\).*/\\1/p')"
if [ -z "$todo_id" ]; then
  echo "Could not parse todo id from create response" >&2
  exit 1
fi
echo
echo "Checking todo persistence"
curl -fsS http://127.0.0.1:8000/todos | grep -F {needle}
echo
echo "Waiting for worker"
for _ in $(seq 1 30); do
  todos="$(curl -fsS http://127.0.0.1:8000/todos)"
  echo "$todos"
  if echo "$todos" | grep -F "\\"id\\":$todo_id" | grep -Fq '"status":"processed"'; then
    exit 0
  fi
  sleep 1
done
echo "Worker did not process todo before timeout" >&2
exit 1
""".strip()


def run_integration_check(sb: modal.Sandbox, title: str) -> str:
    return run(
        sb,
        "bash",
        "-lc",
        integration_check_command(title),
        label="integration check",
        workdir=WORKDIR,
        echo=True,
        timeout=120,
    ).stdout


def eval_timings_from_preview(timings: dict[str, object]) -> dict[str, object]:
    eval_timings = dict(timings)
    if "compose_up_seconds" in timings:
        eval_timings["initial_compose_seconds"] = timings["compose_up_seconds"]
    if "health_ready_seconds" in timings:
        eval_timings["initial_health_seconds"] = timings["health_ready_seconds"]
    return eval_timings


def agent_eval(
    path: str,
    branch: str,
    leave_running: bool,
    *,
    eval_preview_id: str = "",
    eval_task: str = "demo-title",
    eval_command: str = "",
    eval_runs: int = 1,
) -> None:
    if eval_runs <= 0:
        raise ValueError("--eval-runs must be greater than 0")

    started_at = time.monotonic()
    sb: modal.Sandbox | None = None
    created_sandbox = not bool(eval_preview_id)
    metadata = None
    timings: dict[str, object] = {}

    try:
        if eval_preview_id:
            metadata = get_preview(eval_preview_id)
            if metadata is None or not metadata.sandbox_id:
                raise ValueError(f"Preview has no sandbox id: {eval_preview_id}")
            print(f"Reusing preview {eval_preview_id} in sandbox {metadata.sandbox_id}.")
            sb = modal.Sandbox.from_id(metadata.sandbox_id)
            initial_health_start_at = time.monotonic()
            health = wait_for_health(sb)
            initial_health_at = time.monotonic()
            timings = eval_timings_from_preview(metadata.timings)
            timings["initial_health_seconds"] = round(initial_health_at - initial_health_start_at, 2)
        else:
            handle = start_preview_stack(path, branch, status="agent-eval-running")
            metadata = handle.metadata
            sb = handle.sandbox
            health = metadata.health
            timings = eval_timings_from_preview(metadata.timings)
            save_preview(metadata)

        print(f"Baseline API healthy: {health}")
        command = agent_eval_command(eval_task, eval_command)
        print(f"Running agent task: {'custom command' if eval_command else eval_task}")
        task_start_at = time.monotonic()
        run(
            sb,
            "bash",
            "-lc",
            command,
            label="agent task",
            workdir=WORKDIR,
            echo=True,
            timeout=10 * 60,
        )
        task_at = time.monotonic()
        timings["agent_task_seconds"] = round(task_at - task_start_at, 2)

        print("Rebuilding/restarting compose services after agent task...")
        restart_start_at = time.monotonic()
        compose_up(sb)
        restart_at = time.monotonic()
        timings["compose_restart_seconds"] = round(restart_at - restart_start_at, 2)

        post_health_start_at = time.monotonic()
        post_health = wait_for_health(sb)
        post_health_at = time.monotonic()
        timings["post_restart_health_seconds"] = round(post_health_at - post_health_start_at, 2)

        print(f"Post-task API healthy: {post_health}")
        checks_start_at = time.monotonic()
        for index in range(1, eval_runs + 1):
            run_integration_check(sb, f"agent-eval-smoke-{index}")
        checks_at = time.monotonic()
        timings["integration_check_seconds"] = round(checks_at - checks_start_at, 2)
        timings["total_seconds"] = round(checks_at - started_at, 2)

        report = {
            "status": "pass",
            "preview_id": metadata.preview_id,
            "sandbox_id": sb.object_id,
            "task": "custom-command" if eval_command else eval_task,
            "integration_runs": eval_runs,
            "health": post_health,
            "timings": timings,
        }
        metadata.status = "running"
        metadata.health = post_health
        metadata.service_status = compose_ps(sb)
        metadata.updated_at = now_seconds()
        metadata.timings = timings
        metadata.agent_eval = report
        save_preview(metadata)

        print("Agent eval: PASS")
        print_agent_eval_timings(timings)
        print("Agent eval report:")
        print(json.dumps(report, indent=2))

        if created_sandbox and not leave_running:
            print("Stopping eval preview because --leave-running was not set.")
            stop_preview_handle(
                metadata,
                sb,
                delete_preview_record=True,
                delete_pr_index=False,
            )
            print(f"Stopped eval preview {metadata.preview_id}")
    except Exception:
        print("Agent eval: FAIL")
        if sb is not None:
            print("Collecting compose logs before cleanup.")
            try:
                print(compose_logs(sb))
            finally:
                if created_sandbox:
                    compose_down(sb)
                    sb.terminate(wait=True)
                    if metadata is not None:
                        delete_preview(metadata.preview_id)
        raise
