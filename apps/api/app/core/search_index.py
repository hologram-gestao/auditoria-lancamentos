"""Blind index determinístico para filtro de busca sobre campos criptografados.

Permite executar o filtro `search` da Tela de Revisão em SQL puro, sem
descriptografar todas as linhas em memória antes da paginação. A descrição
permanece criptografada com AES-256-GCM (IV aleatório por linha); ao lado
dela persistimos uma coluna paralela com os tokens normalizados aplicados
em HMAC-SHA256 (truncado em 16 chars hex / 8 bytes / ~64 bits de força
contra preimage por token).

Modelo de busca:
    - Tokenização: NFKD-normalize → strip de combining marks (acentos) →
      lowercase → split em `\\w+` → descarta tokens com < 3 chars → dedupe
      preservando ordem.
    - HMAC por token, concatenado com espaço, com leading e trailing space
      no resultado final: `" tok1 tok2 tok3 "`. Os espaços nas extremidades
      simplificam o `LIKE '% tokX %'` (o token nunca aparece no início ou
      fim sem delimitador, eliminando o caso especial).
    - Consulta: mesma tokenização aplicada ao termo da query; cada token
      vira `LIKE '% hmacX %'` ANDado no SQL. Termo vazio (ou só com tokens
      < 3 chars) ⇒ lista vazia ⇒ caller pula a query e devolve 0 resultados.

Limitações conhecidas (aceitas no MVP):
    - Sem prefix/substring matching: "padar" NÃO encontra "Padaria" — só
      tokens completos casam. A UI documenta isso ao usuário.
    - Sem stemming: "pagamentos" ≠ "pagamento". Aceitável até S15+; quem
      quiser stemming aplica antes de chamar `tokenize_for_search`.
    - Tamanho de output: ~16 hex chars/token + 1 espaço ≈ 17 bytes por
      token único. Descrição típica de 5-10 tokens distintos cabe folgado
      em TEXT.

Segurança:
    - Chave (`SEARCH_BLIND_INDEX_KEY`) é distinta de `OMIE_ENCRYPTION_KEY`
      por design: comprometer uma não vaza a outra.
    - HMAC truncado em 16 chars hex (8 bytes) — colisão pontual entre
      tokens distintos é possível em volumes muito grandes, mas o impacto
      é apenas falso-positivo de busca (linha aparece sem fazer sentido).
      Nada de sigilo vaza: o atacante com acesso ao DB já tem todo o resto.
    - Tokens curtos (< 3 chars) são descartados — reduz vazamento de
      n-gramas curtos e o overhead de busca.
"""

from __future__ import annotations

import hmac
import re
import unicodedata
from hashlib import sha256

# Mínimo de chars por token. Curtos demais geram muitos tokens (vaza
# n-gramas) e raramente discriminam — "DE", "OU", "EM" etc.
MIN_TOKEN_LENGTH = 3

# Bytes do HMAC mantidos após truncamento. 8 bytes ⇒ 16 hex chars ⇒ ~2^64
# de espaço; suficiente contra colisão acidental no volume esperado.
HMAC_TRUNCATE_BYTES = 8

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _normalize(text: str) -> str:
    """Remove acentos via NFKD + descarta combining marks + lowercase.

    Idempotente: aplicar 2x dá o mesmo resultado.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    no_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return no_marks.lower()


def tokenize_for_search(text: str) -> list[str]:
    """Quebra `text` em tokens normalizados deduplicados.

    Ordem de saída segue a 1ª ocorrência no input — útil para testes
    determinísticos e mantém estabilidade do output do `compute_*_hmac`.

    Returns:
        Lista de tokens (lowercase, sem acentos, >= MIN_TOKEN_LENGTH chars),
        sem duplicatas. Lista vazia se o input só tiver pontuação,
        whitespace ou tokens curtos.
    """
    if not text:
        return []
    normalized = _normalize(text)
    seen: set[str] = set()
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(normalized):
        token = match.group(0)
        if len(token) < MIN_TOKEN_LENGTH:
            continue
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _hmac_token(token: str, key_bytes: bytes) -> str:
    """HMAC-SHA256(`token`, key) truncado em `HMAC_TRUNCATE_BYTES` bytes hex."""
    digest = hmac.new(key_bytes, token.encode("utf-8"), sha256).digest()
    return digest[:HMAC_TRUNCATE_BYTES].hex()


def _key_to_bytes(hex_key: str) -> bytes:
    """Mesma checagem do `core.crypto` — falha rápido se a chave estiver malformada.

    Não importamos `crypto._hex_key_to_bytes` pra manter os módulos
    desacoplados: o blind index não depende de AES nem vice-versa.
    """
    if not hex_key:
        raise ValueError("SEARCH_BLIND_INDEX_KEY vazia.")
    if len(hex_key) != 64:
        raise ValueError(
            f"SEARCH_BLIND_INDEX_KEY deve ter 64 chars hex (256 bits). Recebido: {len(hex_key)}."
        )
    try:
        return bytes.fromhex(hex_key)
    except ValueError as exc:
        raise ValueError("SEARCH_BLIND_INDEX_KEY deve ser hexadecimal válido.") from exc


def compute_search_hmac(text: str, hex_key: str) -> str | None:
    """Persistível: string de tokens HMAC para a coluna `description_search_hmac`.

    Formato: `" tok1 tok2 ... tokN "` (com leading/trailing space).
    Retorna `None` quando o texto não rende tokens elegíveis — o caller
    grava NULL na coluna (linhas só com símbolos/pontuação ou descrição
    vazia).

    Args:
        text: descrição em claro a ser indexada.
        hex_key: SEARCH_BLIND_INDEX_KEY em hex (64 chars).

    Returns:
        String pronta para persistir, ou `None` se não há tokens.
    """
    tokens = tokenize_for_search(text)
    if not tokens:
        return None
    key_bytes = _key_to_bytes(hex_key)
    hmacs = [_hmac_token(token, key_bytes) for token in tokens]
    # Leading + trailing space pra LIKE '% xxx %' nunca falhar no extremo.
    return " " + " ".join(hmacs) + " "


def compute_query_hmacs(term: str, hex_key: str) -> list[str]:
    """HMACs do termo de busca, prontos para serem ANDados no SQL.

    Cada elemento da lista é o conteúdo a ser usado em `LIKE '% <hmac> %'`.
    Lista vazia significa "termo sem tokens elegíveis" — o caller DEVE
    pular a query e devolver 0 resultados (consistente com o comportamento
    UX: "buscar por 'de'" não faz sentido).

    Args:
        term: termo cru vindo da query string.
        hex_key: SEARCH_BLIND_INDEX_KEY em hex (64 chars).
    """
    tokens = tokenize_for_search(term)
    if not tokens:
        return []
    key_bytes = _key_to_bytes(hex_key)
    return [_hmac_token(token, key_bytes) for token in tokens]
