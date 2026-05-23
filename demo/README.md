# Demo Compose Stack

This stack proves why Compose Preview Lab exists: one API request crosses four
services.

Flow:

1. `POST /todos` writes a Postgres row.
2. The API pushes a Redis job.
3. The worker consumes the Redis job.
4. The worker updates the Postgres row to `processed`.

Useful endpoints:

- `GET /health`
- `POST /todos`
- `GET /todos`
- `GET /jobs`
- MailHog UI on port `8025`
