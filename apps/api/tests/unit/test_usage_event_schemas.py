"""Unit — enum fechado e whitelist de `props` da instrumentação (BACK 04.1).

Estes testes não tocam DB: exercitam a BORDA de validação, que é onde a
promessa "nenhum evento grava PII" é cumprida. Se um campo de texto livre for
adicionado a um `props` no futuro, `test_nenhum_props_aceita_texto_livre`
falha — a promessa vira invariante testada, não comentário.
"""

from __future__ import annotations

import importlib.util
import typing
from pathlib import Path
from typing import Any, Literal, get_args, get_origin
from uuid import UUID

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.db.models.usage_event import (
    DEDUPED_EVENT_NAMES,
    deduped_session_index_predicate,
)
from app.modules.reconciliations.qualification.schemas import SemanticStatus
from app.modules.usage_events.schemas import (
    CLIENT_EMITTED_EVENTS,
    AutorNavegouForaProps,
    AutorNavegouForaRequest,
    FlagRevisadoProps,
    GlossarioEditadoProps,
    NotificacaoEntregueProps,
    NotificacaoEntregueRequest,
    QualificacaoEmitidaProps,
    QualificationVerdict,
    UsageEventName,
    UsageEventRequest,
)

_ADAPTER: TypeAdapter[Any] = TypeAdapter(UsageEventRequest)
_SESSION_ID = "3f7b1e2a-0000-4000-8000-000000000001"
_CLIENT_ID = "3f7b1e2a-0000-4000-8000-0000000000c1"

_PROPS_MODELS: list[type[BaseModel]] = [
    AutorNavegouForaProps,
    NotificacaoEntregueProps,
    QualificacaoEmitidaProps,
    FlagRevisadoProps,
    GlossarioEditadoProps,
]

#: Tipos que NÃO carregam texto livre. `bool`/`int` são grandezas, `Literal` é
#: enum fechado e `UUID` é identificador opaco — nenhum deles comporta nome,
#: CNPJ, descrição de lançamento ou motivo da IA.
_PII_SAFE_SCALARS = (int, bool, UUID)


def _valid(event: str, props: dict[str, Any]) -> dict[str, Any]:
    return {"event": event, "session_id": _SESSION_ID, "props": props}


class TestEnumFechado:
    def test_eventos_do_cliente_sao_apenas_os_dois_observaveis_no_browser(self) -> None:
        assert {
            UsageEventName.AUTOR_NAVEGOU_FORA,
            UsageEventName.NOTIFICACAO_ENTREGUE,
        } == CLIENT_EMITTED_EVENTS

    def test_union_aceita_exatamente_os_eventos_do_cliente(self) -> None:
        """O request só modela os eventos de cliente — os de backend ficam fora."""
        modelados = {
            AutorNavegouForaRequest.model_fields["event"].annotation,
            NotificacaoEntregueRequest.model_fields["event"].annotation,
        }
        valores = {get_args(ann)[0] for ann in modelados}
        assert valores == {e.value for e in CLIENT_EMITTED_EVENTS}

    @pytest.mark.parametrize(
        "event",
        [
            UsageEventName.CONCILIACAO_CRIADA.value,
            UsageEventName.CONCILIACAO_CONCLUIDA.value,
            "evento_inventado",
            "",
        ],
    )
    def test_evento_fora_do_enum_do_cliente_e_rejeitado(self, event: str) -> None:
        with pytest.raises(ValidationError):
            _ADAPTER.validate_python(_valid(event, {"segundos_apos_criar": 1}))


