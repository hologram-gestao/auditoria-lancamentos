"""Wrapper Windows-friendly para o ARQ worker.

Por que existe: o `psycopg` async exige `SelectorEventLoop`, mas o Python
default no Windows é `ProactorEventLoop`. O ARQ cria o event loop ao subir,
antes mesmo de `on_startup` rodar — então a única janela em que conseguimos
trocar a `event_loop_policy` é AQUI, no entry point.

Linux/macOS não precisam: `SelectorEventLoop` já é o default.

Uso (via `pnpm dev:worker` na raiz):
    cd apps/api && uv run python -m scripts.run_worker

Mesmo padrão de `scripts/seed_dev.py` e `scripts/seed_demo_client.py`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Garante que `apps/api/` está no sys.path (idem seed_dev.py / seed_demo_client.py).
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from arq.worker import run_worker  # noqa: E402  (policy precisa vir antes do import)

from app.workers.arq_worker import WorkerSettings  # noqa: E402

if __name__ == "__main__":
    run_worker(WorkerSettings)
