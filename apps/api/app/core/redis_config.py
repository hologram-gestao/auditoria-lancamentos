"""Configuração compartilhada do Redis pra ARQ.

Centraliza o ``RedisSettings`` usado tanto pelo dispatcher (que enfileira
do API) quanto pelo worker (que consome). Evita default frágil do ARQ
estourar em conexões pra Upstash distante.
"""

from __future__ import annotations

from arq.connections import RedisSettings

# Default do ARQ é 1 segundo, suficiente pra Redis local mas curto demais
# pra Upstash em GCP us-east4: ~120ms RTT de SP + TLS handshake (3-4 RTTs
# pra cert/key exchange) gasta ~600-900ms só pra abrir a conexão. Qualquer
# variação de rede explode o limite e o worker crasha em loop.
DEFAULT_CONN_TIMEOUT_SECONDS = 10


def build_redis_settings(redis_url: str) -> RedisSettings:
    """Cria ``RedisSettings`` a partir de DSN, com timeouts seguros pra TLS."""
    settings = RedisSettings.from_dsn(redis_url)
    settings.conn_timeout = DEFAULT_CONN_TIMEOUT_SECONDS
    return settings
