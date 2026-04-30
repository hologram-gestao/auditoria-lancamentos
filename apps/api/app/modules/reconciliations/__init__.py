"""Módulo de conciliações — sessões + entries + anomalias.

Estado atual (S8 — BACK 6.2):
    - GET /api/v1/reconciliations/check-duplicate (verificação de idempotência)

Sessões posteriores ampliam o módulo com criação assíncrona, listagem de
entries, revisão e exportação Excel.
"""
