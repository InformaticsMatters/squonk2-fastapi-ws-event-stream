#!/usr/bin/env bash

#set -e

# Run the container using both the customer-facing WebSocket service
# and the internal REST endpoint.
# Done my simply launching two uvicorn instances in parallel.
echo "+> Launching uvicorn..."
uvicorn app.app:app_p --reload --host 0.0.0.0 --port 8080 & \
    uvicorn app.app:app_i --reload --host 0.0.0.0 --port 8081
