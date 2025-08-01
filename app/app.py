"""The entrypoint for the Squonk2 FastAPI WebSocket service."""

import json
import logging
import os
import sqlite3
import time
import uuid as python_uuid
from logging.config import dictConfig
from typing import Any
from urllib.parse import ParseResult, urlparse

import shortuuid
from dateutil.parser import parse
from fastapi import (
    FastAPI,
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

# Message handler stats interval.
# The recurring number of messages received before stats are emitted.
_MESSAGE_STATS_INTERVAL: int = int(os.getenv("ESS_MESSAGE_STATS_INTERVAL", "800"))
_MESSAGE_STATS_KEY_RECEIVED: str = "received"
_MESSAGE_STATS_KEY_SENT: str = "sent"

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
    _DB_CONNECTION = sqlite3.connect(_DATABASE_PATH)
    _CUR = _DB_CONNECTION.cursor()
    _RES = _CUR.execute("SELECT * FROM es")
    _EVENT_STREAMS = _RES.fetchall()
    _DB_CONNECTION.close()
    for _ES in _EVENT_STREAMS:
        _LOGGER.info(
            "Existing EventStream: %s (id=%s routing_key=%s)",
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

    # Protocol of the service (enumeration).
    protocol: str
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
    stream_from_datetime: str | None = None,
    stream_from_ordinal: int | None = None,
    stream_from_timestamp: int | None = None,
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

    # The following parameters are used to identify the first event in a stream: -
    #
    #   stream_from_datetime - an ISO8601 date/time string
    #   stream_from_ordinal - a message ordinal (integer 0..N)
    #   stream_from_timestamp - an event timestamp (integer) from a prior message
    #
    # Only one of the above is permitted.
    num_stream_from_specified: int = 0
    header_value_error: bool = False
    header_value_error_msg: str = ""
    # Ultimately we set this object...
    offset_specification: ConsumerOffsetSpecification = ConsumerOffsetSpecification(
        OffsetType.NEXT
    )
    # Was a streaming offset provided?
    if stream_from_datetime:
        num_stream_from_specified += 1
        try:
            _LOGGER.info("Given stream_from_datetime=%s", stream_from_datetime)
            from_datetime = parse(stream_from_datetime)
            # We need a RabbitMQ stream timestamp,
            # which is milliseconds since the universal time epoch (1 Jan, 1970).
            # It's easy to get 'seconds', which we multiply by 1,000
            datetime_timestamp = int(time.mktime(from_datetime.timetuple())) * 1_000
            offset_specification = ConsumerOffsetSpecification(
                OffsetType.TIMESTAMP, datetime_timestamp
            )
        except:  # pylint: disable=bare-except
            header_value_error = True
            header_value_error_msg = "Unable to parse stream_from_datetime value"
    if stream_from_ordinal:
        _LOGGER.info("Given stream_from_ordinal=%d", stream_from_ordinal)
        num_stream_from_specified += 1
        try:
            from_ordinal = int(stream_from_ordinal)
            offset_specification = ConsumerOffsetSpecification(
                OffsetType.OFFSET, from_ordinal
            )
        except ValueError:
            header_value_error = True
            header_value_error_msg = "stream_from_ordinal must be an integer"
    if stream_from_timestamp:
        _LOGGER.info("Found stream_from_timestamp=%s", stream_from_timestamp)
        num_stream_from_specified += 1
        try:
            from_timestamp = int(stream_from_timestamp)
            offset_specification = ConsumerOffsetSpecification(
                OffsetType.TIMESTAMP, from_timestamp
            )
        except ValueError:
            header_value_error = True
            header_value_error_msg = "stream_from_timestamp must be an integer"

    # Replace any error with a 'too many values provided' error if necessary
    if num_stream_from_specified > 1:
        header_value_error = True
        header_value_error_msg = "Cannot provide more than one 'stream_from_' variable"

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

    # We use the memcached service to record this connection against the routing key.
    # we create a new unique ID for this call and set it as the value for the stream
    # using the routing key as a memcached key.
    #
    # There may be multiple WebSocket calls for this event stream but we must restrict
    # ourselves to just one. Streams (against the same routing key) check memcached
    # on message retrieval to determine whether they need to shutdown or not.
    #
    # Simply set a new value for the memcached key (the routing key).
    # This might 'knock-out' a previous socket using that key.
    # That's fine - the on_message_for_websocket() function will notice this
    # on the next message and should shut itself down.
    new_socket_uuid: str = str(python_uuid.uuid4())
    _LOGGER.info(
        "Assigned connection ID /%s/ to %s (uuid=%s)", new_socket_uuid, es_id, uuid
    )
    existing_socket_uuid: bytes = _MEMCACHED_CLIENT.get(es_routing_key)
    if existing_socket_uuid and existing_socket_uuid.decode("utf-8") != new_socket_uuid:
        _LOGGER.warning(
            "This replaces connection ID %s for %s (uuid=%s)",
            existing_socket_uuid.decode("utf-8"),
            es_id,
            uuid,
        )
    _MEMCACHED_CLIENT.set(es_routing_key, new_socket_uuid)

    # Start consuming the stream.
    # We don't return from here until there's an error.
    _LOGGER.debug("Consuming %s /%s/...", es_id, new_socket_uuid)
    await _consume(
        consumer,
        es_id=es_id,
        es_routing_key=es_routing_key,
        es_websocket_uuid=new_socket_uuid,
        websocket=websocket,
        offset_specification=offset_specification,
    )

    # On our way out...
    try:
        await websocket.close(
            code=status.WS_1000_NORMAL_CLOSURE, reason="The stream has been deleted"
        )
    except RuntimeError:
        # There's a chance we encounter a RuntimeError
        # with the message 'RuntimeError: Cannot call "send" once a close message has been sent.'
        # We don't care - we have to just get out of here
        # so errors in tear-down have to be ignored,
        # and there's no apparent way to know whether calling 'close()' is safe.
        _LOGGER.debug(
            "Ignoring RuntimeError from close() for %s /%s/", es_id, new_socket_uuid
        )

    _LOGGER.info("Closed WebSocket for %s /%s/ (uuid=%s)", es_id, new_socket_uuid, uuid)


async def generate_on_message_for_websocket(
    websocket: WebSocket,
    *,
    es_id: str,
    es_routing_key: str,
    es_websocket_uuid: str,
    message_stats: dict[str, Any],
):
    """Here we use "currying" to append pre-set parameters
    to a function that will be used as the stream consumer message callback handler.
    We need the callback to 'know' the WebSocket and (for diagnostics) the ES ID
    """
    assert websocket
    assert es_id
    assert es_routing_key
    assert es_websocket_uuid

    async def on_message_for_websocket(
        msg: AMQPMessage, message_context: MessageContext
    ):
        # CAUTION: EVERY possible exception needs to be handled here
        #          otherwise the problem will not be seen,
        #          as exceptions are not exposed to the log and
        #          messages just get dropped.

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
        _LOGGER.debug(
            "Handling message stream=%s es_id=%s /%s/ msg=%s",
            r_stream,
            es_id,
            es_websocket_uuid,
            msg,
        )
        _LOGGER.debug(
            "With offset=%s timestamp=%s /%s/",
            message_context.offset,
            message_context.timestamp,
            es_websocket_uuid,
        )
        # Update message received count
        num_messages_received: int = message_stats[_MESSAGE_STATS_KEY_RECEIVED] + 1
        message_stats[_MESSAGE_STATS_KEY_RECEIVED] = num_messages_received

        # At the time of writing we could not be decoded directly, but we can invoke
        # its built-in __str__() representation to get the message as a string.
        # As a result we get a series of bytes represented as a string (e.g. "b'xyz'").
        # With this we can eval() it to get _real_ bytes and then decode it
        # (we know it's utf-8) in order to get the message as a string! :-O
        try:
            msg_str = eval(str(msg)).decode("utf-8")  # pylint: disable=eval-used
        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOGGER.error(
                "Exception trying to decode message for %s /%s/ msg=%s (%s)",
                es_id,
                es_websocket_uuid,
                msg,
                ex,
            )
            return

        shutdown: bool = False
        # We shutdown if...
        # 1. we are no longer the source of events fo the stream
        #    (e.g. our UUID is not the value of the cached routing key im memcached).
        #    This typically means we've been replaced by a new stream.
        # 2. We get a POISON message
        stream_socket_uuid: str = _MEMCACHED_CLIENT.get(es_routing_key)
        if not stream_socket_uuid:
            _LOGGER.info("There is no connection ID for %s (stopping)...", es_id)
            shutdown = True
        elif stream_socket_uuid.decode("utf-8") != es_websocket_uuid:
            _LOGGER.info(
                "There is a new consumer of %s /%s/, and it is not us /%s/ (stopping)...",
                es_id,
                stream_socket_uuid.decode("utf-8"),
                es_websocket_uuid,
            )
            shutdown = True
        elif msg_str == "POISON":
            _LOGGER.info(
                "Taking POISON for %s /%s/ (stopping)...", es_id, es_websocket_uuid
            )
            shutdown = True
        elif msg_str:
            if msg_str[0] == "{":
                # The EventStream Service is permitted to append to the JSON string
                # as long as it uses keys with the prefix "ess_"
                try:
                    msg_dict: dict[str, Any] = json.loads(msg_str)
                except (
                    json.decoder.JSONDecodeError
                ) as jde:  # pylint: disable=broad-exception-caught
                    _LOGGER.error(
                        "JSONDecodeError for offset %s on %s /%s/ '%s' (skipping) msg_str=%s",
                        message_context.offset,
                        es_id,
                        es_websocket_uuid,
                        jde,
                        msg_str,
                    )
                    return
                # Now decoded to a dictionary if we get here...
                msg_dict["ess_ordinal"] = message_context.offset
                msg_dict["ess_timestamp"] = message_context.timestamp
                msg_str = json.dumps(msg_dict)
            else:
                # The EventStream Service is permitted to append to the protobuf string
                # as long as it uses the '|' delimiter. Here qwe add offset and timestamp.
                msg_str += f"|ordinal: {message_context.offset}"
                msg_str += f"|timestamp: {message_context.timestamp}"

            try:
                # Pass on and count
                await websocket.send_text(msg_str)
                message_stats[_MESSAGE_STATS_KEY_SENT] = (
                    message_stats[_MESSAGE_STATS_KEY_SENT] + 1
                )
            except WebSocketDisconnect:
                _LOGGER.info(
                    "Got WebSocketDisconnect for %s /%s/ (stopping)...",
                    es_id,
                    es_websocket_uuid,
                )
                shutdown = True

        _LOGGER.debug("Handled message for %s /%s/...", es_id, es_websocket_uuid)

        # Consider regular INFO summary.
        # Stats will ultimately be produced if the socket closes,
        # so we just have to consider regular updates here.
        if num_messages_received % _MESSAGE_STATS_INTERVAL == 0:
            _LOGGER.info(
                "Message stats for %s /%s/ %s", es_id, es_websocket_uuid, message_stats
            )

        if shutdown:
            _LOGGER.info(
                "Stopping consumer for %s /%s/ (shutdown)...", es_id, es_websocket_uuid
            )
            message_context.consumer.stop()

    return on_message_for_websocket


async def _consume(
    consumer: Consumer,
    *,
    es_id: str,
    es_routing_key: str,
    es_websocket_uuid: str,
    websocket: WebSocket,
    offset_specification: ConsumerOffsetSpecification,
):
    """An asynchronous generator yielding message bodies from the queue
    based on the provided routing key.
    """
    # A dictionary we pass to the message handler.
    # It can add material to this
    # (most importantly a count of received and sent messages).
    # We'll print this when we leave this consumer.
    message_stats: dict[str, Any] = {
        _MESSAGE_STATS_KEY_RECEIVED: 0,
        _MESSAGE_STATS_KEY_SENT: 0,
    }
    message_handler = await generate_on_message_for_websocket(
        websocket,
        es_id=es_id,
        es_routing_key=es_routing_key,
        es_websocket_uuid=es_websocket_uuid,
        message_stats=message_stats,
    )

    _LOGGER.info(
        "Starting consumer %s /%s/ (offset type=%s offset=%s)...",
        es_id,
        es_websocket_uuid,
        offset_specification.offset_type.name,
        offset_specification.offset,
    )
    await consumer.start()
    _LOGGER.info("Subscribing %s /%s/...", es_id, es_websocket_uuid)
    subscribed: bool = True
    try:
        await consumer.subscribe(
            stream=es_routing_key,
            callback=message_handler,
            decoder=amqp_decoder,
            offset_specification=offset_specification,
        )
    except StreamDoesNotExist:
        _LOGGER.warning("Stream '%s' for %s does not exist", es_routing_key, es_id)
        subscribed = False

    if subscribed:
        _LOGGER.info("Running %s /%s/...", es_id, es_websocket_uuid)
        await consumer.run()
        _LOGGER.info("Stopped %s /%s/ (closing)...", es_id, es_websocket_uuid)
        await consumer.close()
        _LOGGER.info("Closed %s /%s/", es_id, es_websocket_uuid)

    _LOGGER.info(
        "Stopped consuming %s /%s/ %s",
        es_id,
        es_websocket_uuid,
        message_stats,
    )


# Endpoints for the 'internal' event-stream management API -----------------------------


@app_internal.get("/event-stream/version/", status_code=status.HTTP_200_OK)
def get_es_version() -> EventStreamGetVersionResponse:
    """Returns our version information. We're a 'WEBSOCKET'."""
    return EventStreamGetVersionResponse(
        protocol="WEBSOCKET",
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
