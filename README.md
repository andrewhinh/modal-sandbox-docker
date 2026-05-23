# Compose Preview Lab

Run a full `docker compose` stack inside a Docker-enabled Modal Sandbox and expose reviewer-friendly tunnel URLs.

## Quickstart

```bash
uv run modal setup
uv run modal run preview.py --leave-running
uv run modal run preview.py --list
uv run modal run preview.py --stop <preview-id>
```

The demo stack in `demo/compose.yaml` includes FastAPI, Postgres, Redis, a worker, and MailHog. If `--leave-running` is omitted, the preview is built, verified, printed, and torn down.

## Architecture

1. Create a Docker-enabled `modal.Sandbox`.
2. Copy the selected Compose project into `/workspace`.
3. Run `docker compose pull || true` and `docker compose up --build -d`.
4. Wait for `http://127.0.0.1:8000/health`.
5. Save typed preview metadata in `modal.Dict`.
6. Stop with `--stop <preview-id>` to run `docker compose down -v` and terminate the Sandbox.

Docker child containers live inside the Sandbox. Modal manages the parent Sandbox.
