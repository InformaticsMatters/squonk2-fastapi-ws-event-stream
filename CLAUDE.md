The Squonk2 Account Server (AS) Event Streaming service: a container image that gives
AS clients WebSocket delivery of events held in RabbitMQ streams. The AS itself creates
and deletes the streams; this service only serves them.

## Commands

Set-up (uv, `[tool.uv] package = false` — there is nothing to build/publish, so uv
never installs the project itself, only its dependencies):

    uv sync
    uv run pre-commit install -t commit-msg -t pre-commit

`uv sync` builds `.venv` from `uv.lock` using the interpreter in `.python-version`
(3.13, matching the container base image). Run project commands through `uv run`.

Lint/format (this is also exactly what CI runs — pylint, black, isort, commitizen):

    uv run pre-commit run --all-files

Run locally (builds the image, plus RabbitMQ and memcached):

    docker compose up --build --detach
    docker compose down

Exercise it (internal API on `8081`, WebSockets on `8080`):

    http localhost:8081/event-stream/version/ -b
    ESS_LOC=$(http post localhost:8081/event-stream/ routing_key=abc -b | jq -r ".location")
    ./ws_listener.py $ESS_LOC          # in one terminal
    ./ampq_publisher.py abc            # in another
    http delete localhost:8081/event-stream/$(echo $ESS_LOC | cut -d/ -f5) -b

`ampq_publisher.py` imports `aio_pika`, which is *not* in `pyproject.toml` or `uv.lock`;
install it separately (`uv run --with aio-pika ./ampq_publisher.py <key>`) if you need
the publisher.

## Testing

**This repository does not use TDD, and this rule overrides the global "always write a
test before the code" instruction.** Do not write tests before (or alongside) changes
here, and do not add a test runner, test dependencies or a CI test job unless explicitly
asked for one.

There is deliberately no test suite and no test runner — pytest is not a dependency.
Meaningful verification of this service needs a live RabbitMQ stream, memcached, two
uvicorn processes and a real WebSocket client, so it is done **in cluster** against a
deployed image rather than in unit tests.

Verify changes by running the stack and exercising it:

    docker compose up --build --detach
    # create a stream, listen, publish (see Commands above)
    docker compose logs -f es

Beyond that, the checks that gate a change are `pre-commit run --all-files` and the
in-cluster behaviour of the built image.

## Architecture

Everything lives in `app/app.py`. It defines **two** FastAPI applications that
`docker-entrypoint.sh` starts as two `uvicorn` processes in the same container:

- `app_public` — port `8080`, the customer-facing WebSocket endpoint
  (`/event-stream/{uuid}`). Started with `public.env` (`IMAGE_ROLE=public`).
- `app_internal` — port `8081`, the REST API the AS calls to create/list/delete
  streams. Started with `internal.env` (`IMAGE_ROLE=internal`).

`IMAGE_ROLE` gates module-level start-up code: only the *internal* process creates the
SQLite schema and logs existing streams. Both processes import the whole module, so any
module-level work you add runs twice unless guarded the same way.

Three pieces of shared state tie the two processes together:

1. **SQLite** at `/data/event-streams.db`, table `es (id, uuid, routing_key)`. Written
   by the internal API, read by the WebSocket handler to resolve a UUID to a routing
   key. Connections are opened and closed per request — there is no pool.
2. **memcached** — maps `routing_key -> connection UUID` and enforces *one live socket
   per stream*. A new connection overwrites the key; the displaced socket notices the
   mismatch on its next message and stops itself. `DELETE` clears the key, which is how
   an existing socket learns its stream was removed.
3. **RabbitMQ streams** via `rstream` — the routing key *is* the stream name. The AS
   creates the stream; the handler refuses the connection (`WS_1013_TRY_AGAIN_LATER`)
   if it does not exist.

Message flow: `_consume()` subscribes a `Consumer` to the stream and runs until stopped.
The callback built by `generate_on_message_for_websocket()` (curried so it captures the
socket and IDs) decodes each message, appends the stream offset and timestamp, and
forwards it. It shuts the consumer down on a memcached mismatch, a `POISON` message, or
`WebSocketDisconnect`. **Every exception inside that callback must be handled locally** —
`rstream` swallows them, so an unhandled one silently drops messages with nothing in the
log.

Message enrichment differs by payload shape: a body starting with `{` is parsed as JSON
and gains `ess_ordinal` / `ess_timestamp` keys; anything else is treated as a protobuf
text string and gains `|ordinal: N|timestamp: N` suffixes. The reserved `ess_` prefix and
the `|` delimiter are part of the contract with clients — see the README for the full
client-facing description of ordinals, timestamps and historical replay.

Historical replay is selected by the query params `stream_from_ordinal`,
`stream_from_timestamp` and `stream_from_datetime` (mutually exclusive; more than one is
a `WS_1002_PROTOCOL_ERROR`). These became *params* in version 3 — versions 1 and 2 used
headers, and version 1 used `pika` with a classic exchange rather than `rstream` streams.

### Configuration

All via environment variables read at import: `ESS_AMPQ_URL` (required, parsed for
host/user/password/vhost), `ESS_INGRESS_LOCATION` and `ESS_INGRESS_SECURE` (used to
build the `ws://` or `wss://` location returned to the AS), `ESS_MEMCACHED_LOCATION`,
`ESS_MESSAGE_STATS_INTERVAL`.

### Kubernetes surface

`probes/*.sh` and `hooks/pre-stop-hook.sh` are copied into the image and referenced by
the deployment (in the peer `squonk2-fastapi-ws-event-stream-ansible` repository). They
signal through files in `${HOME}`: `RUNNING`, `given.poison`, `taken.poison`.

## Conventions

- **Conventional Commits**, enforced by commitizen at `commit-msg`. Allowed types are
  restricted by `schema_pattern` in `pyproject.toml`:
  `build|bump|chore|ci|dev|docs|feat|fix|perf|refactor|remove|style|test`.
- The `VERSION` file is `0.0.0` in the repository; the `tag` workflow overwrites it with
  the git tag at image-build time. Do not hand-edit it to a real version.
- Releases are driven by tags (`X.Y.Z`, no `v` prefix). Pushing a tag builds and pushes
  `informaticsmatters/squonk2-fastapi-ws-event-stream:<tag>` for amd64 and arm64; pushes
  to any branch build `:latest`.
- The Dockerfile is linted by hadolint in CI, and its `apk` packages are version-pinned
  (`.hadolint.yaml` configures the rules).
