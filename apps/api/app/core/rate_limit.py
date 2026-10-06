"""Rate limiting central via slowapi.

Padrão CLAUDE.md §3.11 + P0-004:
    - /auth/login: 5 FALHAS / 5 min / E-MAIL (`login_identity_limiter`, abaixo),
      mais um teto de enxurrada por IP (`LOGIN_FLOOD_LIMIT`, 60/min).
    - /reconciliations/parse: 10/min / usuário — chama Anthropic ($$).
    - POST /reconciliations: 10/min / usuário — agenda processamento em background.
    - /clients/test-connection: 30/min / usuário — chama Omie.
    - /clients/{id}/sync-accounts: 30/min / usuário — chama Omie.
    - Demais autenticados (CRUD em DB): sem limit explícito por enquanto
      (DB próprio é o cap natural).

Storage backend:
    - Hoje: in-memory (`memory://`) por processo. Com várias instâncias
      (Cloud Run maxScale>1) o limite NÃO é compartilhado — cada instância tem
      seu próprio contador. Aceitável como hardening de baixo risco por ora.
    - Limite consistente entre instâncias exigiria um storage compartilhado
      passado EXPLICITAMENTE ao `Limiter` (`storage_uri=...`) — o slowapi NÃO lê
      `RATELIMIT_STORAGE_URI` do ambiente sozinho. Decisão de storage adiada
      para o refactoring de infra (saída do Redis; possível edge/Cloud Armor).
    - O limitador por identidade do login (`login_identity_limiter`) tem o
      MESMO limite: `MemoryStorage` por processo. Com N instâncias o teto
      efetivo é 5 vezes N falhas por e-mail na janela. Aceito e documentado (dívida
      86e3anx7y, item 3); storage compartilhado é decisão de infra.

Por que o login não é limitado por IP (86e3anx10):
    O browser chama `/api/v1/*` na mesma origem e o BFF do Next reverse-proxia
    server-side; o uvicorn sobe sem `--forwarded-allow-ips`, então
    `get_remote_address` devolve o IP do PROXY para todo mundo. Um balde de 5
    por IP era um balde de 5 para a plataforma inteira (por instância): cinco
    erros de digitação de pessoas diferentes travavam o login de todos, e
    qualquer um derrubava o login de propósito. Liberar `--forwarded-allow-ips`
    não resolve enquanto a API for alcançável direto pelo `*.run.app`: o
    `X-Forwarded-For` seria forjável. Recuperar o IP real é a task de postura
    (86e3anx69), com o Load Balancer na frente.

    Por isso o limite que distingue pessoas é por IDENTIDADE e conta FALHAS,
    dentro da rota (com o `payload` já validado, sem middleware que pré-lê o
    body). Sucesso não conta e não zera: a janela deslizante cuida disso, e
    quem loga certo várias vezes nunca cai no 429. Janela de 5 minutos NÃO é
    lockout: cinco falhas seguidas travam AQUELE e-mail por até 5 minutos
    nesta instância, e só; não existe bloqueio de conta.

Key functions:
    - `get_remote_address`: IP visto pela API (default; no /login é só o teto
      de enxurrada, porque atrás do BFF é o IP do proxy).
    - `user_id_key_func`: valida ASSINATURA do JWT no cookie (HS256 +
      JWT_SECRET) e usa `sub` como chave. Slowapi avalia a key ANTES do
      handler, então não dá pra reusar `get_current_user`. Pulamos `exp`
      de propósito — rate limit precisa funcionar para tokens recém-
      expirados também (o handler vai responder 401 e o atacante não
      ganha nada do "bucket" alocado). Assinatura inválida ou cookie
      ausente → fallback para IP, impedindo que atacante forge `sub`
      arbitrário e estoure o storage de rate limit com chaves fake.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from jose import JWTError, jwt
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

if TYPE_CHECKING:
    from starlette.requests import Request

# Nome do cookie HTTP, não uma credencial — duplicado de `core.dependencies`
# para manter este módulo livre do import do FastAPI.
_ACCESS_TOKEN_COOKIE_NAME = "access_token"  # noqa: S105
_JWT_ALGORITHM = "HS256"

#: Falhas de login por e-mail na janela deslizante. Conta só falha (qualquer 401
#: do `AuthService.login`); a consulta antes da verificação não consome cota.
LOGIN_IDENTITY_LIMIT = "5/5minutes"

#: Teto de ENXURRADA do login, por IP do slowapi. ⚠️ Atrás do BFF do Next o IP
#: que a API enxerga é o do proxy (86e3anx10), então este limite vale para TODOS
#: os usuários juntos, por instância: protege o custo do bcrypt numa enxurrada e
#: não distingue pessoas. Quem distingue é o `login_identity_limiter`. Mesmo
#: molde do `LEADS_RATE_LIMIT`.
LOGIN_FLOOD_LIMIT = "60/minute"

#: Tamanho do prefixo do hash que vai para log: o bastante para correlacionar
#: eventos da mesma identidade, curto demais para servir de dicionário.
_IDENTITY_PREFIX_LENGTH = 8


def user_id_key_func(request: Request) -> str:
    """Retorna `user:{sub}` se o JWT do cookie tem assinatura válida; senão IP.

    Valida a assinatura HS256 contra `JWT_SECRET` (mesma usada em
    `core.security.decode_token`). Pula `exp` — rate limit precisa contar
    requests de tokens recém-expirados também (o handler responde 401
    depois, sem dar bypass de bucket).

    Por que não reusar `decode_token`: queremos NÃO levantar em token
    expirado, e queremos cair em IP de forma silenciosa em qualquer outro
    erro (token malformado, cookie ausente). Aqui é melhor inline.
    """
    token = request.cookies.get(_ACCESS_TOKEN_COOKIE_NAME)
    if not token:
        return get_remote_address(request)
    try:
        secret = get_settings().JWT_SECRET.get_secret_value()
        claims = jwt.decode(
            token,
            secret,
            algorithms=[_JWT_ALGORITHM],
            options={"verify_exp": False, "verify_aud": False},
        )
    except JWTError:
        return get_remote_address(request)
    sub = claims.get("sub") if isinstance(claims, dict) else None
    if isinstance(sub, str) and sub:
        return f"user:{sub}"
    return get_remote_address(request)


def _identity_digest(email: str) -> str:
    """SHA-256 do e-mail normalizado (strip + lower). O e-mail nunca vira chave."""
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


class LoginIdentityLimiter:
    """Limite de FALHAS de login por identidade (e-mail), janela deslizante.

    A chave é `login:<sha256 do e-mail normalizado>`: o mesmo e-mail com caixa
    ou espaços diferentes cai no mesmo balde, e o e-mail em claro não fica no
    storage. Em memória, por instância (ver docstring do módulo).
    """

    def __init__(self, limit: str = LOGIN_IDENTITY_LIMIT) -> None:
        self._window = limit
        self._item = parse(limit)
        self._storage = MemoryStorage()
        self._strategy = MovingWindowRateLimiter(self._storage)

    @property
    def window(self) -> str:
        """O limite em notação do `limits` (ex.: `5/5minutes`), para log."""
        return self._window

    @staticmethod
    def identity_key(email: str) -> str:
        """Chave do balde: `login:<sha256>`, sem o e-mail."""
        return f"login:{_identity_digest(email)}"

    @staticmethod
    def identity_prefix(email: str) -> str:
        """Os primeiros 8 hex do hash: o único rastro da identidade que vai para log."""
        return _identity_digest(email)[:_IDENTITY_PREFIX_LENGTH]

    def is_blocked(self, email: str) -> bool:
        """True quando a identidade já tem o limite de falhas na janela.

        Usa `test`, que só lê a janela: consultar não consome cota.
        """
        return not self._strategy.test(self._item, self.identity_key(email))

    def register_failure(self, email: str) -> None:
        """Conta uma falha (`hit`, que consome cota) para a identidade."""
        self._strategy.hit(self._item, self.identity_key(email))

    def reset(self) -> None:
        """Zera todos os baldes (fixture de teste)."""
        self._storage.reset()


# Singleton do login — consultado e alimentado pela rota `POST /auth/login`.
login_identity_limiter = LoginIdentityLimiter()


# Singleton — anexado a `app.state` em `main.create_app()`.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],  # nenhum limit global; aplicar por rota
    headers_enabled=True,  # X-RateLimit-* nos headers de resposta
)