class TestWhitelistDeProps:
    def test_caminho_feliz_autor_navegou_fora(self) -> None:
        parsed = _ADAPTER.validate_python(
            _valid(UsageEventName.AUTOR_NAVEGOU_FORA.value, {"segundos_apos_criar": 42})
        )
        assert parsed.props.segundos_apos_criar == 42

    def test_caminho_feliz_notificacao_entregue(self) -> None:
        parsed = _ADAPTER.validate_python(
            _valid(
                UsageEventName.NOTIFICACAO_ENTREGUE.value,
                {"via": "toast", "latencia_s": 7},
            )
        )
        assert parsed.props.via == "toast"

    @pytest.mark.parametrize(
        "props",
        [
            pytest.param(
                {"segundos_apos_criar": 5, "destinatario": "fulano@x.com"},
                id="chave-extra-com-pii",
            ),
            pytest.param({"segundos_apos_criar": -1}, id="negativo"),
            pytest.param({"segundos_apos_criar": 999_999_999}, id="acima-do-teto"),
            pytest.param({}, id="sem-chave-obrigatoria"),
        ],
    )
    def test_props_invalido_e_rejeitado(self, props: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            _ADAPTER.validate_python(_valid(UsageEventName.AUTOR_NAVEGOU_FORA.value, props))

    def test_campo_extra_no_corpo_e_rejeitado(self) -> None:
        """`client_id` não vem do cliente — o servidor resolve pelo `session_id`."""
        body = _valid(UsageEventName.AUTOR_NAVEGOU_FORA.value, {"segundos_apos_criar": 1})
        body["client_id"] = "qualquer-coisa"
        with pytest.raises(ValidationError):
            _ADAPTER.validate_python(body)

    def test_via_fora_do_enum_e_rejeitado(self) -> None:
        with pytest.raises(ValidationError):
            _ADAPTER.validate_python(
                _valid(
                    UsageEventName.NOTIFICACAO_ENTREGUE.value,
                    {"via": "email", "latencia_s": 1},
                )
            )

    @pytest.mark.parametrize("model", _PROPS_MODELS, ids=lambda m: m.__name__)
    def test_nenhum_props_aceita_texto_livre(self, model: type[BaseModel]) -> None:
        """Guardrail estrutural anti-PII: todo campo é escalar fechado ou `Literal`.

        É por isso que "sem PII" não depende de revisão manual: não existe campo
        onde um nome, CNPJ ou descrição de lançamento caberia.
        """
        for name, field in model.model_fields.items():
            annotation = field.annotation
            origin = get_origin(annotation)
            is_literal = origin is Literal or origin is typing.Literal
            assert annotation in _PII_SAFE_SCALARS or is_literal, (
                f"{model.__name__}.{name} aceita tipo livre ({annotation!r}) — "
                "campo de texto no sink é porta de entrada de PII."
            )


class TestEventosDaSprint6:
    """BACK 06.1 — vocabulário fechado dos 3 eventos do experimento de glossário."""

    def test_nomes_literais_do_prd(self) -> None:
        """Os nomes vêm do PRD e não podem ser renomeados sem quebrar a leitura D+30."""
        assert UsageEventName.QUALIFICACAO_EMITIDA.value == "qualificacao_emitida"
        assert UsageEventName.FLAG_REVISADO.value == "flag_revisado"
        assert UsageEventName.GLOSSARIO_EDITADO.value == "glossario_editado"

    @pytest.mark.parametrize(
        ("model", "esperado"),
        [
            (QualificacaoEmitidaProps, {"veredito", "com_glossario"}),
            (FlagRevisadoProps, {"procedente"}),
            (GlossarioEditadoProps, {"client_id", "n_categorias"}),
        ],
        ids=["qualificacao_emitida", "flag_revisado", "glossario_editado"],
    )
    def test_props_tem_exatamente_os_campos_declarados(
        self, model: type[BaseModel], esperado: set[str]
    ) -> None:
        """`session_id` é COLUNA da tabela, não chave de `props` (ver docstring do módulo)."""
        assert set(model.model_fields) == esperado

    @pytest.mark.parametrize(
        ("model", "base", "extra"),
        [
            (
                QualificacaoEmitidaProps,
                {"veredito": "suspeita", "com_glossario": True},
                {"motivo": "IOF classificado como juros"},
            ),
            (
                FlagRevisadoProps,
                {"procedente": False},
                {"descricao": "PAG PIX MOINHO PRADO"},
            ),
            (
                GlossarioEditadoProps,
                {"client_id": _CLIENT_ID, "n_categorias": 12},
                {"razao_social": "Austral Ltda"},
            ),
            (
                GlossarioEditadoProps,
                {"client_id": _CLIENT_ID, "n_categorias": 12},
                {"cnpj": "12.345.678/0001-90"},
            ),
        ],
        ids=["motivo", "descricao", "razao_social", "cnpj"],
    )
    def test_campo_de_texto_livre_e_rejeitado(
        self, model: type[BaseModel], base: dict[str, Any], extra: dict[str, Any]
    ) -> None:
        """`extra="forbid"` na EMISSÃO: PII não chega nem a virar `props`."""
        assert model(**base) is not None  # o base sozinho é válido
        with pytest.raises(ValidationError):
            model(**base, **extra)

    def test_veredito_fora_do_enum_e_rejeitado(self) -> None:
        with pytest.raises(ValidationError):
            QualificacaoEmitidaProps(veredito="talvez", com_glossario=False)  # type: ignore[arg-type]

    def test_n_categorias_negativo_e_rejeitado(self) -> None:
        with pytest.raises(ValidationError):
            GlossarioEditadoProps(client_id=UUID(_CLIENT_ID), n_categorias=-1)

    def test_veredito_espelha_o_enum_da_qualificacao(self) -> None:
        """Se a Camada 1 ganhar um 4º status, este teste força atualizar o sink."""
        assert set(get_args(QualificationVerdict)) == set(get_args(SemanticStatus))

    @pytest.mark.parametrize(
        "event",
        [
            UsageEventName.QUALIFICACAO_EMITIDA,
            UsageEventName.FLAG_REVISADO,
            UsageEventName.GLOSSARIO_EDITADO,
        ],
        ids=lambda e: e.value,
    )
    def test_nao_sao_aceitos_do_browser(self, event: UsageEventName) -> None:
        """Aceitar do cliente deixaria forjar numerador E denominador da métrica."""
        assert event not in CLIENT_EMITTED_EVENTS
        with pytest.raises(ValidationError):
            _ADAPTER.validate_python(_valid(event.value, {"procedente": False}))


class TestAllowListDeDedup:
    """ADR-010 — o índice parcial só dedup a eventos de grão "1 por sessão"."""

    def test_deduped_event_names_existem_no_enum(self) -> None:
        """As strings do modelo são literais (evitar ciclo de import) — trava o drift."""
        assert set(DEDUPED_EVENT_NAMES) <= {e.value for e in UsageEventName}

    @pytest.mark.parametrize(
        "event",
        [
            UsageEventName.QUALIFICACAO_EMITIDA,
            UsageEventName.FLAG_REVISADO,
            UsageEventName.GLOSSARIO_EDITADO,
        ],
        ids=lambda e: e.value,
    )
    def test_eventos_multi_ocorrencia_ficam_fora_da_dedup(self, event: UsageEventName) -> None:
        """Se um deles entrar na allow-list, N-1 emissões somem e a razão sai errada."""
        assert event.value not in DEDUPED_EVENT_NAMES

    def test_ordem_alfabetica_fixa(self) -> None:
        """A ordem entra no predicado do índice — mudar quebra a inferência do ON CONFLICT."""
        assert list(DEDUPED_EVENT_NAMES) == sorted(DEDUPED_EVENT_NAMES)

    def test_predicado_bate_com_o_snapshot_da_migration(self) -> None:
        """Modelo, `ON CONFLICT` e migration precisam da MESMA string (senão 42P10).

        A migration é carregada pelo CAMINHO: `alembic/versions/` não é pacote
        importável (e `import alembic` resolveria para a lib instalada).
        """
        path = (
            Path(__file__).resolve().parents[2]
            / "alembic"
            / "versions"
            / "a1d7f36c9b52_s6_usage_events_dedup_allowlist.py"
        )
        spec = importlib.util.spec_from_file_location("_s6_dedup_migration", path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert deduped_session_index_predicate() == module._NEW_PREDICATE
