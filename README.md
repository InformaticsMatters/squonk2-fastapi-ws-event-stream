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
and manager Event Streams for client applications. Clients interact with the AS
API to create and delete Event Streams, but is this application that is
responsible for the WebSocket endpoints and for delivery of events to the client.

The implementation is based on Python and the [FastAPI] framework, offering
_public_ WebSockets managed by an _internal_ API (that the AS uses) and provides
the following (required) endpoints: -

    /event-stream/version/ GET
    /event-stream/ POST
    /event-stream/ GET
    /event-stream/{id} DELETE

See the AS documentation for more details, and the discussion of the [Event Streams]
service on its internal wiki.

The application runs two **uvicorn** FastAPI processes in the container.
An internal API listening on port `8081` used by the AS, and the public WebSocket
service listening on port `8080`.

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
as either a `ws://` or `wss://` service, depending on whether you have set
the Ansible variable `ess_cert_issuer`. If the host is `example.com` you should be able
to connect to an unsecured web socket using the URL `ws://example.com/event-stream/{uuid}`
or `wss://example.com/event-stream/{uuid}` for a secured connection.

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

## Version 1
Version 1 uses the `pika` package and relies on a classic exchange-based
RabbitMQ Topic Queue.

## Version 2
Version 2 uses the `rstream` package and relies on a RabbitMQ stream.

Version 2 also extends messages prior to forwarding them to their socket clients.
It does this by appending the messages's **ordinal** (an `integer`, known as the
_offset_ in the RabbitMQ stream), and  **timestamp** (also an `integer`),
both of which are provided by the backing RabbitMQ stream as the messages are received.
You can also use the **ordinal** as a unique message identifier.

When received as a protobuf string the values are appended to the end of the original
message using `|` as a delimiter. Here is an example, with the message split at the
delimiter for clarity: -

    accountserver.MerchantProcessingCharge
    |timestamp: "2025-04-30T19:20:37.926+00:00" merchant_kind: "DATA_MANAGER" [...]
    |ordinal: 2
    |timestamp: 1746042171620

JSON strings will have these values added to the received dictionary using the
keys `ess_ordinal` and `ess_timestamp`, again, displayed here over several lines
for clarity: -

    {
        "ess_ordinal": 2,
        "ess_timestamp": 1746042171620,
        "message_type": "accountserver.MerchantProcessingCharge",
        "message_body": {
            "timestamp": "2025-04-30T19:20:37.926+00:00",
            "merchant_kind": "DATA_MANAGER"
        }
    }

-   The `"timestamp"` in the `"message_body"` is the date/time and timezone string
    representation of the time the message was created (in the Merchant or AS).
    Every message will contain a timestamp value.

-   The `"ess_timestamp"` is a RabbitMQ-generated numerical value.
    it is not necessarily the same as the message `"timestamp"`.
    It is a measurement of milliseconds since the universal time epoch (1 Jan, 1970).

-   The `"ess_ordinal"` is a unique increasing value that represents the position of
    the message in the underlying RabbitMQ stream in which is held with the first message
    in the stream having an offset of `1`.

To distinguish between "old" and "new" messages you should use either the
`"ess_ordinal"` or `"ess_timestamp"`, they are guaranteed monotonic.
Consecutive messages may not have increasing `"timestamp"` values but they will have
increasing `"ess_timestamp"` and increasing `"ordinal"` values. This is because the DM
and AS both generate messages that you might see. Even if their clocks are synchronised
the DM messages have to pass through a queue and socket (and transmission is not
instantaneous) so you can expect messages to be delayed with respect to those generated
on the AS. To guarantee not miss a message you must use the `"ordinal"`.
If you cannot, use the `"ess_timestamp"`.

## Version 3
Version 3 uses **params** for the specification of historical events. Version 2
used header values to provide these values.

## Connecting to sockets (historical events)
The streaming service keeps historical events based on a maximum age and file size.
Consequently you can connect to your websocket and retrieve these historical events
as long as they remain in the backend streaming queue. You identify the
start-time of your events by using **params** in the websocket request for your
stream's **LOCATION**.

If you do not provide any value your socket will only deliver new events.

You can select the start of your events buy providing either an **ordinal**,
**timestamp**, or **datetime**. The first event delivered will be the next
event after the given reference. For example, if you provide **ordinal** `100`,
the next event you can expect to receive is an event with **ordinal** `101`.

-   To stream from a specific **ordinal**, provide it as the numerical value
    of the request parameter  `stream_from_ordinal`. Passing in a value of `0`
    will ensure you receive the first message in the stream (which has an ordinal of `1`)

-   To stream from a specific **timestamp**, provide it as the numerical value
    of the request parameter  `stream_from_timestamp`. It is interpreted
    as a measurement of milliseconds since the universal time epoch (1 Jan, 1970).

-   To stream from a specific **datetime**, provide the date/time string as the value
    of the request parameter  `stream_from_datetime`. The datetime string is extremely
    flexible and is interpreted by the [python-dateutil] package's `parse` function.
    UTC is used as the reference for messages, and the string will be interpreted as a
    UTC value if it has no timezone specification. If you are in CEST for example, and
    it is `13:33`, and you want to retrieve times from 13:33 (local time), then you
    will need to provide a string value that has the date you are interested in,
    and the time set to `11:33` (the UTC time for 13:33 CEST) or specify `13:33+02:00`.
    Internally, the value is translated to the nearest approximate `timestamp` value.

You can only provide one type of historical reference. If you provide a
value for `stream_from_ordinal` for example, you cannot also provide
a value for `stream_from_timestamp`.

## Stream storage
The Account Server (AS) relies on [RabbitMQ] for its event streaming service,
ans storage capacity for each stream is configured by the AS. Importantly events
are not retained indefinitely, and typically only for a few days. Consequently,
if you disconnect from an event stream for a long period of time any **ordinal**
you have kept may have been lost. In this situation the Event Streaming service
will just deliver the first event it has available.

You will know that you have lost messages if you keep a record of the most recent
message **ordinal**. If you received **ordinal** `100` and then, some time later,
re-connect using an `stream_from_ordinal` value of `100`, and the first message
your receive has the **ordinal** `150` then you can assume 49 messages have been lost.

Having said all this the AS stream storage configuration is generous and, depending
on event message size, and your streams message rate, the AS should be able to retain
events for several days.

>   Recording **ordinals** (or **timestamps**) for every message you
    receive may not be practical as this may impact your application performance,
    especially if you are storing these values in a file or database. You might
    instead record message references in _blocks_ of 100, or 1,000.

---

[black]: https://black.readthedocs.io/en/stable
[commitizen]: https://commitizen-tools.github.io/commitizen/
[conventional commit]: https://www.conventionalcommits.org/en/v1.0.0/
[event streams]: https://gitlab.com/informaticsmatters/squonk2-account-server/-/wikis/event-streams
[fastapi]: https://fastapi.tiangolo.com
[pre-commit]: https://pre-commit.com
[poetry]: https://python-poetry.org
[python-dateutil]: https://github.com/dateutil/dateutil
[rabbitmq]: https://www.rabbitmq.com
