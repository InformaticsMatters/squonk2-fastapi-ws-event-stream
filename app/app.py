"""The entrypoint for the Squonk2 FastAPI WebSocket service."""

from fastapi import FastAPI

# Public (event-stream) and internal (REST) services
app_es = FastAPI()
app_internal = FastAPI()


# Endpoints for the 'public-facing' event-stream web-socket API ------------------------


@app_es.get("/event-stream/")
def event_stream():
    """Read from the event-stream."""
    return {"message": "MESSAGE"}


# Endpoints for the 'internal' event-stream management API -----------------------------


@app_internal.post("/event-stream/")
def post_es():
    """Create a new event-stream."""
    return {"id": 1}


@app_internal.delete("/event-stream/{es_id}")
def delete_es(es_id: int):
    """Destroys an existing event-stream."""
    return {"message": f"Destroy ES {es_id}"}
