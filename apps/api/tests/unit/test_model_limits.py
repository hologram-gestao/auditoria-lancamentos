"""Testes da validação do teto de saída na subida (BACK 02.1).

`validate_parse_output_config` lê apenas alguns atributos do Settings —
usamos um stub leve para não precisar montar o Settings inteiro (que exige
DATABASE_URL, chaves de crypto, etc). Para `claude-sonnet-4-5` (na tabela
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


def _settings_stub(*, configured: int, model: str = "claude-sonnet-4-5") -> SimpleNamespace:
    """Stub com só os atributos que a validação lê."""
    return SimpleNamespace(
        ADL_PARSE_MAX_OUTPUT_TOKENS=configured,
        ANTHROPIC_MODEL_DEFAULT=model,
        ANTHROPIC_API_KEY=SecretStr(""),
        ANTHROPIC_TIMEOUT_SECONDS=60.0,
    )


class TestValidateParseOutputConfig:
    async def test_value_within_cap_passes(self) -> None:
        # 32.000 <= 64.000 (cap do claude-sonnet-4-5) → sobe normal.
        await validate_parse_output_config(_settings_stub(configured=32_000))  # type: ignore[arg-type]

    async def test_value_above_cap_refuses_boot(self) -> None:
        # 100.000 > 64.000 → o serviço NÃO inicia (fail-fast).
        with pytest.raises(ParseOutputConfigError) as excinfo:
            await validate_parse_output_config(_settings_stub(configured=100_000))  # type: ignore[arg-type]
        assert "excede o teto de saída" in str(excinfo.value)

    async def test_at_cap_exactly_passes(self) -> None:
        await validate_parse_output_config(_settings_stub(configured=64_000))  # type: ignore[arg-type]

    async def test_claude_sonnet_5_aceita_acima_do_teto_do_4_5(self) -> None:
        # 100.000 recusaria a subida no 4.5 (64.000) e cabe no 5 (128.000).
        stub = _settings_stub(configured=100_000, model="claude-sonnet-5")
        await validate_parse_output_config(stub)  # type: ignore[arg-type]

    async def test_unknown_model_without_key_does_not_block(self) -> None:
        # Modelo fora da tabela + sem chave → cap desconhecido → não bloqueia.
        stub = _settings_stub(configured=999_999, model="modelo-inexistente-xyz")
        await validate_parse_output_config(stub)  # type: ignore[arg-type]


class TestResolveModelOutputCap:
    async def test_known_model_uses_static_table(self) -> None:
        cap = await resolve_model_output_cap("claude-sonnet-4-5", _settings_stub(configured=1))  # type: ignore[arg-type]
        assert cap == KNOWN_MODEL_OUTPUT_CAPS["claude-sonnet-4-5"] == 64_000

    async def test_claude_sonnet_5_resolve_sem_rede(self) -> None:
        # Sem chave no stub: se o modelo não estivesse na tabela, a resposta
        # seria None (a Models API nem é tentada sem chave).
        cap = await resolve_model_output_cap("claude-sonnet-5", _settings_stub(configured=1))  # type: ignore[arg-type]
        assert cap == KNOWN_MODEL_OUTPUT_CAPS["claude-sonnet-5"] == 128_000

    def test_modelo_padrao_do_settings_esta_na_tabela(self) -> None:
        # O padrão do código (claude-sonnet-5 desde 86e3wg8ek) valida offline na
        # subida; um padrão fora da tabela dependeria da Models API para isso.
        from app.core.config import Settings

        default = Settings.model_fields["ANTHROPIC_MODEL_DEFAULT"].default
        assert default == "claude-sonnet-5"
        assert default in KNOWN_MODEL_OUTPUT_CAPS

    async def test_claude_sonnet_4_5_continua_na_tabela(self) -> None:
        # Voltar pela variável de ambiente segue offline até a retirada.
        assert KNOWN_MODEL_OUTPUT_CAPS["claude-sonnet-4-5"] == 64_000

    async def test_unknown_model_without_key_returns_none(self) -> None:
        cap = await resolve_model_output_cap("modelo-xyz", _settings_stub(configured=1))  # type: ignore[arg-type]
        assert cap is None
