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
    digest = hashlib.blake2s(
        file_entry_id.bytes,
        digest_size=_DIGEST_BYTES,
        person=_DIGEST_PERSON,
    ).digest()
    suffix = base64.b32encode(digest).decode("ascii").rstrip("=")[:_SUFFIX_LENGTH]
    return f"{COD_INT_LANC_PREFIX}{suffix}"
