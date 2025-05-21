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
from pymemcache.client.base import Client, KeepaliveOpts
from pymemcache.client.retrying import RetryingClient
from pymemcache.exceptions import MemcacheUnexpectedCloseError
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

# Configure memcached (routing key to ESS UUID map)
# The location is either a host ("localhost")
# or host and port if the port is not the expected default of 11211 ("localhost:1234")
_MEMCACHED_LOCATION: str = os.getenv("ESS_MEMCACHED_LOCATION", "localhost")
_MEMCACHED_KEEPALIVE: KeepaliveOpts = KeepaliveOpts(idle=35, intvl=8, cnt=5)
_MEMCACHED_BASE_CLIENT: Client = Client(
    _MEMCACHED_LOCATION,
    connect_timeout=4,
    encoding="utf-8",
    timeout=0.5,
    socket_keepalive=_MEMCACHED_KEEPALIVE,
)
_MEMCACHED_CLIENT: RetryingClient = RetryingClient(
    _MEMCACHED_BASE_CLIENT,
    attempts=3,
    retry_delay=0.01,
    retry_for=[MemcacheUnexpectedCloseError],
)

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

    _LOGGER.info("Memcached version=%s", _MEMCACHED_CLIENT.version().decode("utf-8"))

    # List existing event streams
    # and ensure the memcached cache reflects this...
    _DB_CONNECTION = sqlite3.connect(_DATABASE_PATH)
    _CUR = _DB_CONNECTION.cursor()
    _RES = _CUR.execute("SELECT * FROM es")
    _EVENT_STREAMS = _RES.fetchall()
    _DB_CONNECTION.close()
    for _ES in _EVENT_STREAMS:
        routing_key: str = _ES[2]
        ess_uuid: str = _ES[1]
        _LOGGER.info(
            "Existing EventStream: %s (id=%s routing_key=%s uuid=%s)",
            _get_location(_ES[1]),
            _ES[0],
            routing_key,
            ess_uuid,
        )
        _MEMCACHED_CLIENT.set(routing_key, ess_uuid)


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

    await websocket.accept()
    _LOGGER.info("Accepted connection (uuid=%s)", uuid)

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
        header_value_error_msg = "Cannot provide more than one X-StreamFrom variable"

    if header_value_error:
        await websocket.close(
            code=status.WS_1002_PROTOCOL_ERROR,
            reason=header_value_error_msg,
        )
        return

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
        await websocket.close(code=status.WS_1000_NORMAL_CLOSURE, reason=msg)
        return

    # Get the ID (for diagnostics)
    # and the routing key for the queue...
    es_id = es[0]
    es_routing_key: str = es[2]

    _LOGGER.info(
        "Creating Consumer for %s (uuid=%s) [%s]...",
        es_id,
        uuid,
        es_routing_key,
    )
    consumer: Consumer = Consumer(
        _AMPQ_HOSTNAME,
        username=_AMPQ_USERNAME,
        password=_AMPQ_PASSWORD,
        vhost=_AMPQ_VHOST,
        load_balancer_mode=True,
    )
    # Before continuing ... does the stream exist?
    # If we don't check it now we'll fail later anyway.
    # The AS is expected to create and delete the streams.
    if not await consumer.stream_exists(es_routing_key):
        msg: str = f"EventStream {uuid} cannot be found"
        _LOGGER.warning(msg)
        await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER, reason=msg)
        return

    # Start consuming the stream.
    # We don't return from here until there's an error.
    _LOGGER.debug("Consuming %s...", es_id)
    await _consume(
        consumer=consumer,
        stream_name=es_routing_key,
        es_id=es_id,
        es_routing_key=es_routing_key,
        es_uuid=uuid,
        websocket=websocket,
        offset_specification=offset_specification,
    )

    # One our way out...
    await websocket.close(
        code=status.WS_1000_NORMAL_CLOSURE, reason="The stream has been deleted"
    )
    _LOGGER.info("Closed WebSocket for %s (uuid=%s)", es_id, uuid)


