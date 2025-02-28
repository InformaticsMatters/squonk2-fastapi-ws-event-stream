"""The entrypoint for the Squonk2 FastAPI WebSocket service."""

import json
import logging
import os
import sqlite3
from logging.config import dictConfig
from typing import Any

import aio_pika
import shortuuid
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel

# Configure logging
print("Configuring logging...")
_LOGGING_CONFIG: dict[str, Any] = {}
with open("logging.config", "r", encoding="utf8") as stream:
    try:
        _LOGGING_CONFIG = json.loads(stream.read())
    except json.decoder.JSONDecodeError as exc:
        print(exc)
dictConfig(_LOGGING_CONFIG)
print("Configured logging.")

_LOGGER = logging.getLogger(__name__)

# Public (event-stream) and internal (REST) services
app_public = FastAPI()
app_internal = FastAPI()

# Configuration...
_INGRESS_LOCATION: str = os.getenv("ESS_INGRESS_LOCATION", "localhost:8080")
assert _INGRESS_LOCATION, "ESS_INGRESS_LOCATION environment variable must be set"
_INGRESS_SECURE: bool = os.getenv("ESS_INGRESS_SECURE", "no").lower() == "yes"

_AMPQ_EXCHANGE: str = "event-streams"
_AMPQ_URL: str = os.getenv("ESS_AMPQ_URL", "")
assert _AMPQ_URL, "ESS_AMPQ_URL environment variable must be set"

# SQLite database path
_DATABASE_PATH = "/data/event-streams.db"

with open("VERSION", "r", encoding="utf-8") as version_file:
    _VERSION: str = version_file.read().strip()


def _get_location(uuid: str) -> str:
    """Returns the location (URL) for the event stream with the given UUID."""
    location: str = "wss://" if _INGRESS_SECURE else "ws://"
    location += f"{_INGRESS_LOCATION}/event-stream/{uuid}"
    return location


# Logic specific to the 'internal API' process
if os.getenv("IMAGE_ROLE", "").lower() == "internal":
    # Display configuration
    _LOGGER.info("AMPQ_EXCHANGE: %s", _AMPQ_EXCHANGE)
    _LOGGER.info("AMPQ_URL: %s", _AMPQ_URL)
    _LOGGER.info("DATABASE_PATH: %s", _DATABASE_PATH)
    _LOGGER.info("INGRESS_LOCATION: %s", _INGRESS_LOCATION)
    _LOGGER.info("INGRESS_SECURE: %s", _INGRESS_SECURE)

    # Create the database.
    # A table to record allocated Event Streams.
    # The table 'id' is an INTEGER PRIMARY KEY and so becomes an auto-incrementing
    # value when NONE is passed in as it's value.
    _LOGGER.info("Creating SQLite database (if not present)...")
    _DB_CONNECTION = sqlite3.connect(_DATABASE_PATH)
    _CUR = _DB_CONNECTION.cursor()
    _CUR.execute(
        "CREATE TABLE IF NOT EXISTS es (id INTEGER PRIMARY KEY, uuid TEXT, routing_key TEXT)"
    )
    _DB_CONNECTION.commit()
    _DB_CONNECTION.close()
    _LOGGER.info("Created")

    # List existing event streams
    _DB_CONNECTION = sqlite3.connect(_DATABASE_PATH)
    _CUR = _DB_CONNECTION.cursor()
    _RES = _CUR.execute("SELECT * FROM es")
    _EVENT_STREAMS = _RES.fetchall()
    _DB_CONNECTION.close()
    for _ES in _EVENT_STREAMS:
        _LOGGER.info(
            "Existing EventStream: %s (id=%s routing_key='%s')",
            _get_location(_ES[1]),
            _ES[0],
            _ES[2],
        )


# We use pydantic to declare the model (request payloads) for the internal REST API.
# The public API is a WebSocket API and does not require a model.
class EventStreamPostRequestBody(BaseModel):
    """/event-stream/ POST request body."""

    routing_key: str


