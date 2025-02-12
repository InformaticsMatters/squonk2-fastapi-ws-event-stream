#!/usr/bin/env python
"""A simple WebSocket client for local testing.

Usage: simple_ws_client.py <location>
"""
import asyncio
import sys

from simple_websocket import AioClient, ConnectionClosed

_LOCATION: str = sys.argv[1]


async def main():
    """Connect to the WebSocket and just read messages."""
    ws = await AioClient.connect(_LOCATION)
    try:
        while True:
            data = await ws.receive()
            print(f"< {data}")
    except (KeyboardInterrupt, EOFError, ConnectionClosed):
        await ws.close()


if __name__ == "__main__":
    asyncio.run(main())
