"""Chave de ordenação do plano contábil — a ORDEM DA CLASSIFICAÇÃO (86e3n70p9).

A lista do plano saía na ordem do código reduzido tratado como TEXTO (`1, 10, 101,
11`). Para o contador a ordem é a da classificação, que é a estrutura do plano: a
sintética em cima e as analíticas dela logo abaixo (`1`, `1.1`, `1.1.1`,
`1.1.1.02`, `1.1.1.02.001`…). É essa ordem que faz o lugar de uma conta nova ser
achável (a inclusão manual, subtask 9, reusa esta função).

**A classificação ordena como TEXTO, sem preenchimento — é a ordem do Domínio**
(decisão do Pedro, 09/10/2026, medida nas duas amostras reais). As duas fixtures
anonimizadas trazem o plano exatamente como o Domínio o lista, e nas duas a ordem do
arquivo é a ordem de texto da classificação:

- `tests/fixtures/accounting_chart_dominio/` (o plano do Gabriel, 563 contas): sob
  `1.1.2.01` convivem DUAS famílias, `00001, 00002…` e `001, 002…`. Para o Domínio
  são grupos distintos, um depois do outro. Preencher os segmentos com zeros (a
  primeira versão desta função) as intercalava (`00001, 001, 00002, 002…`) e punha
  87 contas fora do lugar;
- `tests/fixtures/accounting_chart_dominio_xls/` (o plano do Murilo, 370 contas):
  largura fixa por nível, então texto e número coincidem.

O preço é conhecido e documentado no modelo da planilha: quem monta a planilha à mão
usa largura fixa por nível (`01`, `02`… `10`), senão `1.1.10` vem antes de `1.1.2`,
como viria no próprio Domínio. Uma regra "mais inteligente" que reinterprete os
números quebra o plano real: `tests/integration/test_accounting_chart_endpoints.py`
importa as duas fixtures e exige a ordem do arquivo.

**Sem classificação, o código reduzido é a fonte, e ELE é preenchido**: cada segmento
só de dígitos ASCII ganha zeros à esquerda até `SORT_SEGMENT_WIDTH` (`10` →
`000010`), para a conta sem classificação não cair em `1, 10, 101, 11`; segmento mais
largo que isso fica como está (nunca truncado) e segmento com letra vai em
minúsculas. Dígito aqui é só `0-9`: `str.isdigit` aceita `²` e dígitos de outros
alfabetos, e o `lpad` do Postgres não — as duas fontes têm de concordar.

**A comparação é de BYTE, nunca de idioma.** A coluna `sort_key` é `COLLATE "C"`
(modelo e migration). Com a collation do sistema operacional (`en_US.utf8` da glibc,
a do Postgres em Debian), o Postgres IGNORA a pontuação na comparação e ordena
`11 < 1.10 < 1.1.10 < 1.1.1.02.001 < 1.1.2` — medido em 09/10/2026. O alpine do CI
(musl) compara por byte e não mostraria o defeito. Em `C`, a ordem do `ORDER BY` é a
do `sorted()` do Python sobre estas chaves.

**A chave é DERIVADA na gravação, nunca inferida na hora da leitura**: a coluna é
calculada por `chart_sort_key` em toda inserção e atualização (importação pelo modelo
e pelo export do Domínio), e a listagem só faz `ORDER BY sort_key NULLS LAST, code,
id`. A migration `b2f7c9e41d06` REPETE a regra em SQL para o backfill (ela não
importa `app.*`; `tests/integration/test_migrations.py` prova que as duas fontes
produzem o mesmo valor para a mesma amostra).
"""

from __future__ import annotations

import re

#: Largura de cada segmento numérico do CÓDIGO REDUZIDO depois do preenchimento (só
#: a conta sem classificação usa). Segmento mais largo fica como está.
SORT_SEGMENT_WIDTH = 6

#: O separador de segmento do código reduzido.
SORT_SEGMENT_SEPARATOR = "."

_ASCII_DIGITS = re.compile(r"[0-9]+")


def _code_segment(segment: str) -> str:
    if _ASCII_DIGITS.fullmatch(segment):
        return segment.zfill(SORT_SEGMENT_WIDTH)
    return segment.lower()


def chart_sort_key(classification: str | None, code: str) -> str:
    """A chave de ordenação de UMA conta — função PURA, a fonte única da regra.

    `classification` é a classificação hierárquica como a planilha trouxe (ou
    `None`); `code` é o código reduzido. Quem grava conta (importação, e a inclusão
    manual quando existir) chama isto e persiste o resultado em `sort_key`.

    Com classificação, a chave é a PRÓPRIA classificação (texto, como no Domínio):

    >>> chart_sort_key("1.1.2.01.00001", "773")
    '1.1.2.01.00001'
    >>> sorted(chart_sort_key(c, "x") for c in ["1.1.2.01.001", "1.1.2.01.00002", "1.1.2.01.00001"])
    ['1.1.2.01.00001', '1.1.2.01.00002', '1.1.2.01.001']

    Sem classificação, o código reduzido com os segmentos numéricos preenchidos:

    >>> chart_sort_key(None, "10")
    '000010'
    >>> chart_sort_key("", "AB-12")
    'ab-12'
    """
    if classification:
        return classification
    return SORT_SEGMENT_SEPARATOR.join(
        _code_segment(segment) for segment in code.split(SORT_SEGMENT_SEPARATOR)
    )
