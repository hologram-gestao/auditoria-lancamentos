"""Subpacote de processamento async (S10 — BACK 8.1 a 8.6).

Camadas:
    - `omie_fetch`: busca lançamentos no Omie e converte em DTOs unificados.
    - `matcher`: função pura (sem I/O) que cruza arquivo x Omie.
    - `anomalies`: cria as anomalias estruturais (`missing_in_omie` /
      `missing_in_file`) na mesma transação do matching.
    - `job`: orquestra fetch → match → anomalies → atualiza sessão. Agendado
      como FastAPI BackgroundTask pelo endpoint (sem broker — FASE 0).
"""
