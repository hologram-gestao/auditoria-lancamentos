"""Instrumentação de outcome da Sprint 4 (BACK 04.1).

Sink mínimo (`usage_events`) + emissores de backend + endpoint para os eventos
que só o frontend consegue observar. Ver `app.db.models.usage_event`.
"""

from app.modules.usage_events.schemas import UsageEventName
from app.modules.usage_events.service import UsageEventService

__all__ = ["UsageEventName", "UsageEventService"]
