"""A natureza da conta derivada da classificação (86e3qhhrr).

A planilha de importação do plano de contas da Omie exige a "Natureza da Operação"
(`Devedora`/`Credora`) em toda conta, e o plano importado do Domínio não a traz. A
regra é pelo GRUPO da conta, o primeiro dígito significativo da classificação:

- `1` (ativo) → `devedora`;
- `2` (passivo) e `3` (patrimônio líquido e receitas) → `credora`;
- `4` em diante (custos, despesas e o resto) → `devedora`.

⚠️ A descrição da task dizia "2, 3 e 4 = Credora". A amostra preenchida pelo Gabriel
(o modelo da Omie com um cliente real, 19/10/2026) mostrou o contrário no grupo 4:
das 496 contas com classificação, as 155 do grupo 4 (custos e despesas) são
`Devedora`. Com a regra acima, 489 batem e só 7 divergem (contas redutoras da receita
no grupo 3, `Devedora` na amostra), que é o que a edição à mão cobre.

Função PURA, sem I/O. A migration `5d8e2b7c4a91` faz o backfill com a MESMA regra em
SQL (`substring(classification from '^\\s*0*([1-9])')`), e o teste de integração
prova a paridade.
"""

from __future__ import annotations

import re

from app.db.models.client_accounting_account import AccountingAccountNature

#: O primeiro dígito significativo do primeiro segmento: zeros à esquerda pulados,
#: qualquer outro caractere encerra a busca (`0.1` não tem grupo).
_GROUP = re.compile(r"\s*0*([1-9])")

#: Grupo → natureza. O que não está aqui (4 a 9) é `devedora`.
_CREDIT_GROUPS = frozenset({"2", "3"})


def derive_nature(classification: str | None) -> AccountingAccountNature | None:
    """A natureza pela classificação, ou `None` quando ela não diz o grupo.

    `None` é conta sem classificação (ou com uma que não começa por dígito): a
    planilha da Omie recusa a conta até alguém escolher a natureza à mão.
    """
    if not classification:
        return None
    match = _GROUP.match(classification)
    if match is None:
        return None
    if match.group(1) in _CREDIT_GROUPS:
        return AccountingAccountNature.CREDORA
    return AccountingAccountNature.DEVEDORA


def resolve_nature(
    *,
    classification: str | None,
    current: str | None,
    overridden: bool,
) -> str | None:
    """A natureza que a conta deve ter depois de uma gravação.

    A escolhida à mão (`overridden`) fica como está; a automática acompanha a
    classificação. Valor do enum, como a coluna guarda.
    """
    if overridden:
        return current
    derived = derive_nature(classification)
    return derived.value if derived is not None else None
