"""Configuração compartilhada do Redis pra ARQ.

Centraliza o ``RedisSettings`` usado tanto pelo dispatcher (que enfileira
do API) quanto pelo worker (que consome). Evita default frágil do ARQ
estourar em conexões pra Upstash distante.
"""

from __future__ import annotations

from arq.connections import RedisSettings

# Default do ARQ é 1 segundo, suficiente pra Redis local mas curto demais
# pra Upstash em GCP us-east4: ~120ms RTT de SP + TLS handshake (3-4 RTTs
# pra cert/key exchange) gasta ~600-900ms só pra abrir a conexão. Em
# reconnects (idle drops do Upstash), o caminho inteiro pode levar +20s.
# 30s dá margem confortável sem segurar muito em caso de outage real.
DEFAULT_CONN_TIMEOUT_SECONDS = 30

# Default do ARQ é 5 retries com 1s entre eles. Aumentamos pra absorver
# blips intermitentes de rede sem propagar exceção pro chamador (no caso
# do worker, propagar mata o container e o Cloud Run reinicia).
DEFAULT_CONN_RETRIES = 10
DEFAULT_CONN_RETRY_DELAY_SECONDS = 2


def build_redis_settings(redis_url: str) -> RedisSettings:
    """Cria ``RedisSettings`` a partir de DSN, com timeouts seguros pra TLS."""
    settings = RedisSettings.from_dsn(redis_url)
    settings.conn_timeout = DEFAULT_CONN_TIMEOUT_SECONDS
    settings.conn_retries = DEFAULT_CONN_RETRIES
    settings.conn_retry_delay = DEFAULT_CONN_RETRY_DELAY_SECONDS
    return settings