async def generate_on_message_for_websocket(
    websocket: WebSocket, es_id: str, es_routing_key: str, es_uuid: str
):
    """Here we use "currying" to append pre-set parameters
    to a function that will be used as the stream consumer message callback handler.
    We need the callback to 'know' the WebSocket and (for diagnostics) the ES ID
    """
    assert websocket
    assert es_id
    assert es_routing_key
    assert es_uuid

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
        _LOGGER.debug(
            "With offset=%s timestamp=%s",
            message_context.offset,
            message_context.timestamp,
        )

        shutdown: bool = False
        # We shutdown if...
        # 1. we are no longer the source of events fo the stream
        #    (e.g. our UUID is not the value of the cached routing key im memcached).
        #    This typically means we've been replaced by a new stream.
        # 2. We get a POISON message
        stream_uuid: str = _MEMCACHED_CLIENT.get(es_routing_key)
        if stream_uuid != es_uuid:
            _LOGGER.info(
                "There is a new owner of %s (uuid=%s). It is not me (uuid=%s) (stopping)...",
                es_id,
                stream_uuid,
                es_uuid,
            )
            shutdown = True
        elif msg == b"POISON":
            _LOGGER.info("Taking POISON for %s (stopping)...", es_id)
            shutdown = True
        elif msg:
            # We know the AMQPMessage (as a string will start "b'" and end "'"
            message_string = str(msg)[2:-1]
            if message_string[0] != "{":
                # The EventStream Service is permitted to append to the protobuf string
                # as long as it uses the '|' delimiter. Here qwe add offset and timestamp.
                message_string += f"|offset: {message_context.offset}"
                message_string += f"|timestamp: {message_context.timestamp}"
            else:
                # The EventStream Service is permitted to append to the JSON string
                # as long as it uses keys with the prefix "ess_"
                msg_dict: dict[str, Any] = json.loads(message_string)
                msg_dict["ess_offset"] = message_context.offset
                msg_dict["ess_timestamp"] = message_context.timestamp
                message_string = json.dumps(msg_dict)
            try:
                await websocket.send_text(message_string)
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
    es_routing_key: str,
    es_uuid: str,
    websocket: WebSocket,
    offset_specification: ConsumerOffsetSpecification,
):
    """An asynchronous generator yielding message bodies from the queue
    based on the provided routing key.
    """
    on_message = await generate_on_message_for_websocket(
        websocket, es_id, es_routing_key, es_uuid
    )

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

    _LOGGER.info("Stopped consuming %s", es_id)


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
    es_routing_key: str = request_body.routing_key
    _LOGGER.info(
        "Creating new event stream %s (routing_key=%s)...", uuid_str, es_routing_key
    )

    db = sqlite3.connect(_DATABASE_PATH)
    cursor = db.cursor()
    cursor.execute(f"INSERT INTO es VALUES (NULL, '{uuid_str}', '{es_routing_key}')")
    db.commit()
    # Now pull the record back to get the assigned record ID...
    es = cursor.execute(f"SELECT * FROM es WHERE uuid='{uuid_str}'").fetchone()
    db.close()
    if not es:
        msg: str = (
            f"Failed to get new EventStream record ID for {uuid_str}"
            f" (routing_key={es_routing_key})"
        )
        _LOGGER.error(msg)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=msg,
        )
    _LOGGER.info("Created %s", es)

    # We use the memcached service to record our UUID against the routing key.
    # There should only be one UUID for each routing key but because streams
    # use asyncio and may not reliably detect disconnections we use this
    # mechanism to record which stream is the current stream for each routing key.
    # We ensure that any old streams (against the same routing key) check
    # memcached on message retrieval to determine whether they need to die or not.
    #
    # Simply set the new value of UUID for the memcached key (the routing key).
    # This might 'knock-out' a previous stream registered against that key.
    # That's fine - the asyncio process will notice this on the next message
    # and will kill itself.
    existing_routing_key_uuid: str = _MEMCACHED_CLIENT.get(routing_key)
    if existing_routing_key_uuid and existing_routing_key_uuid != uuid_str:
        _LOGGER.warning(
            "Replaced routing key %s UUID (%s) with ours (%s)",
            routing_key,
            existing_routing_key_uuid,
            uuid_str,
        )
    _MEMCACHED_CLIENT.set(routing_key, uuid_str)

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

    es_routing_key: str = es[2]
    _LOGGER.info(
        "Deleting event stream %s (routing_key=%s uuid=%s)",
        es_id,
        es_routing_key,
        es[1],
    )

    # Clear the memcached value of this routing key.
    # Any asyncio process using this routing key
    # will notice this and kill itself (at some point)
    _MEMCACHED_CLIENT.delete(es_routing_key)

    # Delete the ES record...
    # This will prevent any further connections.
    db = sqlite3.connect(_DATABASE_PATH)
    cursor = db.cursor()
    cursor.execute(f"DELETE FROM es WHERE id={es_id}")
    db.commit()
    db.close()

    _LOGGER.info("Deleted %s", es_id)
