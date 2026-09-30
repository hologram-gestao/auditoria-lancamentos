"""Schemas do POST público de leads (86e3fr9ut).

Princípios:
    - Os limites são os das COLUNAS (`db/models/lead.py`), importados, nunca repetidos
      (§7 Backend do primer). Um teste unitário amarra as duas fontes.
    - `consent` é `Literal[True]`: sem consentimento não há lead, e o 400 genérico
      do handler global responde (forma inválida).
    - `website` é o HONEYPOT: um campo que pessoa nenhuma vê nem preenche. Ele aceita
      texto (com teto) para que o bot que o preenche receba a MESMA resposta de
      sucesso de quem enviou de verdade. Recusá-lo com 400 ensinaria o bot.
    - Campo opcional vazio (ou só espaços) vira `None`: "não informado" tem uma forma só.
    - A resposta não ecoa nada do que foi enviado.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
)

from app.db.models.lead import (
    LEAD_COMPANY_MAX,
    LEAD_EMAIL_MAX,
    LEAD_MESSAGE_MAX,
    LEAD_NAME_MAX,
    LEAD_WHATSAPP_MAX,
)

#: Versão do texto de consentimento exibido no formulário. Mudou o texto na landing
#: (`apps/web/src/components/landing/content.ts`), muda aqui e lá, na mesma entrega.
CONSENT_TEXT_VERSION = "2026-09-29"

LEAD_NAME_MIN = 2

#: Teto do honeypot: só impede payload patológico. Não é um limite do formulário.
HONEYPOT_MAX = 200

#: WhatsApp aceita só dígitos, `+`, espaço, parênteses e hífen (o que as pessoas
#: digitam num telefone). Nada de letra: o campo não carrega texto livre.
WHATSAPP_PATTERN = r"^[0-9+() \-]+$"


def _blank_to_none(value: object) -> object:
    """`""`/espaços → `None`; texto com conteúdo → aparado nas pontas."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


def _bool_only(value: object) -> object:
    """Recusa o que não for booleano ANTES do `Literal[True]` (que aceitaria `1`)."""
    if not isinstance(value, bool):
        msg = "consentimento precisa ser booleano"
        raise ValueError(msg)  # Pydantic só converte ValueError em erro de campo
    return value


# O teto vai no `str` de DENTRO da união: aplicado à união inteira, ele tentaria medir
# o `None` que o `_blank_to_none` devolve para campo vazio.
_OptionalCompany = Annotated[
    Annotated[str, StringConstraints(max_length=LEAD_COMPANY_MAX)] | None,
    BeforeValidator(_blank_to_none),
]
_OptionalWhatsapp = Annotated[
    Annotated[str, StringConstraints(max_length=LEAD_WHATSAPP_MAX, pattern=WHATSAPP_PATTERN)]
    | None,
    BeforeValidator(_blank_to_none),
]
_OptionalMessage = Annotated[
    Annotated[str, StringConstraints(max_length=LEAD_MESSAGE_MAX)] | None,
    BeforeValidator(_blank_to_none),
]


class LeadCreate(BaseModel):
    """Body de POST /api/v1/leads."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=LEAD_NAME_MIN, max_length=LEAD_NAME_MAX
        ),
    ]
    email: EmailStr
    company: _OptionalCompany = None
    whatsapp: _OptionalWhatsapp = None
    message: _OptionalMessage = None
    # No modo lax o Pydantic aceita `1` como `True` (e `strict` não se aplica a
    # `Literal`). Consentimento é o booleano `true` do checkbox, nada parecido com ele.
    consent: Annotated[Literal[True], BeforeValidator(_bool_only)]
    website: Annotated[str, Field(max_length=HONEYPOT_MAX)] = ""

    @field_validator("email")
    @classmethod
    def _email_fits_the_column(cls, value: str) -> str:
        # O `email-validator` já recusa acima de 254, mas a coluna é quem manda: se
        # um dia a lib afrouxar, o teto continua sendo o da coluna, não um 500.
        if len(value) > LEAD_EMAIL_MAX:
            msg = "e-mail acima do tamanho da coluna"
            raise ValueError(msg)
        return value

    @property
    def is_honeypot_hit(self) -> bool:
        return bool(self.website.strip())


class LeadReceived(BaseModel):
    """Confirmação genérica: igual para lead gravado, honeypot e limite por e-mail."""

    received: Literal[True] = True


class LeadReceivedResponse(BaseModel):
    """Body do 200 de POST /api/v1/leads."""

    data: LeadReceived = Field(default_factory=LeadReceived)
