"""The entrypoint for the Squonk2 FastAPI WebSocket service."""

import uuid

from fastapi import FastAPI, HTTPException, WebSocket, status

# Public (event-stream) and internal (REST) services
app_public = FastAPI()
app_internal = FastAPI()

_ES_UUID_SET: set = set()


# Endpoints for the 'public-facing' event-stream web-socket API ------------------------


@app_public.websocket("/event-stream/{es_id}")
async def event_stream(websocket: WebSocket, es_id: str):
    """The websocket handler for the event-stream."""
    if not es_id in _ES_UUID_SET:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"EventStream {es_id} is not known",
        )
    await websocket.accept()
    while True:
        data = await websocket.receive_text()
        await websocket.send_text(f"Message text was: {data}")


# Endpoints for the 'internal' event-stream management API -----------------------------


@app_internal.post("/event-stream/")
def post_es():
    """Create a new event-stream."""
    uuid_str = f"es-{uuid.uuid4()}"
    _ES_UUID_SET.add(uuid_str)
    return {"id": uuid_str}


@app_internal.delete("/event-stream/{es_id}")
def delete_es(es_id: str):
    """Destroys an existing event-stream."""
    if es_id not in _ES_UUID_SET:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"EventStream {es_id} is not known",
        )
    _ES_UUID_SET.remove(es_id)
    return {}
