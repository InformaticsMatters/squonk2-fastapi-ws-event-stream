"""The entrypoint for the Squonk2 FastAPI WebSocket service."""

import json
import logging
import os
import sqlite3
import time
from logging.config import dictConfig
from typing import Annotated, Any
from urllib.parse import ParseResult, urlparse

import shortuuid
from dateutil.parser import parse
from fastapi import (
    FastAPI,
    Header,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel
from rstream import (
    AMQPMessage,
    Consumer,
    ConsumerOffsetSpecification,
    MessageContext,
    OffsetType,
    amqp_decoder,
)
from rstream.exceptions import StreamDoesNotExist

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

_AMPQ_URL: str = os.getenv("ESS_AMPQ_URL", "")
assert _AMPQ_URL, "ESS_AMPQ_URL environment variable must be set"

_PARSED_AMPQ_URL: ParseResult = urlparse(_AMPQ_URL)
_AMPQ_HOSTNAME: str | None = _PARSED_AMPQ_URL.hostname
_AMPQ_USERNAME: str | None = _PARSED_AMPQ_URL.username
_AMPQ_PASSWORD: str | None = _PARSED_AMPQ_URL.password
# The URL path will be something like '/as'.
# The vhost we need here is 'as'...
if len(_PARSED_AMPQ_URL.path) > 1:
    _AMPQ_VHOST: str = _PARSED_AMPQ_URL.path[1:]
else:
    _AMPQ_VHOST = "/"

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
    _LOGGER.info("AMPQ_URL: %s", _AMPQ_URL)
    _LOGGER.info("DATABASE_PATH: %s", _DATABASE_PATH)
    _LOGGER.info("INGRESS_LOCATION: %s", _INGRESS_LOCATION)
    _LOGGER.info("INGRESS_SECURE: %s", _INGRESS_SECURE)

    # Create the database.
    # A table to record allocated Event Streams.
    # The table 'id' is an INTEGER PRIMARY KEY and so becomes an auto-incrementing
    # value when NONE is passed in as its value.
    _LOGGER.info("Creating SQLite database (if not present)...")
    _DB_CONNECTION = sqlite3.connect(_DATABASE_PATH)
    _CUR = _DB_CONNECTION.cursor()
    _CUR.execute(
        "CREATE TABLE IF NOT EXISTS es (id INTEGER PRIMARY KEY, uuid TEXT, routing_key TEXT)"
    )
    _DB_CONNECTION.commit()
    _DB_CONNECTION.close()
    _LOGGER.info("Created (or exists)")

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
async def event_stream(
    websocket: WebSocket,
    uuid: str,
    x_streamfromdatetime: Annotated[str | None, Header()] = None,
    x_streamfromordinal: Annotated[str | None, Header()] = None,
    x_streamfromtimestamp: Annotated[str | None, Header()] = None,
):
    """The websocket handler for the event-stream.
    The UUID is returned to the AS when the web-socket is created
    using a POST to /event-stream/.

    The socket will close if a 'POISON' message is received.
    The AS will insert one of these into the stream after it is has been closed.
    i.e. the API will call us to close the connection after it has removed
    the record from its DB and our DB (by calling our delete_es() API method)
    before sending the poison pill.
    """

    # Custom request headers.
    # The following are used to identify the first event in a stream: -
    #
    #   X-StreamFromDatetime - an ISO8601 date/time string
    #   X-StreamFromTimestamp - an event timestamp (integer) from a prior message
    #   X-StreamFromOrdinal - a message ordinal (integer 0..N)
    #
    # Only one of the above is expected.
    num_stream_from_specified: int = 0
    header_value_error: bool = False
    header_value_error_msg: str = ""
    # Ultimately we set this object...
    offset_specification: ConsumerOffsetSpecification = ConsumerOffsetSpecification(
        OffsetType.NEXT
    )
    # Was a streaming offset provided?
    if x_streamfromdatetime:
        num_stream_from_specified += 1
        try:
            _LOGGER.info("X-StreamFromDatetime=%s", x_streamfromdatetime)
            from_datetime = parse(x_streamfromdatetime)
            # We need a RabbitMQ stream timestamp,
            # which is milliseconds since the universal time epoch (1 Jan, 1970).
            # It's easy to get 'seconds', which we multiply by 1,000
            datetime_timestamp = int(time.mktime(from_datetime.timetuple())) * 1_000
            offset_specification = ConsumerOffsetSpecification(
                OffsetType.TIMESTAMP, datetime_timestamp
            )
        except:  # pylint: disable=bare-except
            header_value_error = True
            header_value_error_msg = "Unable to parse X-StreamFromDatetime value"
    if x_streamfromordinal:
        _LOGGER.info("X-StreamFromOrdinal=%s", x_streamfromordinal)
        num_stream_from_specified += 1
        try:
            from_ordinal = int(x_streamfromordinal)
            offset_specification = ConsumerOffsetSpecification(
                OffsetType.OFFSET, from_ordinal
            )
        except ValueError:
            header_value_error = True
            header_value_error_msg = "X-StreamFromOrdinal must be an integer"
    if x_streamfromtimestamp:
        _LOGGER.info("X-StreamFromTimestamp=%s", x_streamfromtimestamp)
        num_stream_from_specified += 1
        try:
            from_timestamp = int(x_streamfromtimestamp)
            offset_specification = ConsumerOffsetSpecification(
                OffsetType.TIMESTAMP, from_timestamp
            )
        except ValueError:
            header_value_error = True
            header_value_error_msg = "X-StreamFromTimestamp must be an integer"

    # Replace any error with a 'too many values provided' error if necessary
    if num_stream_from_specified > 1:
        header_value_error = True
        header_value_error_msg = "Cannot provide more than one X-StreamFrom value"

    if header_value_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=header_value_error_msg,
        )

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

    _LOGGER.info(
        "Waiting for 'accept' on stream %s (uuid=%s routing_key='%s')...",
        es_id,
        uuid,
        routing_key,
    )
    await websocket.accept()
    _LOGGER.info("Accepted connection for %s", es_id)

    _LOGGER.debug(
        "Creating Consumer for %s (%s:%s@%s/%s)...",
        es_id,
        _AMPQ_USERNAME,
        _AMPQ_PASSWORD,
        _AMPQ_HOSTNAME,
        _AMPQ_VHOST,
    )
    consumer: Consumer = Consumer(
        _AMPQ_HOSTNAME,
        username=_AMPQ_USERNAME,
        password=_AMPQ_PASSWORD,
        vhost=_AMPQ_VHOST,
        load_balancer_mode=True,
    )
    if not await consumer.stream_exists(routing_key):
        msg: str = f"EventStream {uuid} cannot be found"
        _LOGGER.warning(msg)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=msg,
        )

    _LOGGER.info("Consuming %s...", es_id)
    await _consume(
        consumer=consumer,
        stream_name=routing_key,
        es_id=es_id,
        websocket=websocket,
        offset_specification=offset_specification,
    )

    await websocket.close(
        code=status.WS_1000_NORMAL_CLOSURE, reason="The stream has been deleted"
    )
    _LOGGER.info("Closed WebSocket for %s", es_id)

    _LOGGER.info("Disconnected %s (uuid=%s)...", es_id, uuid)


