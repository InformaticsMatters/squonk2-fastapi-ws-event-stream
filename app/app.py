"""The entrypoint for the Squonk2 FastAPI WebSocket service."""

import logging
import os
from typing import Dict

import aio_pika
import shortuuid
from fastapi import FastAPI, HTTPException, WebSocket, status
from pydantic import BaseModel

logging.basicConfig(level=logging.DEBUG)
_LOGGER = logging.getLogger(__name__)

# Public (event-stream) and internal (REST) services
app_public = FastAPI()
app_internal = FastAPI()

# A map of event streams (a short UUID) to their routing keys.
_ES_ROUTING_MAP: Dict[str, str] = {}
# A map of event streams IDs to their UUIDs.
_ES_UUID_MAP: Dict[int, str] = {}

_INGRESS_LOCATION: str = os.getenv("INGRESS_LOCATION")
assert _INGRESS_LOCATION, "INGRESS_LOCATION environment variable must be set"

_AMPQ_EXCHANGE: str = "event-streams"
_AMPQ_URL: str = os.getenv("AMPQ_URL")
assert _AMPQ_URL, "AMPQ_URL environment variable must be set"
_LOGGER.info("AMPQ_URL: %s", _AMPQ_URL)


# We use pydantic to declare the model (request payloads) for the internal REST API.
# The public API is a WebSocket API and does not require a model.
class EventStreamPostRequestBody(BaseModel):
    """/event-stream/ POST request body."""

    routing_key: str


# Endpoints for the 'public-facing' event-stream web-socket API ------------------------


@app_public.websocket("/event-stream/{es_id}")
async def event_stream(websocket: WebSocket, es_id: str):
    """The websocket handler for the event-stream."""
    _LOGGER.info("_ES_UUID_MAP: %s", _ES_UUID_MAP)

    #    if not es_id in _ES_UUID_MAP:
    #        raise HTTPException(
    #            status_code=status.HTTP_404_NOT_FOUND,
    #            detail=f"EventStream {es_id} is not known",
    #        )
    await websocket.accept()
    _LOGGER.info("Accepted connection for event-stream %s", es_id)

    #    routing_key: str = _ES_UUID_MAP[es_id]
    routing_key: str = "abcdefgh"
    _LOGGER.info("Creating reader for routing key %s (%s)", routing_key, es_id)
    message_reader = get_from_queue(routing_key)

    _LOGGER.info("Reading from %s (%s)...", routing_key, es_id)
    while True:
        reader = anext(message_reader)
        message_body = await reader
        _LOGGER.info("Got %s from %s (%s)", message_body, routing_key, es_id)
        await websocket.send_text(str(message_body))


async def get_from_queue(routing_key: str):
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
    uuid_str = shortuuid.uuid()
    next_id = len(_ES_UUID_MAP) + 1
    _ES_ROUTING_MAP[uuid_str] = request_body.routing_key
    _ES_UUID_MAP[next_id] = uuid_str
    _LOGGER.info("_ES_ROUTING_MAP: %s", _ES_ROUTING_MAP)
    _LOGGER.info("_ES_UUID_MAP: %s", _ES_UUID_MAP)

    return {
        "id": next_id,
        "location": f"ws://{_INGRESS_LOCATION}/event-stream/{uuid_str}",
    }


@app_internal.delete("/event-stream/{es_id}")
def delete_es(es_id: int):
    """Destroys an existing event-stream."""
    _LOGGER.info("_ES_UUID_MAP: %s", _ES_UUID_MAP)
    if es_id not in _ES_UUID_MAP:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"EventStream {es_id} is not known",
        )

    es_uuid = _ES_UUID_MAP[es_id]
    _ = _ES_UUID_MAP.pop(es_id)
    _ = _ES_ROUTING_MAP.pop(es_uuid)

    return {}
