#!/usr/bin/env python
"""A simple WebSocket client for local testing.

Usage: simple_es_subscriber.py <location>
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
            print(str(data))
    except (KeyboardInterrupt, EOFError, ConnectionClosed):
        await ws.close()


if __name__ == "__main__":
    asyncio.run(main())
