"""DTOs internos do módulo de qualificação (S19 BACK 12.1).

NÃO expostos via API — anomalias resultantes saem pela tela de revisão
e pelo export Excel, ambos consumindo a tabela `reconciliation_anomalies`.

Convenção:
    - `pair_id`: string (UUID de `file_entry_id`). Permite serializar pro
      tool-use da Anthropic sem perder estabilidade do mapping de volta.
    - Valores monetários em `Decimal` (CLAUDE.md §3.4).
    - Texto descriptografado vive aqui em memória — caller cuida da
      criptografia ao persistir (via `decrypt`/`encrypt` em `core/crypto`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

SemanticStatus = Literal["ok", "suspeita", "incoerente"]


@dataclass(frozen=True, slots=True)
class QualificationPair:
    """Tupla `(descrição, fornecedor, categoria, valor)` de um par conciliado."""

    pair_id: str
    file_entry_id: UUID
    omie_lancamento_id: int
    description: str
    supplier: str | None
    category: str | None
    amount: Decimal


@dataclass(frozen=True, slots=True)
class SemanticResult:
    """Saída da Camada 1 (IA) para um único par."""

    pair_id: str
    status: SemanticStatus
    motivo: str


@dataclass(frozen=True, slots=True)
class HistoricalResult:
    """Saída da Camada 2 (padrão histórico) para um único par."""

    pair_id: str
    motivo: str


@dataclass(frozen=True, slots=True)
class OutlierResult:
    """Saída da Camada 3 (outlier de valor) para um único par."""

    pair_id: str
    motivo: str


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Contadores de tokens reportados pela Anthropic.

    `cached_input` é o `cache_read_input_tokens` quando o prompt caching
    bate (CLAUDE.md §7 / PLANO §6.2). Útil pra observabilidade de custo
    (S17). Pode vir 0 na 1ª chamada ou se o cache expirou.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0


@dataclass(frozen=True, slots=True)
class QualificationReport:
    """Sumário consumido pelo `job.py` (log estruturado).

    Não é persistido em tabela — derivamos do `reconciliation_anomalies`
    se precisar reconstruir. Os contadores aqui são "o que esta rodada
    do worker produziu" e servem pra cost-report + telemetria.
    """

    pairs_analyzed: int = 0
    coerentes: int = 0
    suspeitas: int = 0
    incoerentes: int = 0
    padrao_quebrado: int = 0
    valor_outlier: int = 0
    semantic_anthropic_calls: int = 0
    tokens: TokenUsage = field(default_factory=TokenUsage)
    skipped_reason: str | None = None

    def as_log_dict(self) -> dict[str, Any]:
        """Achata em dict pra `log.info(**report.as_log_dict())`.

        Mantém chaves snake_case e tipos primitivos pra ficar feliz com o
        structlog renderer (que recusa Decimal/UUID sem coerção).
        """
        return {
            "pairs_analyzed": self.pairs_analyzed,
            "coerentes": self.coerentes,
            "suspeitas": self.suspeitas,
            "incoerentes": self.incoerentes,
            "padrao_quebrado": self.padrao_quebrado,
            "valor_outlier": self.valor_outlier,
            "semantic_anthropic_calls": self.semantic_anthropic_calls,
            "input_tokens": self.tokens.input_tokens,
            "output_tokens": self.tokens.output_tokens,
            "cached_input_tokens": self.tokens.cached_input_tokens,
            "skipped_reason": self.skipped_reason,
        }
