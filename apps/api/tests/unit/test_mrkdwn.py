"""`escape_mrkdwn` no core (86e3mpmcw): o aviso de lead e o alerta de plantão usam o mesmo."""

from __future__ import annotations

from app.core.mrkdwn import escape_mrkdwn
from app.modules.leads import notifier


def test_escapa_os_tres_caracteres_com_o_e_comercial_primeiro() -> None:
    assert escape_mrkdwn("a & b <!channel> <https://x|y>") == (
        "a &amp; b &lt;!channel&gt; &lt;https://x|y&gt;"
    )
    # `&` primeiro: o `&lt;` gerado não vira `&amp;lt;`.
    assert escape_mrkdwn("<") == "&lt;"


def test_o_aviso_de_lead_usa_o_do_core() -> None:
    assert notifier.escape_mrkdwn is escape_mrkdwn
