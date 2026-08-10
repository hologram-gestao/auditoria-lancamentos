"""Afinidade entre o fornecedor do Omie e a descrição do extrato.

Desdobramento da sugestão 1 da Bruna (04/08/2026): ela apontou que o sistema
cruzou o extrato de um fornecedor com o lançamento de outro "considerando apenas
o valor". Estava certa — o matcher conhecia valor e data, e mais nada. Este
módulo é o que dá a ele uma terceira dimensão.

REGRAS QUE NÃO PODEM SER QUEBRADAS (CLAUDE.md §5.9, §4.5):

- **Determinístico, sem IA.** Nenhuma chamada a modelo aqui, nem indireta. O
  fornecedor vem do response do Omie — é dado, não inferência.
- **Só desempate, nunca exclusão.** O resultado desta função ordena candidatos;
  não remove nenhum. Descrição de extrato é texto sujo e abreviado, e
  transformar ruído em "não conciliado" trocaria um falso positivo por outro
  pior.
- **Nada persiste e nada é logado.** Nome de fornecedor é dado identificável do
  cliente final. Trafega em memória durante o processamento e morre ali.

POR QUE CONTAGEM DE TOKENS, E NÃO UM LIMIAR DE SIMILARIDADE: similaridade fuzzy
(Levenshtein, token ratio) resolveria mais casos, mas obriga a escolher um corte
— 0,8? 0,75? — que ninguém consegue justificar quando um cruzamento sai errado e
alguém pergunta por quê. Contar quantos tokens do nome aparecem na descrição não
tem corte nenhum: o candidato que compartilha mais tokens vem antes, e pronto.
Zero tokens em comum simplesmente não informa nada, e o desempate cai para o
critério de data que já existia.
"""

from __future__ import annotations

import re
import unicodedata

# Tokens que não distinguem ninguém: conectivos e sufixos societários. Sem esta
# lista, "COMERCIO DE ALIMENTOS LTDA" e "COMERCIO DE PECAS LTDA" empatariam em
# dois tokens ("COMERCIO", "LTDA") sem nenhuma evidência real.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "DE",
        "DA",
        "DO",
        "DAS",
        "DOS",
        "E",
        "EM",
        "LTDA",
        "ME",
        "EPP",
        "EIRELI",
        "SA",
        "CIA",
        "MEI",
    }
)

# Tudo que não é letra ou dígito vira separador: o extrato mistura o nome com
# ruído de layout ("Pix enviado: Cp:18236120-Maiane Medrado Silva").
_NON_ALNUM = re.compile(r"[^0-9A-Z]+")

# Token com menos de 3 caracteres coincide por acaso com facilidade demais.
_MIN_TOKEN_LENGTH = 3


def _normalize(text: str) -> str:
    """Maiúsculas, sem acento, sem pontuação. Reprodutível e sem dependência."""
    decomposed = unicodedata.normalize("NFKD", text)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM.sub(" ", without_accents.upper()).strip()


def _significant_tokens(text: str) -> set[str]:
    """Tokens que carregam identidade — sem conectivos, sufixos e siglas curtas."""
    return {
        token
        for token in _normalize(text).split()
        if len(token) >= _MIN_TOKEN_LENGTH and token not in _STOPWORDS
    }


def supplier_affinity(supplier: str | None, description: str) -> int:
    """Quantos tokens significativos do fornecedor aparecem na descrição.

    Quanto MAIOR, mais evidência de que os dois se referem à mesma pessoa. Zero
    significa "não sei" — nunca "não é". O caller usa isto como critério de
    ordenação entre candidatos, jamais como filtro.

    Args:
        supplier: razão social/nome fantasia vindo do Omie. `None` para títulos
            a pagar/receber, que trazem só o código do cliente — nesse caso não
            há sinal e o retorno é 0.
        description: descrição da linha do extrato, já decifrada.

    Returns:
        Contagem de tokens em comum (0 quando não há sinal).

    Examples:
        >>> supplier_affinity("Maiane Medrado Silva", "Pix enviado: Cp:18236120-Maiane Medrado Silva")
        3
        >>> supplier_affinity("Cleidson Quiteria de Souza", "Pix enviado: Cp:99-Maiane Medrado Silva")
        0
    """
    if not supplier:
        return 0
    description_tokens = _significant_tokens(description)
    if not description_tokens:
        return 0
    return len(_significant_tokens(supplier) & description_tokens)
