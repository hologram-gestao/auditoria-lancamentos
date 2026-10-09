"""Chave de ordenação do plano contábil — a ORDEM DA CLASSIFICAÇÃO (86e3n70p9).

A lista do plano saía na ordem do código reduzido tratado como TEXTO (`1, 10, 101,
11`). Para o contador a ordem é a da classificação, que é a estrutura do plano: a
sintética em cima e as analíticas dela logo abaixo (`1`, `1.1`, `1.1.1`,
`1.1.1.02`, `1.1.1.02.001`…). É essa ordem que faz o lugar de uma conta nova ser
achável (a inclusão manual, subtask 9, reusa esta função).

**A chave é DERIVADA na gravação, nunca inferida na hora da leitura**: a coluna
`client_accounting_accounts.sort_key` é calculada por `chart_sort_key` em toda
inserção e atualização (importação pelo modelo e pelo export do Domínio), e a
listagem só faz `ORDER BY sort_key NULLS LAST, code, id`. Ordenar na leitura
(`string_to_array` + `lpad` no `ORDER BY`) custaria uma expressão em toda página e
não usaria índice; e ordenar no cliente quebraria a paginação, que é em SQL.

**A regra**, uma só, e a migration `b2f7c9e41d06` a REPETE em SQL para o backfill
(a migration não importa `app.*`; `tests/integration/test_migrations.py` prova que
as duas fontes produzem o mesmo valor para a mesma amostra):

- a fonte é a classificação quando a planilha trouxe uma; sem classificação, o
  código reduzido (a conta sem classificação ordena entre as outras pelo código,
  com a mesma regra, em vez de cair no fim);
- a fonte é partida em segmentos por `.`; segmento só de dígitos ASCII é
  preenchido com zeros à esquerda até `SORT_SEGMENT_WIDTH` (`1.1.10` →
  `000001.000001.000010`, e `1.1.2` → `000001.000001.000002` vem antes); segmento
  mais largo que isso fica como está (nunca truncado); segmento com letra vai em
  minúsculas, como está;
- os segmentos voltam a ser unidos por `.`, e a comparação de texto da coluna
  passa a ser a ordem numérica por nível: `1 < 1.1 < 1.1.1`, `9 < 10`.

Dígito aqui é só `0-9`: `str.isdigit` aceita `²` e dígitos de outros alfabetos, e
o `lpad` do Postgres não — as duas fontes têm de concordar.
"""

from __future__ import annotations

import re

#: Largura de cada segmento numérico depois do preenchimento. O Domínio usa
#: segmentos de até 3 dígitos (`1.1.1.02.001`); 6 cobre plano com milhares de
#: contas por nível sem chegar perto do teto da coluna.
SORT_SEGMENT_WIDTH = 6

#: O separador de nível da classificação (e do código, quando ele é a fonte).
SORT_SEGMENT_SEPARATOR = "."

_ASCII_DIGITS = re.compile(r"[0-9]+")


def _sort_segment(segment: str) -> str:
    if _ASCII_DIGITS.fullmatch(segment):
        return segment.zfill(SORT_SEGMENT_WIDTH)
    return segment.lower()


def chart_sort_key(classification: str | None, code: str) -> str:
    """A chave de ordenação de UMA conta — função PURA, a fonte única da regra.

    `classification` é a classificação hierárquica como a planilha trouxe (ou
    `None`); `code` é o código reduzido. Quem grava conta (importação, e a inclusão
    manual quando existir) chama isto e persiste o resultado em `sort_key`.

    >>> chart_sort_key("1.1.10", "7")
    '000001.000001.000010'
    >>> chart_sort_key(None, "10")
    '000010'
    >>> chart_sort_key("1.1.A", "7")
    '000001.000001.a'
    """
    source = classification if classification else code
    return SORT_SEGMENT_SEPARATOR.join(
        _sort_segment(segment) for segment in source.split(SORT_SEGMENT_SEPARATOR)
    )
