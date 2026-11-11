"""Escape do mrkdwn do Slack, compartilhado pelos avisos que saem por webhook.

O Slack interpreta `&`, `<` e `>` em texto mrkdwn: `<url|texto>` vira link e
`<!channel>` vira menção. Todo texto que entra num payload de incoming webhook
passa por `escape_mrkdwn` antes. Nasceu no aviso de lead (86e3fr9ut) e subiu para o
core quando o alerta de plantão passou a montar texto com contexto (86e3mpmcw).
"""

from __future__ import annotations


def escape_mrkdwn(value: str) -> str:
    """Escapa os três caracteres de controle do mrkdwn do Slack (`&` primeiro)."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
