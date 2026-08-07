"""Emissores tipados de eventos de instrumentação (Sprint 3).

Baseline do ## Outcome & verificação da Sprint 3: **"0 de 3 — hoje nada é
registrado nem notificado"**. Este módulo cria o número de partida que o D+30
(`prd/hologram/ADL/verificacao-sprint-3.md`) vai ler, emitindo eventos
estruturados **countáveis** via structlog + o redactor já existente
(`app/core/logging.py`). Sem dependência nova de telemetria — estende o
logging estruturado que já existe.

Regra inviolável (CLAUDE.md §3): **SEM PII — só IDs.** Nenhum campo além dos
declarados na assinatura de cada emissor. O redactor do structlog mascara
qualquer chave sensível que porventura apareça, mas a própria assinatura destes
emissores só aceita IDs/contadores — nome, razão social, descrição de
lançamento e afins nunca entram.

Métrica que o D+30 conta (contador/métrica exigido pela task):
    - `event="acesso_negado"`   → tentativas de acesso fora da carteira.
    - `event="chave_rotacionada"` → rotações/backfill de chave concluídos.
Cada ocorrência é uma linha de log estruturada contável no Loki/Grafana; os
nomes canônicos são as constantes `EVENT_*` abaixo (nunca strings mágicas).
"""

from __future__ import annotations

from app.core.logging import get_logger

# Nomes canônicos dos eventos — o D+30 conta ocorrências por estes valores.
# Centralizados aqui; consumidores (dependencies, script de rotação, alerting)
# importam a constante, nunca a string crua.
EVENT_ACESSO_NEGADO = "acesso_negado"
EVENT_CHAVE_ROTACIONADA = "chave_rotacionada"
# Sprint 5 (R6): negação de acesso a tenant alheio, com o escopo/tenant do ATOR.
EVENT_ACESSO_CROSS_TENANT_NEGADO = "acesso_cross_tenant_negado"

_log = get_logger("app.telemetry")


def emit_acesso_negado(*, user_id: str, client_id_alvo: str, rota: str) -> None:
    """Emite o evento `acesso_negado` — tentativa de acesso fora da carteira.

    Chamado no ponto onde `ClientNotAccessibleError` é levantado
    (`app/core/dependencies.py`), **ANTES** da conversão 403→404
    (anti-enumeração). A conversão para 404 permanece intacta: o 404 protege o
    atacante de aprender, e o evento protege a equipe de não saber. As duas
    coisas convivem (CONTEXT.md — Regras e decisões).

    Args:
        user_id: id do usuário autenticado que tentou o acesso.
        client_id_alvo: id do cliente-alvo fora da carteira.
        rota: caminho da request (ex.: `/api/v1/clients/{id}`).

    Somente IDs — nunca nome, razão social ou qualquer PII.
    """
    _log.warning(
        EVENT_ACESSO_NEGADO,
        user_id=user_id,
        client_id_alvo=client_id_alvo,
        rota=rota,
    )


def emit_chave_rotacionada(*, clientes_afetados: int, duracao_s: float) -> None:
    """Emite o evento `chave_rotacionada` — rotação/backfill de chave concluído.

    Contrato de propriedades fixado aqui para a task 03.4 (script de rotação)
    chamar. O script ainda não existe nesta task; expomos só o helper + o
    contrato dos campos.

    Args:
        clientes_afetados: quantos clientes tiveram linhas re-cifradas.
        duracao_s: duração total da rotação, em segundos.

    Somente contadores — nunca DEK, ciphertext, `key_id` ou qualquer segredo.
    """
    _log.info(
        EVENT_CHAVE_ROTACIONADA,
        clientes_afetados=clientes_afetados,
        duracao_s=duracao_s,
    )


def emit_acesso_cross_tenant_negado(
    *,
    user_scope: str,
    tenant_ator: str | None,
    tenant_alvo: str,
    rota: str,
) -> None:
    """Emite `acesso_cross_tenant_negado` — acesso negado a tenant alheio (S5/R6).

    Contrato de propriedades fixado pelo PRD da Sprint 5, EXATAMENTE estas quatro:
    `user_scope`, `tenant_ator`, `tenant_alvo`, `rota`. A leitura do D+30
    conta ocorrências por este `event`.

    Convive com `acesso_negado` (S3) em vez de substituí-lo: o D+30 da Sprint 3
    ainda conta aquele evento, e removê-lo zeraria a métrica anterior. Um é
    "negou acesso a um cliente", o outro acrescenta a dimensão de TENANT DO ATOR.

    O nome do campo é `tenant_ator`, não `tenant_do_token`: o valor é o ID do
    tenant do ator, não uma credencial. O nome antigo terminava em `_token` e o
    redactor do logging o mascarava como se fosse segredo — o campo saiu
    `[REDACTED]` em 100% das emissões desde a Sprint 5, deixando a dimensão que
    a S5/R6 acrescentou sem chegar ao log. Não renomear de volta.

    Args:
        user_scope: escopo do ator (`system`|`client`).
        tenant_ator: tenant do ator — `None` para usuário `system`
            (equipe Hologram não pertence a tenant nenhum).
        tenant_alvo: id do cliente/tenant cujo dado foi pedido.
        rota: caminho da request (sem query string).

    Somente IDs e enums — nunca nome, razão social, e-mail ou CNPJ.
    """
    _log.warning(
        EVENT_ACESSO_CROSS_TENANT_NEGADO,
        user_scope=user_scope,
        tenant_ator=tenant_ator,
        tenant_alvo=tenant_alvo,
        rota=rota,
    )
