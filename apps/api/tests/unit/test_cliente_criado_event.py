"""O evento `cliente_criado` — a métrica da Sprint 9 (BACK 09.4).

A leitura D+30 conta:

    count(usage_events)
    WHERE event = 'cliente_criado'
      AND props->>'tem_conexao' = 'false'
      AND props->>'organization_id' = '<org do parceiro>'

Este arquivo trava o que essa consulta precisa encontrar: o nome no enum, as
chaves exatas (nem uma a mais), e a ausência do evento na allow-list de dedup —
sem isso, dois cadastros na mesma janela poderiam virar uma linha só e a
métrica sairia subcontada.

Os três invariantes são de FORMA, não de comportamento: o caminho de emissão
está em `tests/integration/test_client_create_without_origin.py`.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.db.models.usage_event import DEDUPED_EVENT_NAMES
from app.modules.usage_events.schemas import (
    CLIENT_EMITTED_EVENTS,
    ClienteCriadoProps,
    UsageEventName,
)


class TestNomeDoEvento:
    def test_esta_no_enum_fechado(self) -> None:
        assert UsageEventName.CLIENTE_CRIADO.value == "cliente_criado"

    def test_nao_e_emitido_pelo_cliente(self) -> None:
        """O browser não pode forjar o numerador da métrica da sprint (ADR-004)."""
        assert UsageEventName.CLIENTE_CRIADO not in CLIENT_EMITTED_EVENTS

    def test_fica_fora_da_allow_list_de_dedup(self) -> None:
        """Evento novo nasce SEM dedup — entrar na lista exige migration.

        Aqui o "sem dedup" é requisito, não acaso: dois clientes cadastrados na
        mesma janela são DOIS fatos, e colapsá-los subcontaria a métrica.
        """
        assert UsageEventName.CLIENTE_CRIADO.value not in DEDUPED_EVENT_NAMES
        assert DEDUPED_EVENT_NAMES == (
            "autor_navegou_fora",
            "conciliacao_concluida",
            "conciliacao_criada",
            "notificacao_entregue",
        )


class TestWhitelistDeChaves:
    def test_as_quatro_chaves_do_prd(self) -> None:
        props = ClienteCriadoProps(
            client_id=uuid4(),
            organization_id=uuid4(),
            tem_conexao=False,
            tipo_conexao=None,
        )
        assert set(props.model_dump()) == {
            "client_id",
            "organization_id",
            "tem_conexao",
            "tipo_conexao",
        }

    def test_chave_fora_da_whitelist_e_rejeitada_na_borda(self) -> None:
        """`_StrictProps` é `extra="forbid"` — nome de cliente não entra nem por engano."""
        with pytest.raises(ValidationError):
            ClienteCriadoProps(
                client_id=uuid4(),
                organization_id=uuid4(),
                tem_conexao=False,
                tipo_conexao=None,
                nome_do_cliente="Padaria do Bairro",  # type: ignore[call-arg]
            )

    def test_tipo_conexao_e_opcional_e_nulo_no_ramo_sem_origem(self) -> None:
        props = ClienteCriadoProps(client_id=uuid4(), organization_id=uuid4(), tem_conexao=False)
        assert props.tipo_conexao is None

    def test_serializa_em_json_puro(self) -> None:
        """`props` vai para uma coluna JSON — UUID precisa sair como string."""
        dumped = ClienteCriadoProps(
            client_id=uuid4(),
            organization_id=uuid4(),
            tem_conexao=True,
            tipo_conexao="omie",
        ).model_dump(mode="json")
        assert isinstance(dumped["client_id"], str)
        assert isinstance(dumped["organization_id"], str)
        assert dumped["tem_conexao"] is True
        assert dumped["tipo_conexao"] == "omie"
