"""Partida contábil de uma linha do de-para — funções PURAS (Sprint 16, BACK 16.3 — R3).

Toda linha do arquivo contábil é `data;débito;crédito;valor;histórico`, e UM dos lados é
sempre a conta do BANCO no plano do cliente. Este módulo é o ÚNICO lugar que decide:

    - `resolve_bank_code` — qual a conta do banco de uma linha, pela associação da conta
      de origem: explícita por conta → senão, SE a linha não tem conta de origem, a
      conta PADRÃO do tipo → senão, PENDENTE (`None`). Decisão do planejador
      (ADR-088-BE): conta de origem sem associação NUNCA cai na padrão em silêncio;
    - `derive_partida` — débito e crédito pelo SINAL do valor, nunca pelo texto: entrada
      (valor > 0) → débito no banco, crédito na conta decidida; saída (valor < 0) →
      débito na conta decidida, crédito no banco. É a função que a Sprint 13 reusa;
    - `is_partida_completa` — o predicado ÚNICO de "partida completa": alvo com conta do
      plano, conta do banco resolvida e histórico presente (a decisão LEGADA do catálogo
      não tem conta do plano, então é incompleta). A completude da 16.4 o reusa;
    - `pending_source_accounts` — as contas de origem das linhas com ALVO sem conta do
      banco (o 409 da materialização e a prévia listam estas).

Sem I/O, sem relógio, sem banco: testado direto contra as 32 linhas da amostra real.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.db.models import MaterializedSituation

#: `(tipo de origem, conta de origem)`; conta `None` = o slot da conta PADRÃO do tipo.
type BindingKey = tuple[str, str | None]


@dataclass(frozen=True, slots=True)
class Partida:
    """Os dois lados de uma linha, como CÓDIGOS REDUZIDOS do plano do cliente."""

    debit: str
    credit: str


class PartidaLine(Protocol):
    """O mínimo de uma linha (item da prévia OU snapshot materializado) para o predicado."""

    @property
    def source_type(self) -> str: ...
    @property
    def source_account_id(self) -> str | None: ...
    @property
    def situation(self) -> str: ...
    @property
    def accounting_account_code(self) -> str | None: ...
    @property
    def bank_account_code(self) -> str | None: ...
    @property
    def history_present(self) -> bool | None: ...


def resolve_bank_code(
    source_type: str, source_account_id: str | None, bindings: Mapping[BindingKey, str]
) -> str | None:
    """A conta do banco da linha — explícita → padrão (só sem conta de origem) → `None`."""
    if source_account_id is not None:
        return bindings.get((source_type, source_account_id))
    return bindings.get((source_type, None))


def derive_partida(amount: Decimal, decided_code: str, bank_code: str) -> Partida | None:
    """Débito e crédito pelo SINAL do valor ASSINADO. Valor zero não é lançamento: `None`."""
    if amount > 0:
        return Partida(debit=bank_code, credit=decided_code)
    if amount < 0:
        return Partida(debit=decided_code, credit=bank_code)
    return None


def is_partida_completa(line: PartidaLine) -> bool:
    """O predicado ÚNICO: a linha tem conta do plano, conta do banco e histórico."""
    return (
        _situation_value(line.situation) == MaterializedSituation.ALVO.value
        and line.accounting_account_code is not None
        and line.bank_account_code is not None
        and bool(line.history_present)
    )


def pending_source_accounts(lines: Iterable[PartidaLine]) -> tuple[BindingKey, ...]:
    """As contas de origem (só identificadores) das linhas com ALVO sem conta do banco.

    Linhas `nao_mapear`, sem decisão ou sem categoria não vão para o arquivo e não
    bloqueiam (decisão do planejador, ADR-088-BE). Ordem total e estável: `None` (a
    padrão) antes das contas, por tipo de origem.
    """
    pending = {
        (line.source_type, line.source_account_id)
        for line in lines
        if _situation_value(line.situation) == MaterializedSituation.ALVO.value
        and line.bank_account_code is None
    }
    return tuple(sorted(pending, key=lambda key: (key[0], key[1] is not None, key[1] or "")))


def _situation_value(situation: object) -> str:
    """Aceita o enum (item da prévia) e a string (snapshot do banco)."""
    return str(getattr(situation, "value", situation))
