# Squonk2 FastAPI WebSocket Event Stream

[![build](https://github.com/InformaticsMatters/squonk2-fastapi-ws-event-stream/actions/workflows/build.yaml/badge.svg)](https://github.com/InformaticsMatters/squonk2-fastapi-ws-event-stream/actions/workflows/build.yaml)
[![tag](https://github.com/InformaticsMatters/squonk2-fastapi-ws-event-stream/actions/workflows/tag.yaml/badge.svg)](https://github.com/InformaticsMatters/squonk2-fastapi-ws-event-stream/actions/workflows/tag.yaml)

![Architecture](https://img.shields.io/badge/architecture-amd64%20%7C%20arm64-lightgrey)

![GitHub Release](https://img.shields.io/github/v/release/InformaticsMatters/squonk2-fastapi-ws-event-stream)

[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow.svg)](https://conventionalcommits.org)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Packaged with Poetry](https://img.shields.io/badge/packaging-poetry-cyan.svg)](https://python-poetry.org/)

A FastAPI (Python) implementation of the Squonk2 (AS) Event Streaming service.

This repository is responsible for the container image that implements
the event-streaming service of the Squonk2 AS platform and is deployed to the
Namespace of the AS to service internal requests from the API to create, delete
and connect to the internal messaging bus to stream events to a client.

The implementation is based on Python and the [FastAPI] framework, offering
_public_ web-sockets managed by an _internal_ API, managed by the AS using
the required endpoint: -

    /event-stream POST
    /event-stream/{id} DELETE

See the AS Event Stream API documentation for more details, and the discussion
of the [Event Streams] service on its internal wiki.

## Contributing
The project uses: -

- [pre-commit] to enforce linting of files prior to committing them to the
  upstream repository
- [Commitizen] to enforce a [Conventional Commit] commit message format
- [Black] as a code formatter
- [Poetry] as a package manager (for the b/e)

You **MUST** comply with these choices in order to  contribute to the project.

To get started review the pre-commit utility and the conventional commit style
and then set-up your local clone by following the **Installation** and
**Quick Start** sections: -

    poetry shell
    poetry install --with dev
    pre-commit install -t commit-msg -t pre-commit

Now the project's rules will run on every commit, and you can check the
current health of your clone with: -

    pre-commit run --all-files

## Local execution
You can build and run the service using `docker compose`: -

    docker compose up --build --detach

You can interact with it using `http`, where you should be able to create
and delete event streams using the internal API. Here we're using
`jq` and `cut` to process the response body to simplify the subsequent **DELETE**
request: -

To create (**POST**) an event stream, run the following:

    ES_LOC=$(http post localhost:8081/event-stream/ routing_key=0123456789 -b | jq -r ".location")
    echo $ES_LOC
    ES_ID=$(echo $ES_LOC | cut -d/ -f5)
    echo $ES_ID

To **DELETE** the event stream, run the following:

    http delete localhost:8081/event-stream/$ES_ID -b

To list (**GET**) all the existing event streams, run the following:

    http localhost:8081/event-stream/ -b

---

[black]: https://black.readthedocs.io/en/stable
[commitizen]: https://commitizen-tools.github.io/commitizen/
[conventional commit]: https://www.conventionalcommits.org/en/v1.0.0/
[event streams]: https://gitlab.com/informaticsmatters/squonk2-account-server/-/wikis/event-streams
[fastapi]: https://fastapi.tiangolo.com
[pre-commit]: https://pre-commit.com
[poetry]: https://python-poetry.org/
