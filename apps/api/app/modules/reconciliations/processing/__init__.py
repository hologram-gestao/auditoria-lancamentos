"""Subpacote de processamento async (S10 — BACK 8.1 a 8.6).

Camadas:
    - `omie_fetch`: busca lançamentos no Omie e converte em DTOs unificados.
    - `matcher`: função pura (sem I/O) que cruza arquivo x Omie.
    - `anomalies`: cria as anomalias estruturais (`missing_in_omie` /
      `missing_in_file`) na mesma transação do matching.
    - `dispatcher`: enfileira o job no Redis (chamado pelo endpoint).
    - `job`: orquestra fetch → match → anomalies → atualiza sessão. Ponto de
      entrada do worker ARQ.
"""
