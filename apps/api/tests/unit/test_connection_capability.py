"""O predicado único de "esta conexão serve?" (Sprint 9, BACK 09.2 — R3).

A taxonomia é fechada em TRÊS, todas 409, e cada uma existe porque o que o
usuário precisa fazer é diferente: conectar · reconectar · nada. Um teste por
código, mais os dois estados que NÃO são capazes (`inativa` e `erro`).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core.exceptions import (
    ErrorCode,
    NoOriginConnectionError,
    OriginCapabilityMissingError,
    OriginConnectionInErrorError,
)
from app.db.models.client_connection import ClientConnection, ConnectionStatus, ProviderType
from app.integrations.providers.base import Capability
from app.modules.client_connections.capability import (
    connection_supports,
    select_capable_connection,
)

CLIENT_ID = uuid4()


def _connection(
    *,
    status: ConnectionStatus = ConnectionStatus.ATIVA,
    provider_type: str = ProviderType.OMIE.value,
    label: str = "Omie",
) -> ClientConnection:
    """Instância ORM SOLTA (sem sessão) — o predicado é pura leitura de campos."""
    return ClientConnection(
        id=uuid4(),
        client_id=CLIENT_ID,
        provider_type=provider_type,
        label=label,
        status=status.value,
    )


class TestSemConexao:
    def test_cliente_sem_nenhuma_conexao(self) -> None:
        with pytest.raises(NoOriginConnectionError) as exc:
            select_capable_connection([], Capability.LISTAR_CONTAS)
        assert exc.value.status_code == 409
        assert exc.value.code == ErrorCode.SEM_CONEXAO
        # A mensagem diz o PRÓXIMO PASSO, não o estado interno.
        assert "conecte" in exc.value.user_message.lower()


class TestOrigemComErro:
    def test_existe_conexao_nenhuma_ativa(self) -> None:
        conexoes = [_connection(status=ConnectionStatus.ERRO)]
        with pytest.raises(OriginConnectionInErrorError) as exc:
            select_capable_connection(conexoes, Capability.LISTAR_CONTAS)
        assert exc.value.status_code == 409
        assert exc.value.code == ErrorCode.ORIGEM_COM_ERRO

    def test_inativa_tambem_nao_e_capaz(self) -> None:
        """Desligada de propósito também não opera — e o diagnóstico é o mesmo."""
        conexoes = [_connection(status=ConnectionStatus.INATIVA)]
        with pytest.raises(OriginConnectionInErrorError):
            select_capable_connection(conexoes, Capability.LISTAR_CONTAS)

    def test_erro_e_inativa_juntas_seguem_nao_capazes(self) -> None:
        conexoes = [
            _connection(status=ConnectionStatus.ERRO, label="Omie A"),
            _connection(status=ConnectionStatus.INATIVA, label="Omie B"),
        ]
        with pytest.raises(OriginConnectionInErrorError):
            select_capable_connection(conexoes, Capability.LISTAR_LANCAMENTOS)


class TestCapacidadeAusente:
    def test_ativa_mas_o_tipo_nao_faz_isso(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Nada a consertar: o provedor conectado simplesmente não oferece a operação.

        Hoje o Omie declara as 4 capacidades, então o caso só existe com um tipo
        que declare menos — o que é exatamente o cenário do 2º provedor. Em vez
        de inventar um adaptador de mentira no `app/`, o registry é apontado
        para um conjunto reduzido só neste teste.
        """
        from app.modules.client_connections import capability as capability_module

        monkeypatch.setattr(
            capability_module,
            "capabilities_for",
            lambda _provider_type: frozenset({Capability.LISTAR_CONTAS}),
        )
        conexoes = [_connection()]
        with pytest.raises(OriginCapabilityMissingError) as exc:
            select_capable_connection(conexoes, Capability.ESCREVER)
        assert exc.value.status_code == 409
        assert exc.value.code == ErrorCode.CAPACIDADE_AUSENTE
        # Não manda reconectar o que já está são.
        assert "reconect" not in exc.value.user_message.lower()


class TestConexaoCapaz:
    def test_ativa_e_capaz_e_devolvida(self) -> None:
        conexao = _connection()
        assert select_capable_connection([conexao], Capability.LISTAR_CONTAS) is conexao

    def test_desempate_e_a_primeira_da_sequencia(self) -> None:
        """A ordenação é de quem consulta o repositório — a escolha fica visível."""
        primeira = _connection(label="Omie — filial")
        segunda = _connection(label="Omie — matriz")
        escolhida = select_capable_connection([primeira, segunda], Capability.LISTAR_CONTAS)
        assert escolhida is primeira

    def test_ignora_as_nao_ativas_e_escolhe_a_ativa(self) -> None:
        inativa = _connection(status=ConnectionStatus.INATIVA, label="Omie velha")
        ativa = _connection(label="Omie nova")
        assert select_capable_connection([inativa, ativa], Capability.ESCREVER) is ativa


class TestConnectionSupports:
    @pytest.mark.parametrize(
        ("status", "esperado"),
        [
            (ConnectionStatus.ATIVA, True),
            (ConnectionStatus.INATIVA, False),
            (ConnectionStatus.ERRO, False),
        ],
    )
    def test_so_ativa_e_capaz(self, status: ConnectionStatus, esperado: bool) -> None:
        assert connection_supports(_connection(status=status), Capability.LISTAR_CONTAS) is esperado
