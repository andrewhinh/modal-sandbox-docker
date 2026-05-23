from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

import modal


DICT_NAME = "compose-preview-lab-previews"
PR_DICT_NAME = "compose-preview-lab-pr-previews"


@dataclass
class PreviewMetadata:
    preview_id: str
    sandbox_id: str
    branch: str
    path: str
    urls: dict[str, str]
    created_at: int
    updated_at: int
    status: str
    health: str
    service_status: str
    repo: str = ""
    pr_number: int = 0
    ref: str = ""
    commit: str = ""
    timings: dict[str, object] = field(default_factory=dict)
    agent_eval: dict[str, object] | None = None

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "preview_id": self.preview_id,
            "sandbox_id": self.sandbox_id,
            "branch": self.branch,
            "path": self.path,
            "urls": self.urls,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "health": self.health,
            "service_status": self.service_status,
            "repo": self.repo,
            "pr_number": self.pr_number,
            "ref": self.ref,
            "commit": self.commit,
            "timings": self.timings,
        }
        if self.agent_eval is not None:
            data["agent_eval"] = self.agent_eval
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PreviewMetadata:
        urls = data.get("urls", {})
        timings = data.get("timings", {})
        agent_eval = data.get("agent_eval")
        return cls(
            preview_id=str(data.get("preview_id", "")),
            sandbox_id=str(data.get("sandbox_id", "")),
            branch=str(data.get("branch", "")),
            path=str(data.get("path", "")),
            urls={str(key): str(value) for key, value in urls.items()} if isinstance(urls, Mapping) else {},
            created_at=int(data.get("created_at", 0) or 0),
            updated_at=int(data.get("updated_at", 0) or 0),
            status=str(data.get("status", "")),
            health=str(data.get("health", "")),
            service_status=str(data.get("service_status", "")),
            repo=str(data.get("repo", "")),
            pr_number=int(data.get("pr_number", 0) or 0),
            ref=str(data.get("ref", "")),
            commit=str(data.get("commit", "")),
            timings=dict(timings) if isinstance(timings, Mapping) else {},
            agent_eval=dict(agent_eval) if isinstance(agent_eval, Mapping) else None,
        )


def preview_id() -> str:
    return uuid.uuid4().hex[:8]


def now_seconds() -> int:
    return int(time.time())


def metadata_dict() -> modal.Dict:
    return modal.Dict.from_name(DICT_NAME, create_if_missing=True)


def pr_metadata_dict() -> modal.Dict:
    return modal.Dict.from_name(PR_DICT_NAME, create_if_missing=True)


def pr_key(repo: str, pr_number: int) -> str:
    return f"{repo}#{pr_number}"


def save_preview(metadata: PreviewMetadata) -> None:
    metadata_dict().put(metadata.preview_id, metadata.to_dict())


def delete_preview(preview_id: str) -> None:
    metadata_dict().pop(preview_id, None)


def get_preview(preview_id: str) -> PreviewMetadata | None:
    data = metadata_dict().get(preview_id)
    if not isinstance(data, Mapping):
        return None
    return PreviewMetadata.from_dict(data)


def list_previews() -> list[tuple[str, PreviewMetadata]]:
    previews = []
    for key, data in metadata_dict().items():
        if isinstance(data, Mapping):
            previews.append((str(key), PreviewMetadata.from_dict(data)))
    return sorted(previews, key=lambda item: item[1].created_at)


def list_pr_previews() -> list[tuple[str, PreviewMetadata]]:
    previews = []
    for key, data in pr_metadata_dict().items():
        if isinstance(data, Mapping):
            previews.append((str(key), PreviewMetadata.from_dict(data)))
    return sorted(previews, key=lambda item: item[1].updated_at or item[1].created_at)


def save_pr_preview(repo: str, pr_number: int, metadata: PreviewMetadata) -> None:
    pr_metadata_dict().put(pr_key(repo, pr_number), metadata.to_dict())


def delete_pr_preview(repo: str, pr_number: int) -> None:
    pr_metadata_dict().pop(pr_key(repo, pr_number), None)


def get_pr_preview(repo: str, pr_number: int) -> PreviewMetadata | None:
    data = pr_metadata_dict().get(pr_key(repo, pr_number))
    if not isinstance(data, Mapping):
        return None
    return PreviewMetadata.from_dict(data)
