"""Entrypoints de CLI executáveis via `python -m app.cli.<nome>`.

Runnables operacionais que o deploy/pipeline executa como Cloud Run Jobs — fora
do ciclo de request HTTP (não têm JWT). Reusam a lógica de `app/core` e `app/modules`.
"""
