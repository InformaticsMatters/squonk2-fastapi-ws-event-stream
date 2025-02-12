# Squonk2 FastAPI WebSocket Event Stream

[![build](https://github.com/InformaticsMatters/squonk2-fastapi-ws-event-stream/actions/workflows/build.yaml/badge.svg)](https://github.com/InformaticsMatters/squonk2-fastapi-ws-event-stream/actions/workflows/build.yaml)

[![Conventional Commits](https://img.shields.io/badge/Conventional%20Commits-1.0.0-yellow.svg)](https://conventionalcommits.org)
[![pre-commit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Packaged with Poetry](https://img.shields.io/badge/packaging-poetry-cyan.svg)](https://python-poetry.org/)

A FastAPI (Python) implementation of the Squonk2 (AS) Event Streaming service.

This repository is responsible for the container image that implements
the event-streaming service of the Squonk2 AS platform and is deployed to the
Namespace of the AS to service internal requests from the API to create, delete
and connect to the internal messaging bus to stream events to a client.

The implementation is based on Python and the [FastAPI] framework, offering an
_internal_ API for use by the AS and a _public_ web-socket API for clients to connect to.

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

---

[black]: https://black.readthedocs.io/en/stable
[commitizen]: https://commitizen-tools.github.io/commitizen/
[conventional commit]: https://www.conventionalcommits.org/en/v1.0.0/
[fastapi]: https://fastapi.tiangolo.com
[pre-commit]: https://pre-commit.com
[poetry]: https://python-poetry.org/
