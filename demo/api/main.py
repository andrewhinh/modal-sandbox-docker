from __future__ import annotations

import os
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from redis import Redis


DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]


class TodoIn(BaseModel):
    title: str


def db() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL)


def redis_client() -> Redis:
    return Redis.from_url(REDIS_URL, decode_responses=True)


def init_db() -> None:
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id SERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                processed_at TIMESTAMPTZ
            )
            """
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Compose Preview Lab API", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    checks = {"api": "ok"}
    try:
        with db() as conn:
            conn.execute("SELECT 1").fetchone()
        checks["db"] = "ok"
    except Exception as exc:
        checks["db"] = f"error: {exc}"

    try:
        redis_client().ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"

    if checks.get("db") != "ok" or checks.get("redis") != "ok":
        raise HTTPException(status_code=503, detail=checks)
    return checks


@app.post("/todos")
def create_todo(todo: TodoIn) -> dict[str, object]:
    with db() as conn:
        row = conn.execute(
            "INSERT INTO todos (title) VALUES (%s) RETURNING id, title, status",
            (todo.title,),
        ).fetchone()
    redis_client().lpush("todo_jobs", row[0])
    return {"id": row[0], "title": row[1], "status": row[2]}


@app.get("/todos")
def list_todos() -> list[dict[str, object]]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, title, status, created_at, processed_at FROM todos ORDER BY id"
        ).fetchall()
    return [
        {
            "id": row[0],
            "title": row[1],
            "status": row[2],
            "created_at": row[3].isoformat(),
            "processed_at": row[4].isoformat() if row[4] else None,
        }
        for row in rows
    ]


@app.get("/jobs")
def list_jobs() -> dict[str, object]:
    with db() as conn:
        processed = conn.execute("SELECT count(*) FROM todos WHERE status = 'processed'").fetchone()[0]
    return {
        "processed": processed,
        "queued": redis_client().llen("todo_jobs"),
    }

