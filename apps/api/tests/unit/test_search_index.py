"""Testes unitários do blind index de busca (S16).

Foco: garantir que tokenização é idempotente, descarta tokens curtos, mata
acentos e case e que `compute_search_hmac` ↔ `compute_query_hmacs` casam
para a mesma chave. Sem DB, sem I/O.
"""

from __future__ import annotations

import pytest

from app.core.search_index import (
    HMAC_TRUNCATE_BYTES,
    MIN_TOKEN_LENGTH,
    compute_query_hmacs,
    compute_search_hmac,
    tokenize_for_search,
)

_KEY_A = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
_KEY_B = "ff112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"


class TestTokenizeForSearch:
    """Normalização: acentos, case, pontuação, min length, dedup, ordem."""

    def test_lowercases(self) -> None:
        assert tokenize_for_search("ABC Def") == ["abc", "def"]

    def test_strips_accents(self) -> None:
        """Combining marks descartados — 'café' e 'cafe' colidem."""
        assert tokenize_for_search("Café") == tokenize_for_search("cafe")
        assert tokenize_for_search("Pagamento Açaí") == ["pagamento", "acai"]

    def test_strips_punctuation(self) -> None:
        assert tokenize_for_search("foo, bar; baz! qux?") == ["foo", "bar", "baz", "qux"]

    def test_short_tokens_dropped(self) -> None:
        """< MIN_TOKEN_LENGTH chars caem fora — reduz n-grama vaza."""
        assert MIN_TOKEN_LENGTH == 3
        assert tokenize_for_search("de ou em") == []
        assert tokenize_for_search("pagamento de boleto") == ["pagamento", "boleto"]

    def test_dedupes_preserving_first_position(self) -> None:
        """Duplicata é descartada — manda só a 1ª ocorrência."""
        assert tokenize_for_search("foo bar foo baz bar") == ["foo", "bar", "baz"]

    def test_empty_input_returns_empty(self) -> None:
        assert tokenize_for_search("") == []
        assert tokenize_for_search("   ") == []
        assert tokenize_for_search("!!! ??? ...") == []

    def test_unicode_emojis_filtered(self) -> None:
        """Emojis viram nada — \\w não cobre — restam só palavras."""
        assert tokenize_for_search("🔐 segredo 💰 grana") == ["segredo", "grana"]

    def test_idempotent(self) -> None:
        """Aplicar 2x não muda o resultado."""
        once = tokenize_for_search("Pagamento Padaria PÃO")
        twice = tokenize_for_search(" ".join(once))
        assert once == twice

    def test_numbers_count_as_tokens(self) -> None:
        """Identificadores numéricos (NF 12345) são úteis em busca."""
        assert tokenize_for_search("Nota 12345 Fiscal") == ["nota", "12345", "fiscal"]


class TestComputeSearchHmac:
    """Lado persistência — formato e propriedades do output."""

    def test_returns_none_when_no_tokens(self) -> None:
        assert compute_search_hmac("", _KEY_A) is None
        assert compute_search_hmac("!!! ??? ...", _KEY_A) is None
        assert compute_search_hmac("de em", _KEY_A) is None

    def test_returns_space_delimited_tokens(self) -> None:
        out = compute_search_hmac("Pagamento Padaria", _KEY_A)
        assert out is not None
        # Leading + trailing space + tokens hex de HMAC_TRUNCATE_BYTES*2 chars
        assert out.startswith(" ")
        assert out.endswith(" ")
        parts = out.strip().split(" ")
        assert len(parts) == 2
        for tok in parts:
            assert len(tok) == HMAC_TRUNCATE_BYTES * 2
            assert all(c in "0123456789abcdef" for c in tok)

    def test_same_input_same_output_for_same_key(self) -> None:
        """HMAC é determinístico — mesma chave, mesma saída."""
        a = compute_search_hmac("Recebimento Cielo", _KEY_A)
        b = compute_search_hmac("Recebimento Cielo", _KEY_A)
        assert a == b

    def test_different_keys_give_different_outputs(self) -> None:
        """Trocar a chave invalida todo o blind index — propriedade necessária."""
        a = compute_search_hmac("Pagamento", _KEY_A)
        b = compute_search_hmac("Pagamento", _KEY_B)
        assert a != b

    def test_accent_normalized_input_collides(self) -> None:
        """'Café' e 'cafe' geram MESMO HMAC — busca insensível a acento."""
        a = compute_search_hmac("Café com leite", _KEY_A)
        b = compute_search_hmac("CAFE COM LEITE", _KEY_A)
        # Cabe lembrar: "com" é < 3 chars? Não — 3 chars ≥ MIN_TOKEN_LENGTH.
        assert a == b


class TestComputeQueryHmacs:
    """Lado consulta — combina com compute_search_hmac na mesma chave."""

    def test_returns_empty_when_no_tokens(self) -> None:
        assert compute_query_hmacs("", _KEY_A) == []
        assert compute_query_hmacs("!!! de em", _KEY_A) == []

    def test_matches_persisted_hmac(self) -> None:
        """O HMAC do token da query bate com o HMAC persistido."""
        persisted = compute_search_hmac("Pagamento Padaria", _KEY_A)
        assert persisted is not None
        query_hmacs = compute_query_hmacs("padaria", _KEY_A)
        assert len(query_hmacs) == 1
        # O HMAC retornado deve aparecer (cercado por espaços) no persistido.
        assert f" {query_hmacs[0]} " in persisted

    def test_accent_insensitive_query(self) -> None:
        """Buscar 'Padaria' encontra texto persistido como 'PADARIA' (acentuação)."""
        persisted = compute_search_hmac("Padaria do Bairro", _KEY_A)
        assert persisted is not None
        assert all(f" {h} " in persisted for h in compute_query_hmacs("padaria", _KEY_A))
        # Variante com case diferente também
        assert all(f" {h} " in persisted for h in compute_query_hmacs("PADARIA", _KEY_A))

    def test_different_key_does_not_match(self) -> None:
        """Mesmo texto + chaves distintas → HMACs distintos → busca não casa."""
        persisted = compute_search_hmac("Padaria", _KEY_A)
        assert persisted is not None
        query_hmacs = compute_query_hmacs("padaria", _KEY_B)
        assert query_hmacs
        assert query_hmacs[0] not in persisted

    def test_multi_word_query_produces_multiple_hmacs(self) -> None:
        """'pagamento padaria' vira 2 HMACs — caller AND-ará as 2 condições."""
        hmacs = compute_query_hmacs("pagamento padaria", _KEY_A)
        assert len(hmacs) == 2


class TestKeyValidation:
    """Chave malformada falha cedo — sem cair em depths obscuros do HMAC."""

    def test_empty_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="vazia"):
            compute_search_hmac("foo", "")

    def test_short_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="64 chars hex"):
            compute_search_hmac("foo", "ab" * 10)

    def test_non_hex_key_rejected(self) -> None:
        with pytest.raises(ValueError, match="hexadecimal"):
            compute_query_hmacs("foo", "z" * 64)
