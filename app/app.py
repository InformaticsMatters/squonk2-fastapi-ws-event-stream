"""The entrypoint for the Squonk2 FastAPI WebSocket service."""

import json
import logging
import os
import sqlite3
from logging.config import dictConfig
from typing import Any, Dict

import aio_pika
import shortuuid
from fastapi import FastAPI, HTTPException, WebSocket, status
from pydantic import BaseModel

# Configure logging
print("Configuring logging...")
_LOGGING_CONFIG: Dict[str, Any] = {}
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

_INGRESS_LOCATION: str = os.getenv("INGRESS_LOCATION")
assert _INGRESS_LOCATION, "INGRESS_LOCATION environment variable must be set"

_AMPQ_EXCHANGE: str = "event-streams"
_AMPQ_URL: str = os.getenv("AMPQ_URL")
assert _AMPQ_URL, "AMPQ_URL environment variable must be set"
_LOGGER.info("AMPQ_URL: %s", _AMPQ_URL)

# Create our local database.
# A table to record allocated Event Streams.
# The table 'id' is an INTEGER PRIMARY KEY and so becomes an auto-incrementing
# value when NONE is passed in as it's value.
_DATABASE_PATH = "/data/event-streams.db"
_LOGGER.info("Creating SQLite database (%s)...", _DATABASE_PATH)

_DB_CONNECTION = sqlite3.connect(_DATABASE_PATH)
_CUR = _DB_CONNECTION.cursor()
_CUR.execute(
    "CREATE TABLE IF NOT EXISTS es (id INTEGER PRIMARY KEY, uuid TEXT, routing_key TEXT)"
)
_DB_CONNECTION.commit()
_DB_CONNECTION.close()

_LOGGER.info("Created.")


# We use pydantic to declare the model (request payloads) for the internal REST API.
# The public API is a WebSocket API and does not require a model.
class EventStreamPostRequestBody(BaseModel):
    """/event-stream/ POST request body."""

    routing_key: str


# Endpoints for the 'public-facing' event-stream web-socket API ------------------------


@app_public.websocket("/event-stream/{uuid}")
async def event_stream(websocket: WebSocket, uuid: str):
    """The websocket handler for the event-stream."""

    # Get the DB record for this UUID...
    db = sqlite3.connect(_DATABASE_PATH)
    cursor = db.cursor()
    res = cursor.execute(f"SELECT * FROM es WHERE uuid='{uuid}'")
    es = res.fetchone()
    db.close()
    if not es:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"EventStream {uuid} is not known",
        )

    # Get the ID (for diagnostics)
    # and the routing key for the queue...
    es_id = es[0]
    routing_key: str = es[2]

    await websocket.accept()
    _LOGGER.info(
        "Accepted connection for %s (uuid=%s routing_key=%s)", es_id, uuid, routing_key
    )

    _LOGGER.info("Creating reader for %s...", es_id)
    message_reader = _get_from_queue(routing_key)

    _LOGGER.info("Reading from %s...", es_id)
    while True:
        reader = anext(message_reader)
        message_body = await reader
        _LOGGER.info("Got %s for %s (routing_key=%s)", message_body, es_id, routing_key)
        await websocket.send_text(str(message_body))


async def _get_from_queue(routing_key: str):
    """An asynchronous generator yielding message bodies from the queue
    based on the provided routing key.
    """
    connection = await aio_pika.connect_robust(_AMPQ_URL)

    async with connection:
        channel = await connection.channel()
        es_exchange = await channel.declare_exchange(
            _AMPQ_EXCHANGE,
            aio_pika.ExchangeType.DIRECT,
        )
        queue = await channel.declare_queue(exclusive=True)
        await queue.bind(es_exchange, routing_key=routing_key)

        async with queue.iterator() as queue_iter:
            async for message in queue_iter:
                async with message.process():
                    yield message.body


# Endpoints for the 'internal' event-stream management API -----------------------------


@app_internal.post("/event-stream/")
def post_es(request_body: EventStreamPostRequestBody):
    """Create a new event-stream returning the endpoint location.

    The AS provides a routing key to this endpoint and expects a event stream location
    in return.

    This is one of the required endpoints for the Squonk2 event-stream service.
    If successful it must return the location the client can use to read data
    and an ID the event stream is known by (that can be used to delete the stream).
    In our case, it's a WebSocket URL like 'ws://localhost:8000/event-stream/0000'.
    """
    uuid_str: str = shortuuid.uuid()
    location: str = f"ws://{_INGRESS_LOCATION}/event-stream/{uuid_str}"

    # Create a new ES record...
    # An ID is assigned automatically -
    # we just need to supply a short UUID and corresponding location.
    _LOGGER.info("Creating new event stream: %s", uuid_str)

    db = sqlite3.connect(_DATABASE_PATH)
    cursor = db.cursor()
    cursor.execute(
        f"INSERT INTO es VALUES (NULL, '{uuid_str}', '{request_body.routing_key}')"
    )
    db.commit()
    # Now pull the record back to get the assigned numeric ID...
    res = cursor.execute(f"SELECT * FROM es WHERE uuid='{uuid_str}'")
    es = res.fetchone()
    assert es, "Failed to insert new event stream"
    db.close()

    _LOGGER.info("Created %s", es)

    return {
        "id": es[0],
        "location": location,
    }


@app_internal.delete("/event-stream/{es_id}")
def delete_es(es_id: int):
    """Destroys an existing event-stream."""

    _LOGGER.info("Deleting event stream: %s", es_id)

    # Get the ES record (by primary key)
    db = sqlite3.connect(_DATABASE_PATH)
    cursor = db.cursor()
    res = cursor.execute(f"SELECT * FROM es WHERE id={es_id}")
    es = res.fetchone()
    db.close()
    if not es:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"EventStream {es_id} is not known",
        )

    # Delete the ES record...
    db = sqlite3.connect(_DATABASE_PATH)
    cursor = db.cursor()
    cursor.execute(f"DELETE FROM es WHERE id={es_id}")
    db.commit()
    db.close()

    _LOGGER.info("Deleted %s", es_id)

    return {}
