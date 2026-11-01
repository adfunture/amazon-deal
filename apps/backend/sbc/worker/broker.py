"""Dramatiq-Broker. Redis in Betrieb, StubBroker in Tests/ohne REDIS_URL."""

from __future__ import annotations

import dramatiq
from dramatiq.brokers.stub import StubBroker

from sbc.config import get_settings


def _make_broker() -> dramatiq.Broker:
    url = get_settings().redis_url
    if url and get_settings().app_env != "test":
        from dramatiq.brokers.redis import RedisBroker

        # Fehlgeschlagene Nachrichten landen nach max_retries in der Dead-Letter-Queue (<queue>.XQ)
        return RedisBroker(url=url, dead_message_ttl=14 * 24 * 3600 * 1000)
    return StubBroker()


broker = _make_broker()
dramatiq.set_broker(broker)
