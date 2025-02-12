"""The entrypoint for the Squonk2 FastAPI WebSocket service."""

import uuid
from typing import Dict

from fastapi import FastAPI, HTTPException, WebSocket, status
from pydantic import BaseModel

# Public (event-stream) and internal (REST) services
app_public = FastAPI()
app_internal = FastAPI()

# A map of event stream IDs (a UUID with a "es-" prefix) to their routing keys.
_ES_UUID_MAP: Dict = {}


# We use pydantic to declare the model (request payloads) for the internal REST API.
# The public API is a WebSocket API and does not require a model.
class EventStreamPostRequestBody(BaseModel):
    """/event-stream/ POST request body."""

    routing_key: str


# Endpoints for the 'public-facing' event-stream web-socket API ------------------------


@app_public.websocket("/event-stream/{es_id}")
async def event_stream(websocket: WebSocket, es_id: str):
    """The websocket handler for the event-stream."""
    if not es_id in _ES_UUID_MAP:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"EventStream {es_id} is not known",
        )
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        await websocket.send_text(f"Message text was: {data}")


# Endpoints for the 'internal' event-stream management API -----------------------------


@app_internal.post("/event-stream/")
def post_es(request_body: EventStreamPostRequestBody):
    """Create a new event-stream."""
    uuid_str = f"es-{uuid.uuid4()}"
    _ES_UUID_MAP[uuid_str] = request_body.routing_key
    print(_ES_UUID_MAP)

    return {"id": uuid_str}


@app_internal.delete("/event-stream/{es_id}")
def delete_es(es_id: str):
    """Destroys an existing event-stream."""
    print(_ES_UUID_MAP)
    if es_id not in _ES_UUID_MAP:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"EventStream {es_id} is not known",
        )
    _ = _ES_UUID_MAP.pop(es_id)

    return {}