async def generate_on_message_for_websocket(websocket: WebSocket, es_id: str):
    """Here we use "currying" to append pre-set parameters
    to a function that wil be used as the stream consumer message callback handler.
    We need the callback to 'know' the WebSocket and (for diagnostics) the ES ID
    """
    assert websocket

    async def on_message_for_websocket(
        msg: AMQPMessage, message_context: MessageContext
    ):
        # The message is expected to be formed from an
        # AMQPMessage generated in the AS using 'body=bytes(message_string, "utf-8")'
        #
        # The MessageContext contains: -
        # - consumer: The Consumer object
        # - subscriber_name: str
        # - offset: int (numerical offset in the stream 0..N)
        # - timestamp: int (milliseconds since Python time epoch)
        #              It's essentially time.time() x 1000
        r_stream = message_context.consumer.get_stream(message_context.subscriber_name)
        _LOGGER.info(
            "Got msg='%s' stream=%s es_id=%s",
            msg,
            r_stream,
            es_id,
        )
        _LOGGER.info(
            "With offset=%s timestamp=%s",
            message_context.offset,
            message_context.timestamp,
        )

        shutdown: bool = False
        #        decoded_msg: str = msg.decode(encoding="utf-8")
        if msg == b"POISON":
            _LOGGER.info("Taking POISON for %s (stopping)...", es_id)
            shutdown = True
        elif msg:
            # The EventStream Service is permitted to append to the protobuf string
            # as long as it uses the '|' delimiter. Here qwe add offset and timestamp.
            # We know the AMQPMessage (as a string will start "b'" and end "'"
            message_string = str(msg)[2:-1]
            if message_string[0] != "{":
                message_string += (
                    f"|{message_context.offset}|{message_context.timestamp}"
                )
                patched_msg: AMQPMessage = AMQPMessage(
                    body=bytes(message_string, "utf-8")
                )
            else:
                patched_msg: AMQPMessage = AMQPMessage(
                    body=bytes(message_string, "utf-8")
                )
            try:
                await websocket.send_text(str(patched_msg))
            except WebSocketDisconnect:
                _LOGGER.info("Got WebSocketDisconnect for %s (stopping)...", es_id)
                shutdown = True

        _LOGGER.debug("Handled msg for %s...", es_id)

        if shutdown:
            _LOGGER.info("Stopping consumer for %s (shutdown)...", es_id)
            message_context.consumer.stop()

    return on_message_for_websocket


async def _consume(
    *,
    consumer: Consumer,
    stream_name: str,
    es_id: str,
    websocket: WebSocket,
    offset_specification: ConsumerOffsetSpecification,
):
    """An asynchronous generator yielding message bodies from the queue
    based on the provided routing key.
    """
    on_message = await generate_on_message_for_websocket(websocket, es_id)

    _LOGGER.info(
        "Starting consumer %s (offset type=%s offset=%s)...",
        es_id,
        offset_specification.offset_type.name,
        offset_specification.offset,
    )
    await consumer.start()
    _LOGGER.info("Subscribing %s...", es_id)
    subscribed: bool = True
    try:
        await consumer.subscribe(
            stream=stream_name,
            callback=on_message,
            decoder=amqp_decoder,
            offset_specification=offset_specification,
        )
    except StreamDoesNotExist:
        _LOGGER.warning("Stream '%s' for %s does not exist", stream_name, es_id)
        subscribed = False

    if subscribed:
        _LOGGER.info("Running %s...", es_id)
        await consumer.run()
        _LOGGER.info("Stopped %s (closing)...", es_id)
        await consumer.close()
        _LOGGER.info("Closed %s", es_id)

    _LOGGER.info("Stopped consuming for %s", es_id)


# Endpoints for the 'internal' event-stream management API -----------------------------


@app_internal.get("/event-stream/version/", status_code=status.HTTP_200_OK)
def get_es_version() -> EventStreamGetVersionResponse:
    """Returns our version information."""
    return EventStreamGetVersionResponse(
        category="WEBSOCKET",
        name="Python FastAPI",
        version=_VERSION,
    )


@app_internal.post("/event-stream/", status_code=status.HTTP_201_CREATED)
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
