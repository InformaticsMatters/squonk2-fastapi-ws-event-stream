#!/usr/bin/env python
"""A simple RabbitMQ message publisher for local testing.
Takes a 'routing key' and sends a message to the 'expected' AS exchange
on a localhost RabbitMQ server.

Usage: simple_message_publisher.py <routing_key>
"""
import asyncio
import sys

import aio_pika

_ROUTING_KEY: str = sys.argv[1]

_AMPQ_EXCHANGE: str = "event-streams"


async def main() -> None:
    """Publish a message."""
    connection = await aio_pika.connect_robust(
        "amqp://es:cheddar1963@localhost:5672",
    )

    async with connection:
        channel = await connection.channel()

        es_exchange = await channel.declare_exchange(
            _AMPQ_EXCHANGE,
            aio_pika.ExchangeType.DIRECT,
        )
        await es_exchange.publish(
            aio_pika.Message(body=f"Hello {_ROUTING_KEY}".encode()),
            routing_key=_ROUTING_KEY,
        )


if __name__ == "__main__":
    asyncio.run(main())
