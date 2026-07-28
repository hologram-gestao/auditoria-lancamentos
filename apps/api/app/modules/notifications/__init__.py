"""Notificações in-app de fim de conciliação (Sprint 4, BACK 04.4).

Ver `app.db.models.notification` para o modelo e os guardrails de PII.
"""

from app.modules.notifications.service import NotificationService

__all__ = ["NotificationService"]
