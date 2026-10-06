"""Limitador de falhas de login por identidade (86e3anx10).

O limite do login deixou de ser por IP: atrás do BFF do Next a API vê o IP do
proxy, e o balde de 5 virava um balde da plataforma inteira. Aqui se prova o
contrato do limitador por e-mail, sem banco: a chave normaliza caixa e espaço,
não carrega o e-mail, a consulta não consome cota, 5 falhas bloqueiam e o
`reset` zera. Que SUCESSO não conta é decisão da rota (ela só chama
`register_failure` no 401); o teste de integração prova isso pela API.
"""

from __future__ import annotations

from app.core.rate_limit import (
    LOGIN_FLOOD_LIMIT,
    LOGIN_IDENTITY_LIMIT,
    LoginIdentityLimiter,
)

EMAIL = "ana@hologram.com.br"


def test_limites_declarados() -> None:
    assert LOGIN_IDENTITY_LIMIT == "5/5minutes"
    assert LOGIN_FLOOD_LIMIT == "60/minute"


def test_mesmo_email_com_caixa_e_espacos_cai_na_mesma_chave() -> None:
    key = LoginIdentityLimiter.identity_key(EMAIL)
    assert LoginIdentityLimiter.identity_key("  Ana@Hologram.COM.br ") == key
    assert LoginIdentityLimiter.identity_key("bia@hologram.com.br") != key


def test_chave_e_prefixo_nao_contem_o_email() -> None:
    key = LoginIdentityLimiter.identity_key(EMAIL)
    prefix = LoginIdentityLimiter.identity_prefix(EMAIL)
    assert key.startswith("login:")
    assert len(key) == len("login:") + 64
    for part in ("ana", "hologram", "@"):
        assert part not in key
    assert len(prefix) == 8
    assert key.removeprefix("login:").startswith(prefix)


def test_cinco_falhas_bloqueiam_e_a_sexta_consulta_diz_bloqueado() -> None:
    limiter = LoginIdentityLimiter()
    for i in range(5):
        assert not limiter.is_blocked(EMAIL), f"bloqueou antes da {i + 1}ª falha"
        limiter.register_failure(EMAIL)
    assert limiter.is_blocked(EMAIL)
    # A grafia diferente do mesmo e-mail também está bloqueada.
    assert limiter.is_blocked(" ANA@hologram.com.br")
    # Outra identidade segue livre.
    assert not limiter.is_blocked("bia@hologram.com.br")


def test_consulta_nao_consome_cota() -> None:
    limiter = LoginIdentityLimiter()
    for _ in range(50):
        assert not limiter.is_blocked(EMAIL)
    for _ in range(4):
        limiter.register_failure(EMAIL)
    for _ in range(50):
        assert not limiter.is_blocked(EMAIL)


def test_reset_zera() -> None:
    limiter = LoginIdentityLimiter()
    for _ in range(5):
        limiter.register_failure(EMAIL)
    assert limiter.is_blocked(EMAIL)
    limiter.reset()
    assert not limiter.is_blocked(EMAIL)


def test_window_e_o_limite_declarado() -> None:
    assert LoginIdentityLimiter().window == LOGIN_IDENTITY_LIMIT
