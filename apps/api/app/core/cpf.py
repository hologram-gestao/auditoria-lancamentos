"""CPF: normalizar e validar (86e3qhhrn, vínculo conta contábil ↔ pessoa).

Irmão de `core/cnpj.py`, com a MESMA postura: um lugar só para a pergunta "estes
dígitos são um CPF?", e nada aqui loga nem persiste o valor. O relatório de clientes
e fornecedores do Domínio traz pessoa física (CPF, 11 dígitos) ao lado de pessoa
jurídica (CNPJ, 14), e o vínculo com a conta contábil precisa aceitar as duas.

**CPF é dado do cliente final (§4.5):** quem o guarda, guarda cifrado com a DEK do
cliente; aqui ele só trafega em memória.
"""

from __future__ import annotations

import re

CPF_DIGITS = 11

_NON_DIGITS = re.compile(r"\D")
#: O que a entrada pode ter além dos dígitos: a máscara usual (`000.000.000-00`) e espaços.
_ALLOWED = re.compile(r"^[\d.\-\s]+$")


def _check_digit(digits: str, first_weight: int) -> int:
    total = sum(int(d) * w for d, w in zip(digits, range(first_weight, 1, -1), strict=True))
    remainder = (total * 10) % 11
    return 0 if remainder == 10 else remainder


def normalize_cpf(raw: str) -> str:
    """Os 11 dígitos de um CPF válido, com ou sem máscara.

    Raises:
        ValueError: caractere fora da máscara, tamanho errado, todos os dígitos
            iguais ou dígito verificador errado. A mensagem não ecoa o valor.
    """
    if not _ALLOWED.match(raw or ""):
        raise ValueError("CPF com caractere inválido")
    digits = _NON_DIGITS.sub("", raw)
    if len(digits) != CPF_DIGITS:
        raise ValueError("CPF precisa de 11 dígitos")
    if len(set(digits)) == 1:
        raise ValueError("CPF inválido")
    first = _check_digit(digits[:9], 10)
    second = _check_digit(digits[:10], 11)
    if digits[9:] != f"{first}{second}":
        raise ValueError("CPF com dígito verificador inválido")
    return digits


def format_cpf(digits: str) -> str:
    """`000.000.000-00`, a partir dos 11 dígitos já normalizados."""
    if len(digits) != CPF_DIGITS or not digits.isdigit():
        raise ValueError("format_cpf espera os 11 dígitos já normalizados")
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"