class EventStreamGetVersionResponse(BaseModel):
    """/event-stream/version/ GET response."""

    # Category of the service (enumeration).
    # We're a 'WEBSOCKET'
    category: str
    # Our name (ours is 'Python FastAPI')
    name: str
    # Our version number
    version: str


class EventStreamPostResponse(BaseModel):
    """/event-stream/ POST response."""

    id: int
    location: str


class EventStreamItem(BaseModel):
    """An individual event stream (returned in the GET response)."""

    id: int
    location: str
    routing_key: str


class EventStreamGetResponse(BaseModel):
    """/event-stream/ POST response."""

    event_streams: list[EventStreamItem]


# Endpoints for the 'public-facing' event-stream web-socket API ------------------------


@app_public.websocket("/event-stream/{uuid}")
async def event_stream(websocket: WebSocket, uuid: str):
    """The websocket handler for the event-stream.
    The UUID is returned to the AS when the web-socket is created
    using a POST to /event-stream/.

    The socket will close if a 'POISON' message is received.
    The AS will insert one of these into the stream after it is has been closed.
    i.e. the API will call us to close the connection (removing the record from our DB)
    before sending the poison pill.
    """

    # Get the DB record for this UUID...
    _LOGGER.debug("Connect attempt (uuid=%s)...", uuid)
    db = sqlite3.connect(_DATABASE_PATH)
    cursor = db.cursor()
    res = cursor.execute(f"SELECT * FROM es WHERE uuid='{uuid}'")
    es = res.fetchone()
    db.close()
    if not es:
        msg: str = f"Connect for unknown EventStream {uuid}"
        _LOGGER.warning(msg)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=msg,
        )

    # Get the ID (for diagnostics)
    # and the routing key for the queue...
    es_id = es[0]
    routing_key: str = es[2]

    _LOGGER.debug(
        "Waiting for 'accept' on stream %s (uuid=%s routing_key='%s')...",
        es_id,
        uuid,
        routing_key,
    )
    await websocket.accept()
    _LOGGER.info("Accepted connection for %s", es_id)

    _LOGGER.debug("Creating reader for %s...", es_id)
    message_reader = _get_from_queue(routing_key)

    _LOGGER.debug(
        "Reading messages for %s (message_reader=%s)...", es_id, message_reader
    )
    _running: bool = True
    while _running:
        _LOGGER.debug("Calling anext() for %s...", es_id)
        reader = anext(message_reader)
        message_body = await reader
        _LOGGER.debug("Got message for %s (message_body=%s)", es_id, message_body)
        if message_body == b"POISON":
            _LOGGER.info("Taking POISON for %s (%s) (closing)...", es_id, uuid)
            _running = False
        else:
            try:
                await websocket.send_text(str(message_body))
            except WebSocketDisconnect:
                _LOGGER.warning("Got WebSocketDisconnect for %s (%s) (closing)...")
                _running = False

    _LOGGER.info("Closing %s (uuid=%s)...", es_id, uuid)
    await websocket.close(
        code=status.WS_1000_NORMAL_CLOSURE, reason="The stream has been deleted"
    )
    _LOGGER.info("Closed %s", es_id)


async def _get_from_queue(routing_key: str):
    """An asynchronous generator yielding message bodies from the queue
    based on the provided routing key.
    """
    connection = await aio_pika.connect_robust(_AMPQ_URL)

    async with connection:
        channel = await connection.channel()
        es_exchange: aio_pika.AbstractExchange = await channel.declare_exchange(
            _AMPQ_EXCHANGE, aio_pika.ExchangeType.DIRECT, durable=True
        )
        queue = await channel.declare_queue(
            auto_delete=True, durable=True, exclusive=True
        )
        await queue.bind(es_exchange, routing_key)

        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    yield message.body


# Endpoints for the 'internal' event-stream management API -----------------------------


