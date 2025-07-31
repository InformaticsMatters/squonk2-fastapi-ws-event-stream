#!/usr/bin/env python
"""A simple synchronous WebSocket client for local testing.

Usage: ws_listener.py <location>
"""
import argparse
import json
import ssl

import certifi
from simple_websocket import Client, ConnectionClosed


def main(c_args: argparse.Namespace):
    """Connect to the WebSocket and just read messages."""
    params = ""
    if c_args.datetime_offset:
        params = f"?stream_from_datetime={c_args.datetime_offset}"
    elif c_args.timestamp_offset:
        params = f"?stream_from_timestamp={c_args.timestamp_offset}"
    elif c_args.ordinal_offset:
        params = f"?stream_from_ordinal={c_args.ordinal_offset}"

    # Compensate for observed CERTIFICATE_VERIFY_FAILED errors
    # when using secure web-sockets
    context = (
        ssl.create_default_context(cafile=certifi.where())
        if c_args.location.startswith("wss:")
        else None
    )

    total_bytes = 0
    total_messages = 0
    min_bytes = 1_000_000
    max_bytes = 0
    url = f"{c_args.location}{params}"
    ws = Client.connect(url, ssl_context=context)
    try:
        while True:

            data = ws.receive()

            # Collect some stats
            data_len = len(data)
            total_bytes += data_len
            min_bytes = min(min_bytes, data_len)
            max_bytes = max(max_bytes, data_len)
            total_messages += 1

            msg_type = "(unknown)"
            msg = "(unknown)"
            ordinal = "(unknown)"
            timestamp = "(unknown)"
            if data[0] == "{":
                # A JSON message
                data_map = json.loads(data)
                msg_type = data_map["message_type"]
                msg = data_map["message_body"]
                ordinal = data_map["ess_ordinal"]
                timestamp = data_map["ess_timestamp"]
            else:
                # A protocol buffer message
                sections = data.split("|")
                print(data)
                msg_type = sections[0]
                msg = sections[1]
                if len(sections) > 2:
                    ordinal = sections[2].split()[1]
                    timestamp = sections[3].split()[1]
                else:
                    ordinal = "(not present)"
                    timestamp = "(not present)"
            print("-----------")
            print(f"    ORDINAL: {ordinal}")
            print(f"  TIMESTAMP: {timestamp}")
            print(f"       TYPE: {msg_type}")
            print(f"       BODY: {msg}")
            print(f"TOTAL BYTES: {total_bytes}")
            print(f"  MIN BYTES: {min_bytes}")
            print(f"  MAX BYTES: {max_bytes}")
            print(f"  AVG BYTES: {int(total_bytes/total_messages + 0.5)}")
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
