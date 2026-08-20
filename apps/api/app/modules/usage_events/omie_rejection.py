"""`faultstring` → família categórica, sem levar o texto junto (BACK 07.5).

**O conflito que este módulo resolve.** O PRD declara
`omie_lancamento_rejeitado {codigo, faultstring}`. A whitelist de `props` do
sink proíbe texto livre (`extra="forbid"`, todo campo `int`/`Literal`/UUID) — e
essa proibição é a única coisa que impede PII de entrar na tabela de métrica. O
`faultstring` é texto do fornecedor e a Omie **ecoa valores que enviamos**,
inclusive o `cObs`, que carrega a descrição da compra (CLAUDE.md §4.5).

**A saída não é enfraquecer a whitelist nem descartar a informação.** O texto
integral segue existindo onde é útil e está protegido: volta ao usuário na
resposta do lote e fica em `reconciliation_omie_postings.error_message`
(ADR-023-BE). Aqui entra só a **família**, que é o que a leitura D+30 precisa
para responder "por que os lançamentos estão sendo recusados?" — se a resposta
for "categoria inválida" em 80% dos casos, o problema é o passo de
classificação, não o lançamento.

**Nenhuma palavra-chave abaixo é PII** e nenhum trecho do `faultstring` é
retornado: a função devolve um dos valores fechados de `OmieRejectionCategory`.
Casos não reconhecidos viram `"outro"` — e "outro" crescendo é o sinal de que
falta uma família, não um convite a gravar o texto.
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.usage_events.schemas import OmieRejectionCategory

#: (família, palavras que a identificam). Ordem IMPORTA: a primeira família
#: cujo termo aparecer vence. `duplicidade` vem antes de tudo porque é a única
#: cuja leitura muda uma decisão de produto — é o guardrail "zero lançamento
#: duplicado" falando.
_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("duplicidade", ("duplic", "ja existe", "ja cadastrad", "codigo de integracao")),
    ("credencial", ("app_key", "app key", "app_secret", "credencia", "acesso negado")),
    ("categoria_invalida", ("categoria",)),
    ("conta_invalida", ("conta corrente", "ncodcc", "conta nao", "conta invalid")),
    ("campo_invalido", ("tag ", "campo", "obrigator", "invalid", "nao faz parte")),
    ("indisponibilidade", ("timeout", "indisponi", "tente novamente", "consumo redundante")),
)


def _normalize(text: str) -> str:
    """Minúsculas sem acento — a Omie mistura acentuação entre mensagens."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def classify_omie_rejection(fault_message: str | None) -> OmieRejectionCategory:
    """Família do erro do fornecedor. **Nunca devolve trecho do texto.**

    Args:
        fault_message: o `faultstring` (ou `user_message` derivado dele). Pode
            ser `None`/vazio — a Omie já devolveu erro sem mensagem.

    Returns:
        Um dos valores de `OmieRejectionCategory`; `"outro"` quando nenhuma
        família reconhece.
    """
    if not fault_message:
        return "outro"
    normalized = _normalize(fault_message)
    for category, needles in _RULES:
        if any(needle in normalized for needle in needles):
            return category  # type: ignore[return-value]
    return "outro"
