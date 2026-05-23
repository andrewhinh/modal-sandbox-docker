from __future__ import annotations

import os
import time

import psycopg
from redis import Redis


DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]


def init_db() -> None:
    with psycopg.connect(DATABASE_URL) as conn:
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


def process(todo_id: str) -> None:
    with psycopg.connect(DATABASE_URL) as conn:
        conn.execute(
            "UPDATE todos SET status = 'processed', processed_at = now() WHERE id = %s",
            (todo_id,),
        )


def main() -> None:
    init_db()
    redis = Redis.from_url(REDIS_URL, decode_responses=True)
    while True:
        item = redis.brpop("todo_jobs", timeout=5)
        if item is None:
            continue
        _, todo_id = item
        process(todo_id)
        time.sleep(0.2)


if __name__ == "__main__":
    main()

