#!/usr/bin/env bash

#set -e

# Run the container using both the customer-facing WebSocket service
# and the internal REST endpoint.
# Done my simply launching two uvicorn instances in parallel.
echo "+> Launching uvicorn..."
uvicorn app.app:app_public --reload --host 0.0.0.0 --port 8080 & \
    uvicorn app.app:app_internal --reload --host 0.0.0.0 --port 8081
