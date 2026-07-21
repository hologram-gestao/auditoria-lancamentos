#!/usr/bin/env python3
"""Smoke pós-deploy: prova a ENTREGA do alerta (Sprint 3, Req. 4 / INFRA 03.7).

Roda no runner do CI (NÃO na imagem): faz login como admin de MONITORAÇÃO e
dispara o gatilho SINTÉTICO da 03.6 (`POST /api/v1/system/alert-test`) contra o
serviço da API JÁ deployado e configurado. Sai != 0 se a notificação não chegou
ao(s) canal(is) — "alerta configurado" não conta sem entrega provada.

Por que o endpoint (e não um Cloud Run Job): a 03.6 entregou o gatilho sintético
como endpoint HTTP admin-only (chama `send_alert(SYNTHETIC)` no serviço que já
tem TODA a config — DATABASE_URL, chaves de cripto, canais). Um Job novo não
sobe o app sem esse conjunto de secrets, e não há módulo runnable no repo. Bater
no endpoint do serviço vivo exercita o caminho REAL de dispatch da 03.6.

Só stdlib (urllib) — sem dependência e SEM import da app. Nunca imprime senha,
cookie nem PII: o corpo da resposta é só `{delivered, webhook, email}`
(booleans/null), por design da 03.6.

Env:
  API_BASE_URL          base pública da API (ex.: https://auditoria-api-dev-xxx.run.app)
  SMOKE_ADMIN_EMAIL     admin dedicado de monitoração (perfil admin; ver runbook)
  SMOKE_ADMIN_PASSWORD  senha do admin de monitoração (secret do GitHub Environment)
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request
from typing import NoReturn

_TIMEOUT_S = 30


def _fail(msg: str) -> NoReturn:
    print(f"::error::{msg}")
    sys.exit(1)


def main() -> None:
    base = (os.environ.get("API_BASE_URL") or "").strip().rstrip("/")
    email = (os.environ.get("SMOKE_ADMIN_EMAIL") or "").strip()
    password = os.environ.get("SMOKE_ADMIN_PASSWORD") or ""
    if not base:
        _fail("API_BASE_URL vazio (vars.API_URL_* não definida no environment).")
    if not email or not password:
        _fail(
            "SMOKE_ADMIN_EMAIL/SMOKE_ADMIN_PASSWORD ausentes — crie o admin de "
            "monitoração e configure os secrets (ver scripts/environments-runbook.md)."
        )

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    # 1) Login → cookie HttpOnly `access_token` fica no cookie jar.
    login_body = json.dumps({"email": email, "password": password}).encode()
    login_req = urllib.request.Request(
        f"{base}/api/v1/auth/login",
        data=login_body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        opener.open(login_req, timeout=_TIMEOUT_S).read()
    except urllib.error.HTTPError as exc:
        _fail(
            f"login do admin de monitoração falhou (HTTP {exc.code}). "
            "Admin existe? Senha correta? (ver runbook)"
        )
    except urllib.error.URLError as exc:
        _fail(f"login inacessível ({exc.reason}). API_BASE_URL correto? Serviço no ar?")

    if not any(cookie.name == "access_token" for cookie in jar):
        _fail("login não devolveu cookie access_token.")

    # 2) Gatilho SINTÉTICO da 03.6 (admin-only) → send_alert(SYNTHETIC) ao canal.
    trigger_req = urllib.request.Request(f"{base}/api/v1/system/alert-test", method="POST")
    try:
        raw = opener.open(trigger_req, timeout=_TIMEOUT_S).read().decode()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        _fail(f"POST /system/alert-test falhou (HTTP {exc.code}): {detail}")
    except urllib.error.URLError as exc:
        _fail(f"POST /system/alert-test inacessível ({exc.reason}).")

    print(f"Resposta do gatilho (sem PII): {raw}")
    try:
        data = json.loads(raw)["data"]
    except (json.JSONDecodeError, KeyError, TypeError):
        _fail("resposta do endpoint fora do contrato {data:{delivered,...}}.")

    # 3) Assert ENTREGA: pelo menos um canal entregou de verdade.
    if data.get("delivered") is not True:
        _fail(
            f"alerta NÃO entregue (delivered={data.get('delivered')}, "
            f"webhook={data.get('webhook')}, email={data.get('email')}). "
            "Canal quebrado ou vazio — 'alerta configurado' não conta sem entrega."
        )

    print(
        "✓ Alerta sintético ENTREGUE ao canal de plantão configurado "
        f"(webhook={data.get('webhook')}, email={data.get('email')})."
    )


if __name__ == "__main__":
    main()