@app_internal.post("/event-stream/version/", status_code=status.HTTP_200_CREATED)
def get_es_version() -> EventStreamGetVersionResponse:
    """Returns our version information."""
    # And construct the location we'll be listening on...
    return EventStreamPostResponse(
        category="WEBSOCKET",
        name="Python FastAPI",
        version=_VERSION,
    )


@app_internal.post("/event-stream/", status_code=status.HTTP_201_OK)
def post_es(request_body: EventStreamPostRequestBody) -> EventStreamPostResponse:
    """Create a new event-stream returning the endpoint location.

    The AS provides a routing key to this endpoint and expects a event stream location
    in return.

    This is one of the required endpoints for the Squonk2 event-stream service.
    If successful it must return the location the client can use to read data
    and an ID the event stream is known by (that can be used to delete the stream).
    In our case, it's a WebSocket URL like 'ws://localhost:8000/event-stream/0000'.
    """
    # Generate am new (difficult to guess) UUID for the event stream...
    uuid_str: str = shortuuid.uuid()

    # Create a new ES record.
    # An ID is assigned automatically -
    # we just need to provide a UUID and the routing key.
    routing_key: str = request_body.routing_key
    _LOGGER.info(
        "Creating new event stream %s (routing_key='%s')...", uuid_str, routing_key
    )

    db = sqlite3.connect(_DATABASE_PATH)
    cursor = db.cursor()
    cursor.execute(f"INSERT INTO es VALUES (NULL, '{uuid_str}', '{routing_key}')")
    db.commit()
    # Now pull the record back to get the assigned record ID...
    es = cursor.execute(f"SELECT * FROM es WHERE uuid='{uuid_str}'").fetchone()
    db.close()
    if not es:
        msg: str = (
            f"Failed to get new EventStream record ID for {uuid_str} (routing_key='{routing_key}')"
        )
        _LOGGER.error(msg)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=msg,
        )
    _LOGGER.info("Created %s", es)

    # And construct the location we'll be listening on...
    return EventStreamPostResponse(id=es[0], location=_get_location(uuid_str))


@app_internal.get("/event-stream/", status_code=status.HTTP_200_OK)
def get_es() -> EventStreamGetResponse:
    """Returns a list of the details of all existing event-streams,
    their IDs, locations, and routing keys."""

    _LOGGER.info("Request to get event streams...")

    # Get the ES record (by primary key)
    db = sqlite3.connect(_DATABASE_PATH)
    cursor = db.cursor()
    all_es = cursor.execute("SELECT * FROM es").fetchall()
    db.close()

    event_streams: list[EventStreamItem] = []
    for es in all_es:
        location: str = _get_location(es[1])
        event_streams.append(
            EventStreamItem(id=es[0], location=location, routing_key=es[2])
        )

    _LOGGER.info("Returning %s event stream records", len(event_streams))

    return EventStreamGetResponse(event_streams=event_streams)


@app_internal.delete("/event-stream/{es_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_es(es_id: int):
    """Destroys an existing event-stream."""

    _LOGGER.info("Request to delete event stream %s...", es_id)

    # Get the ES record (by primary key)
    db = sqlite3.connect(_DATABASE_PATH)
    cursor = db.cursor()
    es = cursor.execute(f"SELECT * FROM es WHERE id={es_id}").fetchone()
    db.close()
    if not es:
        msg: str = f"EventStream {es_id} is not known"
        _LOGGER.warning(msg)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=msg,
        )

    _LOGGER.info(
        "Deleting event stream %s (uuid=%s routing_key='%s')", es_id, es[1], es[2]
    )

    # Delete the ES record...
    # This will prevent any further connections.
    db = sqlite3.connect(_DATABASE_PATH)
    cursor = db.cursor()
    cursor.execute(f"DELETE FROM es WHERE id={es_id}")
    db.commit()
    db.close()

    _LOGGER.info("Deleted %s", es_id)
