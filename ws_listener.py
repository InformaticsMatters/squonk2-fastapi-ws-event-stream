#!/usr/bin/env python
"""A simple synchronous WebSocket client for local testing.

Usage: ws_listener.py <location>
"""
import argparse

from simple_websocket import Client, ConnectionClosed


def main(c_args: argparse.Namespace):
    """Connect to the WebSocket and just read messages."""
    headers = {}
    if c_args.datetime_offset:
        headers["X-StreamFromDatetime"] = c_args.datetime_offset
    elif c_args.timestamp_offset:
        headers["X-StreamFromTimestamp"] = c_args.timestamp_offset
    elif c_args.ordinal_offset:
        headers["X-StreamFromOrdinal"] = c_args.ordinal_offset

    ws = Client.connect(c_args.location, headers=headers)
    try:
        while True:
            data = ws.receive()
            print(data)
    except (KeyboardInterrupt, EOFError, ConnectionClosed):
        ws.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="WebSocket Stream Listener",
        description="A simple WebSocket stream listener",
    )
    parser.add_argument("location")
    parser.add_argument("-d", "--datetime-offset")
    parser.add_argument("-t", "--timestamp-offset")
    parser.add_argument("-o", "--ordinal-offset")
    args = parser.parse_args()

    main(args)
