"""Process-wide dependencies, built once in create_app and reachable from request handlers."""
from __future__ import annotations

from dataclasses import dataclass

import redis
from fastapi import Request
from sqlalchemy import Engine

from ..identity.sms import SmsSender
from ..security.crypto import FieldCipher
from .config import Settings


@dataclass
class Services:
    settings: Settings
    engine: Engine
    cipher: FieldCipher
    redis: redis.Redis
    sms: SmsSender


def services(request: Request) -> Services:
    return request.app.state.services
