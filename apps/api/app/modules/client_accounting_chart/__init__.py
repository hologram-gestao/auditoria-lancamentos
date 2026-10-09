"""Plano de contas CONTÁBIL do cliente — o do sistema contábil de destino (Sprint 16).

`chart_sort_key` é a função ÚNICA da ordem da classificação (86e3n70p9): quem gravar
conta por outro caminho (a inclusão manual, subtask 9) a chama e persiste `sort_key`.
"""

from app.modules.client_accounting_chart.sort_key import chart_sort_key

__all__ = ["chart_sort_key"]
