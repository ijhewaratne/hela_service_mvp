"""Pluggable SMS delivery (Q8). No real vendor is integrated until DEC-08 is decided."""
from __future__ import annotations

from typing import Protocol

from sqlalchemy import Connection, text


class SmsSender(Protocol):
    def send(self, con: Connection, *, phone_lookup: str, phone_e164: str, body: str) -> None: ...


class SandboxInboxSender:
    """Isolated local development only: writes to dev_sms_inbox, never to application logs."""

    def send(self, con: Connection, *, phone_lookup: str, phone_e164: str, body: str) -> None:
        con.execute(text('INSERT INTO dev_sms_inbox(phone_lookup, body) VALUES (:p, :b)'),
                    {'p': phone_lookup, 'b': body})


class UnconfiguredSender:
    """Used outside dev until an approved vendor exists: refuses to pretend a message was sent."""

    def send(self, con: Connection, *, phone_lookup: str, phone_e164: str, body: str) -> None:
        raise RuntimeError('No approved SMS provider is configured (DEC-08 pending)')
