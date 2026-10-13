"""Derivação do `cCodIntLanc` — a chave de integração POR LINHA (BACK 07.2).

**A regra que não pode ser quebrada:** a chave vem da IDENTIDADE da linha
(`reconciliation_file_entries.id`), **nunca** do conteúdo dela (data + valor +
descrição). Duas compras reais idênticas na mesma fatura — dois cafés de
R$ 12,00 no mesmo dia, no mesmo estabelecimento — têm conteúdo idêntico. Um
hash de conteúdo colapsaria as duas numa chave só e **deixaria de lançar a
segunda**: dinheiro FALTANDO na contabilidade do cliente. E isso é pior que
dinheiro duplicado, porque o critério de rollback da sprint ("um único
duplicado desliga o recurso") só vigia o duplicado — o faltante passaria.

**O aperto de 20 caracteres.** `cCodIntLanc` é `string20` na Omie. Um UUID em
hex tem 32 caracteres e não cabe; truncá-lo seria escolher uma taxa de colisão
sem medir qual. O encoding abaixo é dimensionado de propósito:

    "ADL" (3) + base32 de 85 bits (17) = 20 caracteres

85 bits de digest sobre o UUID da linha. Pelo paradoxo do aniversário, a chance
de duas linhas do MESMO cliente colidirem é ~N²/2^86: com 1 milhão de linhas,
~1,3e-14. Não é zero — e por isso a colisão **não** é tratada como
impossível: `UNIQUE(client_id, cod_int_lanc)` existe no banco e transforma o
caso em `IntegrityError` (erro tratado), nunca numa linha silenciosamente não
lançada.

**Por que passar por um digest em vez de reencodar os bytes do UUID.** Os dois
seriam igualmente únicos (o UUIDv4 já é aleatório). O digest evita entregar a
PK do ADL a um sistema de terceiros, e mantém o encoding estável se um dia a
origem da identidade mudar de tipo.

**Alfabeto.** base32 padrão (RFC 4648) — `A-Z2-7`, maiúsculas, sem símbolo. É
seguro para um campo de texto de API externa e legível para o operador, que
precisa localizar o lançamento no Omie pela chave.
"""

from __future__ import annotations

import base64
import hashlib
from uuid import UUID

from app.db.models import COD_INT_LANC_MAX_LENGTH

#: Prefixo humano — o operador vê essa chave na tela do Omie e precisa saber
#: de onde ela veio. Consome 3 dos 20 caracteres, deliberadamente.
COD_INT_LANC_PREFIX = "ADL"

#: 17 chars de base32 = 85 bits de entropia (ver o cabeçalho para o cálculo de
#: colisão). Derivado, não escolhido à mão: é o que sobra dos 20 da Omie.
_SUFFIX_LENGTH = COD_INT_LANC_MAX_LENGTH - len(COD_INT_LANC_PREFIX)

#: 11 bytes = 88 bits de digest; a base32 de 88 bits dá 18 chars e cortamos em
#: 17. Pedir menos bytes do que os chars consomem produziria padding no fim da
#: chave — caracteres constantes que não somam entropia nenhuma.
_DIGEST_BYTES = 11

#: Domínio de derivação. Evita que este digest coincida com qualquer outro
#: derivado do mesmo UUID em outro ponto do sistema.
_DIGEST_PERSON = b"adl-lanc"


# ----------------------------------------------------------------------
# Lançamento como Contas a Pagar (86e3n70q0, bloco B): uma chave POR PASSO
# ----------------------------------------------------------------------
#
# A provisão faz até três escritas no Omie, e cada uma tem a sua chave de
# integração — é sobre ela que a Omie é idempotente (captura de 13/10/2026: o 2º
# envio de cada uma devolveu o MESMO id). Título e baixa usam o MESMO sufixo da
# linha que o `cCodIntLanc`, com prefixo próprio: as chaves de uma linha diferem
# entre si (são namespaces diferentes no Omie de qualquer jeito) e a pessoa que
# procura no Omie acha o título e a baixa de uma compra pelo mesmo final. O
# fornecedor é a exceção, de propósito: a chave dele vem do CNPJ, porque um
# fornecedor é um por CNPJ no Omie do cliente, não um por linha.

#: Título a pagar (`codigo_lancamento_integracao`, `string60`).
PAYABLE_KEY_PREFIX = "HCP"
#: Baixa do título (`codigo_baixa_integracao`, `string20` — o teto mais apertado).
PAYMENT_KEY_PREFIX = "HBX"
#: Fornecedor cadastrado pela plataforma (`codigo_cliente_integracao`, `string60`).
SUPPLIER_KEY_PREFIX = "HFN"

#: Domínio do digest do CNPJ — outro `person`, para que o sufixo do fornecedor
#: nunca coincida com o de uma linha.
_SUPPLIER_DIGEST_PERSON = b"hos-forn"


def _suffix(material: bytes, person: bytes) -> str:
    """17 chars de base32 de um blake2s de 88 bits (ver o cabeçalho do módulo)."""
    digest = hashlib.blake2s(material, digest_size=_DIGEST_BYTES, person=person).digest()
    return base64.b32encode(digest).decode("ascii").rstrip("=")[:_SUFFIX_LENGTH]


def derive_payable_key(file_entry_id: UUID) -> str:
    """`codigo_lancamento_integracao` do título a pagar desta linha."""
    return f"{PAYABLE_KEY_PREFIX}{_suffix(file_entry_id.bytes, _DIGEST_PERSON)}"


def derive_payment_key(file_entry_id: UUID) -> str:
    """`codigo_baixa_integracao` da baixa do título desta linha."""
    return f"{PAYMENT_KEY_PREFIX}{_suffix(file_entry_id.bytes, _DIGEST_PERSON)}"


def derive_supplier_key(cnpj_digits: str) -> str:
    """`codigo_cliente_integracao` do fornecedor que a plataforma cadastra.

    Derivada do CNPJ NORMALIZADO (14 dígitos, `core/cnpj.normalize_cnpj`): o mesmo
    fornecedor em duas linhas, ou em duas conciliações, é a MESMA chave — e a
    Omie devolve o mesmo cadastro em vez de criar outro. A chave não persiste
    aqui (o CNPJ também não, §4.5); ela só vai à Omie do próprio cliente.
    """
    if len(cnpj_digits) != 14 or not cnpj_digits.isdigit():
        raise ValueError("derive_supplier_key espera o CNPJ normalizado (14 dígitos)")
    return f"{SUPPLIER_KEY_PREFIX}{_suffix(cnpj_digits.encode('ascii'), _SUPPLIER_DIGEST_PERSON)}"


def derive_cod_int_lanc(file_entry_id: UUID) -> str:
    """Chave `cCodIntLanc` determinística para uma linha da fatura.

    Determinística de propósito: no caminho de timeout (BACK 07.4) o ADL
    precisa perguntar à Omie "este lançamento entrou?" usando a MESMA chave,
    sem depender de ter conseguido gravar algo entre o envio e a falha.

    Args:
        file_entry_id: PK da linha do arquivo. É a identidade — duas linhas
            distintas com o mesmo conteúdo têm PKs distintas e, portanto,
            chaves distintas.

    Returns:
        Chave de até 20 caracteres, `[A-Z2-7]` após o prefixo `ADL`.
    """
    return f"{COD_INT_LANC_PREFIX}{_suffix(file_entry_id.bytes, _DIGEST_PERSON)}"
