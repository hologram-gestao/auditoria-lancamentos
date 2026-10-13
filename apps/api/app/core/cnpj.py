"""CNPJ: normalizar, validar e formatar (86e3n70q0, lançamento como Contas a Pagar).

Um lugar só para as três perguntas, porque três chamadores dependem da MESMA
resposta: a busca do fornecedor (`GET /clients/{id}/omie/fornecedores`), o
pedido de provisão por linha e a chave de integração do fornecedor
(`derive_supplier_key`). Se cada um normalizasse do seu jeito, o mesmo CNPJ
escrito com e sem máscara viraria duas chaves e dois fornecedores no Omie.

**CNPJ é dado do cliente final (§4.5).** Nada aqui loga, e nada persiste o
valor: ele trafega em memória até a Omie e some.
"""

from __future__ import annotations

import re

CNPJ_DIGITS = 14

_NON_DIGITS = re.compile(r"\D")
#: O que a entrada pode ter além dos dígitos: a máscara usual e espaços.
_ALLOWED = re.compile(r"^[\d./\-\s]+$")

_WEIGHTS_FIRST = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_WEIGHTS_SECOND = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)


def _check_digit(digits: str, weights: tuple[int, ...]) -> int:
    remainder = sum(int(d) * w for d, w in zip(digits, weights, strict=True)) % 11
    return 0 if remainder < 2 else 11 - remainder


def normalize_cnpj(raw: str) -> str:
    """Os 14 dígitos de um CNPJ válido, com ou sem máscara.

    Raises:
        ValueError: caractere fora da máscara, tamanho errado, todos os dígitos
            iguais ou dígito verificador errado. A mensagem não ecoa o valor.
    """
    if not _ALLOWED.match(raw or ""):
        raise ValueError("CNPJ com caractere inválido")
    digits = _NON_DIGITS.sub("", raw)
    if len(digits) != CNPJ_DIGITS:
        raise ValueError("CNPJ precisa de 14 dígitos")
    if len(set(digits)) == 1:
        raise ValueError("CNPJ inválido")
    first = _check_digit(digits[:12], _WEIGHTS_FIRST)
    second = _check_digit(digits[:12] + str(first), _WEIGHTS_SECOND)
    if digits[12:] != f"{first}{second}":
        raise ValueError("CNPJ com dígito verificador inválido")
    return digits


def format_cnpj(digits: str) -> str:
    """`00.000.000/0000-00` — a forma que a captura de 13/10/2026 enviou à Omie.

    Tanto o filtro do `ListarClientes` quanto o `cnpj_cpf` do `IncluirCliente`
    foram aceitos com máscara; mandar a mesma forma mantém o request igual ao
    provado.
    """
    if len(digits) != CNPJ_DIGITS or not digits.isdigit():
        raise ValueError("format_cnpj espera os 14 dígitos já normalizados")
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"
