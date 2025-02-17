#!/usr/bin/env ash

#set -e

# Run the container using both the customer-facing WebSocket service
# and the internal REST endpoint.
# Done by launching two uvicorn instances in parallel.
echo "+> Launching uvicorn..."
uvicorn app.app:app_public --host 0.0.0.0 --port 8080 --env-file public.env & \
    uvicorn app.app:app_internal --host 0.0.0.0 --port 8081 --env-file internal.env
