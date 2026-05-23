from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from modal_app.metadata import PreviewMetadata


COMMENT_MARKER = "<!-- compose-preview-lab -->"
GITHUB_API = "https://api.github.com"


def _request(method: str, url: str, token: str, body: dict[str, Any] | None = None) -> Any:
    data = None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "compose-preview-lab",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API failed: {exc.code} {exc.reason}\n{detail}") from exc
    if not raw:
        return None
    return json.loads(raw)


def preview_comment_body(metadata: PreviewMetadata) -> str:
    api_url = metadata.urls.get("8000", "")
    health_url = f"{api_url.rstrip('/')}/health" if api_url else ""
    health = metadata.health or "(not checked)"
    commit = metadata.commit or "(unknown)"
    preview_id = metadata.preview_id or "(unknown)"
    repo = metadata.repo or "owner/repo"
    pr_number = metadata.pr_number or "N"

    lines = [
        COMMENT_MARKER,
        "## Compose Preview Lab",
        "",
        f"Preview URL: {api_url or '(missing API tunnel)'}",
        f"Health: {health}",
        f"Health URL: {health_url or '(not available)'}",
        f"Commit: `{commit}`",
        "",
        f"Teardown: close this PR, or run `uv run modal run preview.py --repo {repo} --stop-pr {pr_number}`.",
        "",
        f"Preview ID: `{preview_id}`",
        "",
        "Alpha caveat: Docker daemon/container state is not snapshotted; containers are recreated for each preview.",
    ]
    return "\n".join(lines)


def upsert_pr_comment(repo: str, pr_number: int, metadata: PreviewMetadata, token: str | None = None) -> None:
    token = token or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN not set; skipping PR comment.")
        return

    comments_url = f"{GITHUB_API}/repos/{repo}/issues/{pr_number}/comments"
    comments = _request("GET", comments_url, token)
    body = preview_comment_body(metadata)
    for comment in comments:
        if COMMENT_MARKER in comment.get("body", ""):
            _request("PATCH", comment["url"], token, {"body": body})
            print(f"Updated PR comment for {repo}#{pr_number}.")
            return

    _request("POST", comments_url, token, {"body": body})
    print(f"Created PR comment for {repo}#{pr_number}.")


def write_github_outputs(metadata: PreviewMetadata) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return

    api_url = metadata.urls.get("8000", "")
    health_url = f"{api_url.rstrip('/')}/health" if api_url else ""
    outputs = {
        "preview_id": metadata.preview_id,
        "preview_url": api_url,
        "health_url": health_url,
        "health": metadata.health,
        "sandbox_id": metadata.sandbox_id,
    }
    with open(output_path, "a", encoding="utf-8") as output:
        for name, value in outputs.items():
            text = str(value)
            if "\n" in text:
                delimiter = f"EOF_{name}"
                while delimiter in text:
                    delimiter += "_"
                output.write(f"{name}<<{delimiter}\n{text}\n{delimiter}\n")
            else:
                output.write(f"{name}={text}\n")
    print(f"Wrote GitHub Actions outputs to {output_path}.")

