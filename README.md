# Compose Preview Lab

Every PR gets a live, isolated URL for its full `docker-compose` stack, running
inside a Modal Sandbox.

This is a DevRel-grade example for Modal Docker-in-Sandboxes: bring the exact
multi-service stack teams already use locally, run it in an isolated Sandbox,
forward the API port, and tear it down reliably.

## What This Shows

- `docker compose up --build` inside a Modal Sandbox
- Full-stack app preview per branch or local checkout
- Public Modal tunnel URL for reviewers
- Isolated Postgres, Redis, worker, API, and MailHog per preview
- Preview metadata stored in `modal.Dict`
- Honest alpha handling: Docker daemon/container state is not snapshotted
- Agent/eval loop that edits, rebuilds, and tests the Compose stack

This stays a focused DevRel lab: realistic Compose infra for previews and evals,
not a hosted IDE or agent platform.

## Quickstart

Authenticate Modal first:

```bash
uv run modal setup
```

Start the demo stack and leave it running:

```bash
uv run modal run preview.py --leave-running
```

Run a different Compose project:

```bash
uv run modal run preview.py --path ./demo --branch main --leave-running
```

List previews:

```bash
uv run modal run preview.py --list
```

Show compose logs:

```bash
uv run modal run preview.py --logs <preview-id>
```

Stop a preview:

```bash
uv run modal run preview.py --stop <preview-id>
```

If you omit `--leave-running`, the preview is built, verified, printed, and then
cleaned up immediately.

Benchmark cold starts for the demo stack:

```bash
uv run modal run preview.py --benchmark --runs 3
```

Run the deterministic agent/eval loop:

```bash
uv run modal run preview.py --agent-eval
```

## Example Output

```text
Preview ID: abc123ef
Building compose stack...
API healthy: {"api":"ok","db":"ok","redis":"ok"}
Preview URL: https://modal-host.example
MailHog URL: https://modal-host.example
Teardown: modal run preview.py --stop abc123ef
```

## Demo App

The demo stack is in [demo/compose.yaml](demo/compose.yaml):

- `api`: FastAPI CRUD app on port `8000`
- `worker`: consumes Redis jobs and updates Postgres
- `db`: Postgres 16 with a health check
- `redis`: Redis 7
- `mailhog`: MailHog UI on port `8025`

Endpoints:

- `GET /health`: checks API, DB, and Redis
- `POST /todos`: writes a Postgres row and enqueues a Redis job
- `GET /todos`: reads Postgres
- `GET /jobs`: shows worker progress

Smoke test against the preview URL:

```bash
curl -fsS "$PREVIEW_URL/health"
curl -fsS -X POST "$PREVIEW_URL/todos" \
  -H 'content-type: application/json' \
  -d '{"title":"review compose preview"}'
curl -fsS "$PREVIEW_URL/todos"
curl -fsS "$PREVIEW_URL/jobs"
```

## Architecture

1. `preview.py` creates a Docker-enabled `modal.Sandbox`.
2. The Sandbox starts `dockerd`.
3. The selected local project is copied into `/workspace`.
4. A generated `.env` is written into `/workspace`.
5. The Sandbox runs `docker compose pull || true`.
6. The Sandbox runs `docker compose up --build -d`.
7. The CLI waits for `http://127.0.0.1:8000/health`.
8. Modal exposes encrypted tunnels for ports `8000` and `8025`.
9. Preview metadata is saved in `modal.Dict`.
10. `--stop <preview-id>` runs `docker compose down -v` and terminates the Sandbox.

Key boundary: Docker child containers live inside the Sandbox. Modal manages the
parent Sandbox.

## Repo Layout

```text
compose-preview-lab/
  README.md
  preview.py
  modal_app/
    __init__.py
    sandbox.py
    compose.py
    tunnels.py
    previews.py
    github.py
  demo/
    compose.yaml
    api/
      Dockerfile
      main.py
      requirements.txt
    worker/
      Dockerfile
      worker.py
      requirements.txt
```

## Observability

MVP surfaces:

- compose build output
- compose logs via `--logs`
- `/health` result
- service status from `docker compose ps`
- startup timings in stdout and stored metadata

Each run prints a `timings` object:

```json
{
  "sandbox_create_seconds": 5.2,
  "docker_ready_seconds": 6.0,
  "file_sync_seconds": 0.7,
  "compose_pull_seconds": 30.5,
  "compose_up_seconds": 38.9,
  "health_ready_seconds": 1.6,
  "tunnel_ready_seconds": 0.1,
  "total_seconds": 83.0
}
```

