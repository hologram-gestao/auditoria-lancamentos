"""Unit — enum fechado e whitelist de `props` da instrumentação (BACK 04.1).

Estes testes não tocam DB: exercitam a BORDA de validação, que é onde a
promessa "nenhum evento grava PII" é cumprida. Se um campo de texto livre for
adicionado a um `props` no futuro, `test_nenhum_props_aceita_texto_livre`
falha — a promessa vira invariante testada, não comentário.
"""

from __future__ import annotations

import typing
from typing import Any, Literal, get_args, get_origin

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.modules.usage_events.schemas import (
    CLIENT_EMITTED_EVENTS,
    AutorNavegouForaProps,
    AutorNavegouForaRequest,
    NotificacaoEntregueProps,
    NotificacaoEntregueRequest,
    UsageEventName,
    UsageEventRequest,
)

_ADAPTER: TypeAdapter[Any] = TypeAdapter(UsageEventRequest)
_SESSION_ID = "3f7b1e2a-0000-4000-8000-000000000001"

_PROPS_MODELS: list[type[BaseModel]] = [AutorNavegouForaProps, NotificacaoEntregueProps]


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
        """Guardrail estrutural anti-PII: todo campo é `int` ou `Literal`.

        É por isso que "sem PII" não depende de revisão manual: não existe campo
        onde um nome, CNPJ ou descrição de lançamento caberia.
        """
        for name, field in model.model_fields.items():
            annotation = field.annotation
            origin = get_origin(annotation)
            is_literal = origin is Literal or origin is typing.Literal
            assert annotation is int or is_literal, (
                f"{model.__name__}.{name} aceita tipo livre ({annotation!r}) — "
                "campo de texto no sink é porta de entrada de PII."
            )
