#!/usr/bin/env python
"""A simple synchronous WebSocket client for local testing.

Usage: ws_listener.py <location>
"""
import sys

from simple_websocket import Client, ConnectionClosed

if len(sys.argv) != 2:
    print("Usage: ws_listener.py <location>")
    sys.exit(1)

_LOCATION: str = sys.argv[1]


def main():
    """Connect to the WebSocket and just read messages."""
    ws = Client.connect(_LOCATION)
    try:
        while True:
            data = ws.receive()
            print(str(data))
    except (KeyboardInterrupt, EOFError, ConnectionClosed):
        ws.close()


if __name__ == "__main__":
    main()