Values vary by region, cache warmth, and image-builder cache state. The
`timings` object printed after each run is the source of truth for your
workspace.

`--benchmark --runs N` creates a fresh Sandbox for each run, waits for health and
tunnels, tears the Sandbox down, then prints avg/min/max seconds for each phase.
It reuses one local workspace archive across runs so the benchmark measures
Sandbox, Docker, Compose, and app readiness rather than repeated local tarball
creation.

## Phase 4: Agent Eval Loop

`--agent-eval` demonstrates why coding agents and evals need realistic
multi-service environments. It creates a Docker-enabled Sandbox, starts the demo
Compose stack, applies a deterministic repo edit, rebuilds/restarts services, and
runs integration checks against API, Postgres, Redis, and the worker.

Default deterministic task:

```bash
uv run modal run preview.py --agent-eval
```

Run more integration cases after the same patch:

```bash
uv run modal run preview.py --agent-eval --eval-runs 3
```

Reuse an existing preview Sandbox:

```bash
uv run modal run preview.py --agent-eval --eval-preview-id <preview-id>
```

Run a custom shell task in `/workspace` before rebuild:

```bash
uv run modal run preview.py \
  --agent-eval \
  --eval-command "sed -i 's/Compose Preview Lab API/Compose Preview Lab API custom/' api/main.py"
```

Use `--eval-task noop` to skip the deterministic edit and only run the Compose
restart plus integration checks.

The report prints `PASS`/`FAIL`, task name, preview id, health output,
integration run count, and timings. This is the local scaffold where a real agent
hook can later write files or run commands; no paid LLM key is required.

## Security Posture

- No secrets by default.
- Demo uses [demo/.env.example](demo/.env.example), not a real `.env`.
- Only ports `8000` and `8025` are forwarded in the MVP.
- Teardown is explicit and documented.
- This is not a hardened multi-tenant PaaS.

## Snapshot Strategy

Docker daemon state is not snapshotted. Compose Preview Lab snapshots the
project workspace as a local tar archive, copies it into each fresh Sandbox, and
recreates containers on boot.

Use snapshots later for:

- repo files
- dependency caches
- generated `.env`
- compose project directory

Do not promise persistent Docker daemon/container snapshots.

## Fast Pull Benchmark Hook

`compose_pull_seconds` is the pull-focused metric. To compare normal images with
optimized pulls, publish a second Compose file that references registry images
prepared with lazy-pull/eStargz-compatible layers, then run:

```bash
uv run modal run preview.py --path ./demo --benchmark --runs 3
uv run modal run preview.py --path ./demo-estargz --benchmark --runs 3
```

This repo does not fabricate eStargz numbers because the result depends on your
registry, image conversion pipeline, and runtime support. Until those images
exist, the benchmark is the repeatable measurement hook.

## Phase 2: GitHub Preview Flow

The repo includes `.github/workflows/compose-preview.yml`.

Required GitHub secrets:

- `MODAL_TOKEN_ID`
- `MODAL_TOKEN_SECRET`

The workflow uses the built-in `GITHUB_TOKEN` to create or update one sticky PR
comment. It runs on same-repo PRs by default; fork PRs usually cannot access
Modal secrets with the standard `pull_request` trigger.

On PR open, synchronize, or reopen:

```yaml
on:
  pull_request:
    types: [opened, synchronize, reopened, closed]
```

The workflow creates a fresh preview for the PR head, stores metadata in
`modal.Dict`, writes GitHub step outputs, and comments the preview URL plus
health result.

```bash
uv run modal run preview.py \
  --path ./demo \
  --repo owner/repo \
  --pr 123 \
  --branch feature-branch \
  --ref feature-branch \
  --commit abc123 \
  --comment
```

Useful outputs in GitHub Actions:

- `preview_id`
- `preview_url`
- `health_url`
- `health`
- `sandbox_id`

On PR close, the workflow stops the Sandbox and deletes PR metadata:

```bash
uv run modal run preview.py --repo owner/repo --stop-pr 123
```

Manual cleanup for old previews:

```bash
uv run modal run preview.py --cleanup-stale-hours 24
```

Cleanup is best-effort: if a stored Sandbox id can be resolved, the command runs
`docker compose down -v` and terminates the Sandbox. If Modal cannot resolve the
old Sandbox handle, metadata cleanup still proceeds where possible.

Set `PREVIEW_PATH` in the workflow if your Compose file is not under `./demo`.

## Non-Goals

- Heroku clone
- Kubernetes replacement
- full IDE
- full agent/eval platform
- hosted multi-tenant SaaS
- persistent Docker daemon snapshots
- generic Docker tutorial
