#!/usr/bin/env python
"""A simple RabbitMQ message publisher for local testing.
Takes a 'routing key' and sends a message to the 'expected' AS exchange
on a localhost RabbitMQ server.

Usage: simple_es_publisher.py <routing_key>
"""
import asyncio
import sys
from datetime import datetime, timezone

import aio_pika
from google.protobuf import text_format
from informaticsmatters.protobuf.accountserver.merchant_charge_message_pb2 import (
    MerchantChargeMessage,
    OperationEnum,
)

if len(sys.argv) != 2:
    print("Usage: simple_es_publisher.py <routing-key>")
    sys.exit(1)

_ROUTING_KEY: str = sys.argv[1]

_AMPQ_EXCHANGE: str = "event-streams"
_AMPQ_URL: str = "amqp://es:cheddar1963@localhost:5672"

# A demonstration message
_MESSAGE_CLASS: str = "accountserver.MerchantCharge"
_MESSAGE: MerchantChargeMessage = MerchantChargeMessage()
_MESSAGE.timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
_MESSAGE.merchant_kind = "DATA_MANAGER"
_MESSAGE.merchant_name = "squonk"
_MESSAGE.merchant_id = 1
_MESSAGE.operation = OperationEnum.OPERATION_ENUM_PROCESSING
_MESSAGE.auth_code = 456782
_MESSAGE.value = "0.50"
_MESSAGE.sqn = 1
_MESSAGE_STRING: str = text_format.MessageToString(_MESSAGE, as_one_line=True)


async def main() -> None:
    """Publish a message."""
    connection = await aio_pika.connect_robust(_AMPQ_URL)
    async with connection:
        channel = await connection.channel()

        es_exchange = await channel.declare_exchange(
            _AMPQ_EXCHANGE,
            aio_pika.ExchangeType.DIRECT,
        )
        await es_exchange.publish(
            aio_pika.Message(body=f"{_MESSAGE_CLASS}|{_MESSAGE_STRING}".encode()),
            routing_key=_ROUTING_KEY,
        )


if __name__ == "__main__":
    asyncio.run(main())
