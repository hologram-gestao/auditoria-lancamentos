"""Gera `docs/endpoints-sensiveis-sprint5.md` a partir da lista canônica.

A página legível é **derivada**, nunca escrita à mão: fonte única é
`app.core.sensitive_endpoints`. Rode depois de mexer na lista:

    uv run python -m scripts.gen_sensitive_endpoints_doc

Sem argumentos e sem I/O de rede — escreve o arquivo e imprime o placar.
"""

from __future__ import annotations

from pathlib import Path

from app.core.sensitive_endpoints import (
    NON_TENANT_ENDPOINTS,
    PENDING_ENDPOINTS,
    SENSITIVE_ENDPOINTS,
    ScopeKind,
)

OUTPUT = Path(__file__).resolve().parents[1] / "docs" / "endpoints-sensiveis-sprint5.md"

_HEADER = """# Endpoints sensíveis a tenant — Sprint 5 (R3 / BACK 05.4)

> **Artefato versionado da sprint.** É o **denominador fechado** da métrica
> "endpoints sensíveis com caso negativo cross-tenant testado e passando ÷ total".
> Sem esta lista, "100%" seria um número sobre um conjunto arbitrário.
>
> **Fonte única:** `apps/api/app/core/sensitive_endpoints.py`. Esta página é
> gerada por `scripts/gen_sensitive_endpoints_doc.py` — não edite aqui, edite
> lá. O `tests/integration/test_sensitive_endpoints.py` falha se a lista
> divergir das rotas reais **ou** se uma rota nova com
> `{client_id}`/`{session_id}` não for classificada.
"""

_COMO_MEDE = """
## Como a cobertura é medida

`tests/integration/test_sensitive_endpoints.py` parametriza **toda** a lista e,
para cada endpoint, faz um operador do tenant A disparar a rota contra recursos
do tenant B. Critérios de cada caso:

- nunca `2xx` (para detalhe/PK e coleções endereçadas por `client_id`);
- coleções globais (notificações) respondem `200` com **zero** linhas de B;
- o corpo **nunca** contém dado de B (o teste procura a razão social do alvo);
- o body enviado é **válido** de propósito — um `422` de validação passaria sem
  nunca chegar na autorização, e a cobertura seria falsa.
"""


def render() -> str:
    total = len(SENSITIVE_ENDPOINTS)
    pendentes = len(PENDING_ENDPOINTS)
    cobertos = total - pendentes
    linhas = [_HEADER]

    linhas.append("\n## Placar\n")
    linhas.append("| | |")
    linhas.append("| --- | --- |")
    linhas.append(f"| Endpoints sensíveis (denominador) | **{total}** |")
    linhas.append(f"| Com caso negativo cross-tenant verde | **{cobertos}** |")
    linhas.append(f"| Pendentes (implementação em outra task) | **{pendentes}** |")
    pct = cobertos * 100 // total if total else 0
    linhas.append(f"| Cobertura | **{cobertos}/{total} = {pct}%** |")

    if pendentes:
        linhas.append(
            "\nOs pendentes já estão no denominador porque a base da métrica é "
            "fechada na abertura da sprint (mudá-la no meio invalidaria a "
            "comparação). Esvaziar `PENDING_ENDPOINTS` é parte do DoD.\n"
        )

    linhas.append("\n## Lista canônica\n")
    linhas.append(
        "Legenda de `tipo`: **coleção** = vaza forjando `client_id` na "
        "URL/payload · **detalhe (PK)** = vaza pela PK do recurso, **sem** "
        "`client_id` na requisição (o mais fácil de esquecer).\n"
    )
    linhas.append("| Método | Path | Tipo | Módulo | Como o tenant é imposto | Status |")
    linhas.append("| --- | --- | --- | --- | --- | --- |")
    for endpoint in SENSITIVE_ENDPOINTS:
        tipo = "coleção" if endpoint.kind is ScopeKind.COLLECTION else "detalhe (PK)"
        status = (
            f"⏳ {PENDING_ENDPOINTS[endpoint.key]}"
            if endpoint.key in PENDING_ENDPOINTS
            else "✅ verde"
        )
        linhas.append(
            f"| `{endpoint.method}` | `{endpoint.path}` | {tipo} | "
            f"`{endpoint.module}` | {endpoint.mechanism} | {status} |"
        )

    linhas.append("\n## Rotas `/api/v1` fora do denominador\n")
    linhas.append(
        "Não carregam dado escopável a um cliente. Registradas explicitamente "
        'para que o teste de completude possa afirmar "toda rota está '
        'classificada" — rota nova cai fora das duas listas e o CI falha, em '
        "vez de passar por omissão.\n"
    )
    linhas.append("| Rota | Por que não é sensível a tenant |")
    linhas.append("| --- | --- |")
    for key, motivo in sorted(NON_TENANT_ENDPOINTS.items()):
        linhas.append(f"| `{key}` | {motivo} |")

    linhas.append(_COMO_MEDE)
    return "\n".join(linhas) + "\n"


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(), encoding="utf-8")
    total = len(SENSITIVE_ENDPOINTS)
    cobertos = total - len(PENDING_ENDPOINTS)
    print(f"{OUTPUT.relative_to(OUTPUT.parents[1])}: {cobertos}/{total} endpoints cobertos")


if __name__ == "__main__":
    main()
