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
_public_ WebSockets managed by an _internal_ API, managed by the AS using
the required endpoints: -

    /event-stream/ POST
    /event-stream/ GET
    /event-stream/{id} DELETE

See the AS documentation for more details, and the discussion of the [Event Streams]
service on its internal wiki.

The application runs two **uvicorn** FastAPI processes in the container.
An API listening on port `8081`, and the WebSocket service listening on port `8080`.

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

## Installation
The repository contains an ansible playbook that can be used to simplify the
deployment of the application into a pre-existing Kubernetes **Namespace** but you will
need to customise the playbook by first defining your own variables.

To install the application follow the steps below.

>   You will need access to your Kubernetes cluster and a **KUBECONFIG** file.

From a poetry shell, install the required dependencies: -

    poetry install --with deploy

Move to the `ansible` directory and define your variables: -

    pushd ansible

>   All the _required_ variables can be found at the top of the standard
    `default/main.yaml` file, but you are advised to inspect all the variables to
    ensure they are suitable for your installation (variables are defined in
    `default/main.yaml` and `vars/main.yaml`).

You can create a `parameters.yaml` file and set your variables there.
A `parameters-template.yaml` file is provided as an example. This is protected from
accidental commits as it's in the project's `.gitignore` file: -

    cp parameters-template.yaml parameters.yaml

Then, when you have set your variables, identify your **KUBECONFIG** file,
and run the playbook: -

    export KUBECONFIG=~/k8s-config/nw-xch-dev.yaml
    ansible-playbook site.yaml -e @parameters.yaml

Once deployed the application's internal API will be behind the service
`ess-ws-api` on port `8081`, and available to any application running in the
cluster. The Account Server will be able to manage event streams via the URL
`http://ess-ws-api:8081/event-stream/`.

The external web-socket service will be available on the ingress host you've specified,
as either a `ws://` or `wss://` service, depending on the ingress configuration. If
the host is `example.com` you should be able to connect to an unsecure web socket using
the URL `ws://example.com/event-stream/{uuid}`.

To update the running image (to deploy a new tagged version) just re-run the
playbook with a suitable value for `ess_image_tag`.

To remove the application run the playbook again, but set the `ess_state` variable
to `absent`: -

    ansible-playbook site.yaml -e @parameters.yaml -e ess_state=absent

## Troubleshooting
The deployed application uses the Python logging framework. Significant events
are written to the console, and in a rotating file in `/logs/es.log`.

Access logging is written to the rotating file handler `/logs/access.log`,
and WSGI logging to `/logs/wsgi.log`.

## Local development
You can build and run the service using `docker compose`: -

    docker compose up --build --detach

And shut it down with: -

    docker compose down

You can interact with it using `http`, where you should be able to
get the version of the service, create, and delete event streams
using the internal API: -

    http localhost:8081/event-stream/version/ -b

Here we're using `jq` and `cut` to process the response body to simplify the
subsequent **DELETE** request: -

To create (**POST**) an event stream, run the following:

    ESS_LOC=$(http post localhost:8081/event-stream/ routing_key=abc -b | jq -r ".location")
    echo $ESS_LOC
    ESS_ID=$(echo $ESS_LOC | cut -d/ -f5)
    echo $ESS_ID

To **DELETE** the event stream, run the following:

    http delete localhost:8081/event-stream/$ESS_ID -b

To list (**GET**) all the existing event streams, run the following:

    http localhost:8081/event-stream/ -b

The docker-compose file will start the web socket service on port 8080 and
the internal API on port `8081`. It will also run a RabbitMQ server, which can be found
on port `5672`.

As well as creating and deleting sockets via the internal API some very simple Python
modules have also been provided to inject messages onto the RabbitMQ bus and to
read messages from the corresponding web socket: -

If you have a websocket you can start a simple listener with the following command,
which will print each message received: -

    ./ws_listener.py $ESS_LOC

You can then *inject* a very simple **MerchantCharge** message that will be picked up
by the client using the command: -

    ./ampq_publisher.py <routing_key>

>   By default the publisher uses a built-in AMPQ URL that is assumed to match
    the one used by RabbitMQ in the docker-compose file. If you need to change this
    you can provide a different AMPQ URL as a 2nd argument on the command line.

---

[black]: https://black.readthedocs.io/en/stable
[commitizen]: https://commitizen-tools.github.io/commitizen/
[conventional commit]: https://www.conventionalcommits.org/en/v1.0.0/
[event streams]: https://gitlab.com/informaticsmatters/squonk2-account-server/-/wikis/event-streams
[fastapi]: https://fastapi.tiangolo.com
[pre-commit]: https://pre-commit.com
[poetry]: https://python-poetry.org/
