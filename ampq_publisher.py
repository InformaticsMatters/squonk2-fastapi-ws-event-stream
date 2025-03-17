#!/usr/bin/env python
"""A simple RabbitMQ message publisher for local testing.

Takes a 'routing key' and sends a built-in message to the 'event-streams' AS exchange
on a RabbitMQ server. The AMPQ URL is specified below and is expected
to match the ESS_AMPQ_URL value used in the project's docker-compose file.
It can be changed by providing a second argument on the command line.

Usage: ampq_publisher.py <routing_key> [<ampq_url>]
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

# Deal with command line arguments
if len(sys.argv) not in (2, 3):
    print("Usage: ampq_publisher.py <routing-key> [<ampq-url>]")
    sys.exit(1)

_ROUTING_KEY: str = sys.argv[1]
if len(sys.argv) > 2:
    _AMPQ_URL: str = sys.argv[2]
else:
    _AMPQ_URL: str = "amqp://es:cheddar1963@localhost:5672"

# The AMPQ exchange the AS published to.
_AMPQ_EXCHANGE: str = "event-streams"

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
    body_str: str = f"{_MESSAGE_CLASS}|{_MESSAGE_STRING}"
    print(f"Sending {body_str} (to {_AMPQ_URL})...")

    connection = await aio_pika.connect_robust(_AMPQ_URL)
    async with connection:
        channel = await connection.channel()

        es_exchange = await channel.declare_exchange(
            _AMPQ_EXCHANGE,
            aio_pika.ExchangeType.DIRECT,
            durable=True,
        )
        await es_exchange.publish(
            aio_pika.Message(body_str.encode()),
            routing_key=_ROUTING_KEY,
        )


if __name__ == "__main__":
    asyncio.run(main())
