"""Testes da validação do teto de saída na subida (BACK 02.1).

`validate_parse_output_config` lê apenas alguns atributos do Settings —
usamos um stub leve para não precisar montar o Settings inteiro (que exige
DATABASE_URL, chaves de crypto, etc). Para `claude-sonnet-5-5` (na tabela
estática) a validação é offline e determinística: nunca toca a Models API.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.integrations.anthropic.model_limits import (
    KNOWN_MODEL_OUTPUT_CAPS,
    ParseOutputConfigError,
    resolve_model_output_cap,
    validate_parse_output_config,
)


def _settings_stub(*, configured: int, model: str = "claude-sonnet-5-5") -> SimpleNamespace:
    """Stub com só os atributos que a validação lê."""
    return SimpleNamespace(
        ADL_PARSE_MAX_OUTPUT_TOKENS=configured,
        ANTHROPIC_MODEL_DEFAULT=model,
        ANTHROPIC_API_KEY=SecretStr(""),
        ANTHROPIC_TIMEOUT_SECONDS=60.0,
    )


class TestValidateParseOutputConfig:
    async def test_value_within_cap_passes(self) -> None:
        # 32.000 <= 128.000 (cap do claude-sonnet-5-5) → sobe normal.
        await validate_parse_output_config(_settings_stub(configured=32_000))  # type: ignore[arg-type]

    async def test_value_above_cap_refuses_boot(self) -> None:
        # 200.000 > 128.000 → o serviço NÃO inicia (fail-fast).
        with pytest.raises(ParseOutputConfigError) as excinfo:
            await validate_parse_output_config(_settings_stub(configured=200_000))  # type: ignore[arg-type]
        assert "excede o teto de saída" in str(excinfo.value)

    async def test_at_cap_exactly_passes(self) -> None:
        await validate_parse_output_config(_settings_stub(configured=128_000))  # type: ignore[arg-type]

    async def test_claude_sonnet_5_continua_valido_pela_variavel(self) -> None:
        # Voltar para a ponte pela variável de ambiente segue offline.
        stub = _settings_stub(configured=100_000, model="claude-sonnet-5")
        await validate_parse_output_config(stub)  # type: ignore[arg-type]

    async def test_unknown_model_without_key_does_not_block(self) -> None:
        # Modelo fora da tabela + sem chave → cap desconhecido → não bloqueia.
        stub = _settings_stub(configured=999_999, model="modelo-inexistente-xyz")
        await validate_parse_output_config(stub)  # type: ignore[arg-type]


class TestResolveModelOutputCap:
    async def test_claude_sonnet_5_5_resolve_sem_rede(self) -> None:
        # Sem chave no stub: fora da tabela a resposta seria None (a Models API
        # nem é tentada sem chave). 86e3wg8et.
        cap = await resolve_model_output_cap("claude-sonnet-5-5", _settings_stub(configured=1))  # type: ignore[arg-type]
        assert cap == KNOWN_MODEL_OUTPUT_CAPS["claude-sonnet-5-5"] == 128_000

    async def test_claude_sonnet_5_resolve_sem_rede(self) -> None:
        # Sem chave no stub: se o modelo não estivesse na tabela, a resposta
        # seria None (a Models API nem é tentada sem chave).
        cap = await resolve_model_output_cap("claude-sonnet-5", _settings_stub(configured=1))  # type: ignore[arg-type]
        assert cap == KNOWN_MODEL_OUTPUT_CAPS["claude-sonnet-5"] == 128_000

    def test_modelo_padrao_do_settings_esta_na_tabela(self) -> None:
        # O padrão do código (claude-sonnet-5-5 desde 86e3wg8et) valida offline na
        # subida; um padrão fora da tabela dependeria da Models API para isso.
        from app.core.config import Settings

        default = Settings.model_fields["ANTHROPIC_MODEL_DEFAULT"].default
        assert default == "claude-sonnet-5-5"
        assert default in KNOWN_MODEL_OUTPUT_CAPS

    def test_modelo_retirado_saiu_da_tabela(self) -> None:
        # O Sonnet 4.5 sai da API em 30/11/2026: nada aponta mais para ele.
        assert set(KNOWN_MODEL_OUTPUT_CAPS) == {"claude-sonnet-5", "claude-sonnet-5-5"}

    def test_variavel_de_fallback_saiu(self) -> None:
        # ANTHROPIC_MODEL_FALLBACK estava declarada e sem nenhum uso (86e3wg8et).
        from app.core.config import Settings

        assert "ANTHROPIC_MODEL_FALLBACK" not in Settings.model_fields

    async def test_unknown_model_without_key_returns_none(self) -> None:
        cap = await resolve_model_output_cap("modelo-xyz", _settings_stub(configured=1))  # type: ignore[arg-type]
        assert cap is None
