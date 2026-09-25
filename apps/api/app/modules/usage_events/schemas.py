"""Schemas Pydantic da instrumentação de outcome (Sprint 4, BACK 04.1).

Aqui vive o **enum FECHADO** de eventos e a **whitelist de chaves de `props` por
evento**. Os dois são a defesa contra dois riscos concretos:

1. **Lixo na métrica** — `event` fora do enum é 422; a leitura D+30 não precisa
   adivinhar o que é ruído.
2. **PII no sink** — nenhum `props` tem campo de texto livre. Todo campo é `int`
   (contadores/durações), `Literal` (enum) ou UUID. Com `extra="forbid"`, chave
   desconhecida vira 422 antes de chegar ao banco: não existe caminho pelo qual
   uma descrição de lançamento, CNPJ ou nome de destinatário entre na tabela.

Origem dos campos: seção `Instrumentação` do `## Outcome & verificação`
(CONTEXT.md). `duracao_s`/`latencia_s`/`segundos_apos_criar` são **inteiros**
(CLAUDE.md §3.4 — nunca float).

**Grão do evento (Sprint 6).** `session_id` é COLUNA da tabela, não chave de
`props` — o PRD declara `qualificacao_emitida {session_id, veredito,
com_glossario}`, e a sessão entra pela coluna exatamente como já acontece com
`conciliacao_criada` (declarado `{session_id, client_id, n_arquivos,
criado_por}`, gravado com `session_id` na coluna). Quem dedup a por sessão é o
índice parcial; a lista de eventos que aceitam essa dedup vive em
`app.db.models.usage_event.DEDUPED_EVENT_NAMES` (ver ADR-010).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

#: Vereditos possíveis da qualificação (Camada 1). Espelha `SemanticStatus` de
#: `app.modules.reconciliations.qualification.schemas` — importar de lá criaria
#: uma dependência do sink de métrica no módulo de conciliação; a consistência
#: entre os dois é travada por teste unitário.
QualificationVerdict = Literal["ok", "suspeita", "incoerente"]


class UsageEventName(StrEnum):
    """Lista FECHADA de eventos. Nenhum evento nasce fora daqui."""

    # Emitidos pelo BACKEND, no ponto real do fluxo.
    CONCILIACAO_CRIADA = "conciliacao_criada"
    CONCILIACAO_CONCLUIDA = "conciliacao_concluida"
    # Emitidos pelo FRONTEND (só o browser observa navegação e entrega visual).
    NOTIFICACAO_ENTREGUE = "notificacao_entregue"
    AUTOR_NAVEGOU_FORA = "autor_navegou_fora"
    # Sprint 6 (BACK 06.1) — experimento do glossário. Todos de BACKEND: aceitar
    # `qualificacao_emitida`/`flag_revisado` do browser deixaria o cliente forjar
    # numerador E denominador da métrica de outcome (mesmo raciocínio do item 4
    # da ADR-004).
    QUALIFICACAO_EMITIDA = "qualificacao_emitida"
    FLAG_REVISADO = "flag_revisado"
    GLOSSARIO_EDITADO = "glossario_editado"
    # Sprint 7 (BACK 07.5) — lançamento no Omie. Também de BACKEND: aceitá-los
    # do browser deixaria o cliente forjar numerador (`sucesso`) E denominador
    # (`linhas`) da métrica da sprint.
    OMIE_LANCAMENTO_ENVIADO = "omie_lancamento_enviado"
    OMIE_LANCAMENTO_REJEITADO = "omie_lancamento_rejeitado"
    # 86e34jd1d — exclusão definitiva de cliente. De BACKEND (o fato é do
    # servidor); sem `session_id`, fora da dedup por construção: cada exclusão
    # é uma linha.
    CLIENTE_EXCLUIDO = "cliente_excluido"
    CLIENTE_ENCERRADO = "cliente_encerrado"
    # Camada de organizações (86e36ecnp) — administração da plataforma. De
    # BACKEND; sem `session_id`, fora da dedup por construção: cada cadastro e
    # cada suspensão é uma linha. Só IDs e contagens — nunca o nome do BPO.
    ORGANIZACAO_CRIADA = "organizacao_criada"
    ORGANIZACAO_DESATIVADA = "organizacao_desativada"
    # 86e3bvbfx — a plataforma transferiu um staff de organização. De BACKEND,
    # sem `session_id`, fora da dedup: cada transferência é uma linha. Só IDs e
    # contagens do que foi removido — nunca nome de pessoa nem de organização.
    USUARIO_TRANSFERIDO_DE_ORGANIZACAO = "usuario_transferido_de_organizacao"
    # Sprint 9 (BACK 09.4) — **a métrica da sprint**. De BACKEND, sem
    # `session_id`, fora da dedup por construção: cada cadastro é uma linha.
    #
    # Fórmula da leitura D+30:
    #     count(usage_events)
    #     WHERE event = 'cliente_criado'
    #       AND props->>'tem_conexao' = 'false'
    #       AND props->>'organization_id' = '<id da organização do parceiro>'
    #
    # O id da organização é **parâmetro da leitura**, resolvido na hora (a org
    # do escritório parceiro é criada na operação, não aqui). Baseline 0: até
    # esta sprint era impossível criar cliente sem credencial. Só IDs e enums.
    CLIENTE_CRIADO = "cliente_criado"
    # Sprint 10 (BACK 10.4) — **a métrica da Sprint 10**. De BACKEND, sem
    # `session_id`, fora da dedup por construção: cada sincronização é uma
    # linha, e é isso que a fórmula exige (ver abaixo).
    #
    # Fórmula da leitura D+30:
    #     com_destino ÷ ativas
    # lidos da **ÚLTIMA** linha por `client_id` no período — um cliente que
    # sincroniza 30 vezes gera 30 linhas, e a leitura usa a mais recente. Se o
    # evento entrasse na allow-list de dedup, a 2ª sincronização em diante
    # sumiria e a leitura mediria a foto do primeiro dia para sempre.
    #
    # Baseline **0%**: não porque não se lia categoria (`listar_categorias`
    # existe desde a Sprint 7), mas porque **nada era persistido com destino** —
    # o vínculo com a conta de demonstrativo não era sequer declarado no DTO.
    # Alvo: ≥ 70% das ATIVAS com destino.
    #
    # ⚠️ `com_destino` e `com_conta_contabil` são contados sobre as **ATIVAS**,
    # a mesma base de `ativas` — é o que faz a fórmula fechar. `total_categorias`
    # é o conjunto inteiro (inclui inativas e `ausente_na_origem`) e serve de
    # contexto, não de denominador.
    PLANO_CONTAS_SINCRONIZADO = "plano_contas_sincronizado"
    # Sprint 11 (BACK 11.3) — **a métrica da Sprint 11**. De BACKEND, sem
    # `session_id`, fora da dedup por construção: cada sincronização da carteira
    # é uma linha, e é isso que a fórmula exige (ver abaixo).
    #
    # Fórmula da leitura D+30 (cobertura da carteira):
    #     títulos em aberto persistidos ÷ títulos em aberto que a origem devolve
    #     numa consulta ampla de conferência
    # lidos da **ÚLTIMA** linha por `client_id` no período — o numerador sai de
    # `titulos_pagar + titulos_receber`, e o denominador é a conferência manual
    # do QA contra a origem (não há como o servidor saber o que a origem tem
    # sem consultá-la de novo). Um cliente que sincroniza 30 vezes gera 30
    # linhas; se o evento entrasse na allow-list de dedup, a 2ª sincronização em
    # diante sumiria e a leitura mediria a foto do primeiro dia para sempre.
    #
    # Baseline **0%**: nenhum título em aberto é persistido COMO TÍTULO hoje. O
    # que existe são divergências de conciliação, presas a uma sessão de conta e
    # mês (`reconciliation_omie_entries`, verificado em 21/09/2026). Alvo: 100%
    # dos títulos em aberto do cliente, em todas as contas — previsão declarada,
    # e o único número honesto, porque carteira parcial produz aging errado, que
    # é pior que aging nenhum.
    CARTEIRA_SINCRONIZADA = "carteira_sincronizada"
    # Sprint 15 (BACK 15.1) — instrumentação de R1. De BACKEND, sem
    # `session_id`, fora da dedup por construção: cada registro de contexto é
    # uma linha (o mesmo cliente pode registrar vários no mesmo título).
    #
    # Conta REGISTROS, não o universo: é o `S-1` do PRD ("quem atende registra
    # espontaneamente?") — quem mede a COBERTURA sobre o vencido é o outro
    # evento da sprint, `recebiveis_classificados` (BACK 15.2).
    CONTEXTO_TITULO_REGISTRADO = "contexto_titulo_registrado"
    # Sprint 15 (BACK 15.2) — **a métrica da Sprint 15**. De BACKEND, sem
    # `session_id`, fora da dedup por construção: cada cálculo do relatório é
    # uma linha (o mesmo cliente pode abrir o relatório várias vezes).
    #
    # Fórmula da leitura D+30:
    #     valor_sem_contexto_centavos ÷ valor_vencido_total_centavos
    # lidos da **ÚLTIMA** linha por `client_id` no período — abrir o relatório
    # 30 vezes precisa gerar 30 linhas, senão a leitura mediria a foto do
    # primeiro cálculo para sempre.
    #
    # Baseline **100%**: hoje todo título vencido conta como inadimplência,
    # porque não existe onde registrar o contrário (verificado em 21/09/2026).
    RECEBIVEIS_CLASSIFICADOS = "recebiveis_classificados"


#: Eventos que o `POST /api/v1/usage-events` aceita. Os de backend ficam de fora
#: DE PROPÓSITO: `conciliacao_criada`/`conciliacao_concluida` são o denominador e
#: o marco temporal da métrica — aceitá-los do cliente permitiria forjar o
#: resultado da sprint. Quem os emite é o servidor, no ponto real do fluxo.
#: O mesmo vale para os 3 eventos da Sprint 6 (`qualificacao_emitida`,
#: `flag_revisado`, `glossario_editado`): a lista abaixo é allow-list, então
#: eles já nascem recusados pelo endpoint (422 na union discriminada).
CLIENT_EMITTED_EVENTS: frozenset[UsageEventName] = frozenset(
    {UsageEventName.NOTIFICACAO_ENTREGUE, UsageEventName.AUTOR_NAVEGOU_FORA}
)

# Teto sanitário das durações: 30 dias em segundos. Não é regra de negócio — é
# guardrail contra valor absurdo (relógio do cliente errado, aba aberta por
# semanas) contaminando a média da leitura D+30.
_MAX_SECONDS = 30 * 24 * 60 * 60


class _StrictProps(BaseModel):
    """Base de todo `props`: chave desconhecida é erro, não é ignorada."""

    model_config = ConfigDict(extra="forbid")


class AutorNavegouForaProps(_StrictProps):
    """`autor_navegou_fora` — **é o evento que prova o outcome da sprint**."""

    segundos_apos_criar: int = Field(ge=0, le=_MAX_SECONDS)


class NotificacaoEntregueProps(_StrictProps):
    """`notificacao_entregue` — mede se a notificação ALCANÇA a pessoa."""

    via: Literal["sino", "toast"]
    latencia_s: int = Field(ge=0, le=_MAX_SECONDS)


# ----------------------------------------------------------------------
# Sprint 6 (BACK 06.1) — props do experimento de glossário
#
# Os 3 modelos abaixo são a fonte ÚNICA do shape de cada evento: os emissores
# do service constroem por eles (nunca `dict` solto), então a garantia
# `extra="forbid"` + "só enum/bool/int/UUID" vale na EMISSÃO, não só na borda
# HTTP — e nenhum deles tem campo de texto livre onde motivo/descrição/nome de
# fornecedor/categoria/CNPJ caberia (CLAUDE.md §3.3 e §4.7).
# ----------------------------------------------------------------------


class QualificacaoEmitidaProps(_StrictProps):
    """`qualificacao_emitida` — DENOMINADOR da métrica da Sprint 6.

    Uma linha por veredito emitido pela Camada 1 da qualificação. `veredito`
    é o enum fechado da IA; `com_glossario` diz se o bloco de system do
    glossário do cliente foi injetado naquela análise (BACK 06.4) — é o que
    permite comparar o "antes" e o "depois" do mesmo cliente.
    """

    veredito: QualificationVerdict
    com_glossario: bool


class FlagRevisadoProps(_StrictProps):
    """`flag_revisado` — NUMERADOR da métrica: o revisor julgou o flag.

    `procedente=False` é um falso positivo confirmado pela revisão — a
    grandeza que a Sprint 6 quer derrubar em ≥30%.
    """

    procedente: bool


class ClienteExcluidoProps(_StrictProps):
    """`cliente_excluido` (86e34jd1d) — o que foi levado junto, em contagens.

    Só IDs e inteiros: nome do cliente é dado identificável e NÃO entra (§4.7).
    """

    client_id: UUID
    n_conciliacoes: int = Field(ge=0)
    n_usuarios: int = Field(ge=0)


class ClienteEncerradoProps(_StrictProps):
    """`cliente_encerrado` (86e36pm1z) — encerramento com retenção.

    Contrapartida do `cliente_excluido`: aqui as conciliações FICAM e os
    usuários são anonimizados (não apagados). Mesmas contagens, só IDs e
    inteiros — nome do cliente é identificável e não entra (§4.7). Evento novo
    nasce SEM dedup (allow-list — entrar nela exige migration).
    """

    client_id: UUID
    n_conciliacoes: int = Field(ge=0)
    n_usuarios: int = Field(ge=0)


class ClienteCriadoProps(_StrictProps):
    """`cliente_criado` (S9 BACK 09.4) — o numerador da métrica da Sprint 9.

    `tem_conexao` é o que a leitura D+30 filtra: `false` é exatamente o caso que
    a sprint existe para tornar possível (cliente pleno sem nenhuma origem).
    `tipo_conexao` é `None` nesse ramo e o tipo do provedor no outro — enum
    fechado do servidor, nunca texto livre.

    `organization_id` (e não o nome do BPO) porque a fórmula agrupa por
    organização e **nome é dado identificável** (§4.7). `client_id` entra pelo
    mesmo motivo dos irmãos `cliente_excluido`/`cliente_encerrado`: dá para
    reconstituir a sequência de um cliente sem guardar quem ele é.
    """

    client_id: UUID
    organization_id: UUID
    tem_conexao: bool
    tipo_conexao: str | None = None


class PlanoContasSincronizadoProps(_StrictProps):
    """`plano_contas_sincronizado` (S10 BACK 10.4) — **a métrica da Sprint 10**.

    Só contagens e o id do tenant. **Nenhum nome de categoria**, nenhum código
    de categoria, nenhum texto — nem sequer uma lista de códigos, que
    reconstituiria o desenho contábil do cliente dentro do sink de métrica.
    `extra="forbid"` do `_StrictProps` garante que chave a mais é erro, não
    campo silencioso.

    A fórmula da leitura D+30 é `com_destino ÷ ativas`, então os dois vêm da
    MESMA base ativa. `total_categorias` é o conjunto inteiro (com inativas e
    ausentes na origem) e serve de contexto: um `ativas` que cai sem
    `total_categorias` cair é categoria sendo desativada, não sumindo.

    `client_id` é PROP, e não coluna: `usage_events` só tem coluna de
    `session_id`, e uma sincronização de plano de contas não pertence a
    conciliação nenhuma. Mesmo lugar em que `glossario_editado` e
    `cliente_criado` gravam o tenant deles.
    """

    client_id: UUID
    total_categorias: int = Field(ge=0)
    ativas: int = Field(ge=0)
    com_destino: int = Field(ge=0)
    com_conta_contabil: int = Field(ge=0)


class CarteiraSincronizadaProps(_StrictProps):
    """`carteira_sincronizada` (S11 BACK 11.3) — **a métrica da Sprint 11**.

    As **cinco** chaves declaradas no PRD, e nenhuma a mais. Só contagens, um
    número de dias e o id do tenant: **nenhum nome de devedor**, nenhum código de
    fornecedor, nenhum identificador de título, nem sequer uma lista de códigos —
    que reconstituiria a carteira de cobranças do cliente dentro do sink de
    métrica. O `extra="forbid"` do `_StrictProps` garante que chave a mais é erro,
    não campo silencioso.

    `mais_antigo_dias` é o **maior atraso** da carteira, em dias, e **`0`
    significa "nada vencido"** — inclusive na carteira vazia. É informação
    honesta, não ausência de dado: quem precisa distinguir "carteira vazia" de
    "nada vencido" soma `titulos_pagar + titulos_receber`, e quem precisa
    distinguir as duas de "nunca sincronizou" olha `clients.titles_synced_at`,
    que existe exatamente para isso (§R3).

    `client_id` é PROP, e não coluna: `usage_events` só tem coluna de
    `session_id`, e uma sincronização de carteira não pertence a conciliação
    nenhuma. Mesmo lugar em que `plano_contas_sincronizado` e `cliente_criado`
    gravam o tenant deles.

    ⚠️ `vencidos` é um SUBCONJUNTO de `titulos_pagar + titulos_receber`, não uma
    terceira parcela: somá-lo ao total daria um número que não significa nada.
    """

    client_id: UUID
    titulos_receber: int = Field(ge=0)
    titulos_pagar: int = Field(ge=0)
    vencidos: int = Field(ge=0)
    mais_antigo_dias: int = Field(ge=0)


#: Vocabulário fechado do tipo de contexto, ESPELHO de `TitleContextType`
#: (`db/models/title_context.py`). `Literal` (e não importar o enum do modelo)
#: pelo mesmo motivo de `QualificationVerdict`: o sink de métrica não depende do
#: módulo de domínio, e a consistência entre os dois é travada por teste.
TitleContextTypeName = Literal[
    "acordo_de_pagamento",
    "pagamento_antecipado",
    "nota_a_cancelar",
    "cobranca_suspensa",
    "perda_provavel",
    "outro",
]


class ContextoTituloRegistradoProps(_StrictProps):
    """`contexto_titulo_registrado` (S15 BACK 15.1) — instrumenta a suposição S-1.

    Só o tenant e o TIPO — nunca o texto livre (é PII em potencial, §4.7) nem o
    identificador do título (reconstituiria a carteira de cobranças dentro do
    sink, mesmo raciocínio de `carteira_sincronizada`).
    """

    client_id: UUID
    tipo_contexto: TitleContextTypeName


class RecebiveisClassificadosProps(_StrictProps):
    """`recebiveis_classificados` (S15 BACK 15.2) — **a métrica da Sprint 15**.

    As **quatro** chaves declaradas no PRD, e nenhuma a mais. Só valores em
    CENTAVOS (`int`, §3.4 — nunca `Decimal`/float no sink), uma contagem e o id
    do tenant: nenhum identificador de título, nenhum código de fornecedor —
    que reconstituiria a carteira de cobranças do cliente dentro do sink de
    métrica, mesmo raciocínio de `carteira_sincronizada`.

    `valor_sem_contexto_centavos` soma os títulos vencidos SEM contexto E os
    com contexto `perda_provavel` (R4) — é o numerador da inadimplência real.
    `valor_vencido_total_centavos` é o denominador, os DOIS grupos somados. Os
    dois cobrem a pagar + a receber juntos: o evento não separa por tipo, a
    RESPOSTA do relatório separa.
    """

    client_id: UUID
    valor_vencido_total_centavos: int = Field(ge=0)
    valor_sem_contexto_centavos: int = Field(ge=0)
    titulos_vencidos: int = Field(ge=0)


class OrganizacaoCriadaProps(_StrictProps):
    """`organizacao_criada` (86e36ecnp) — a plataforma cadastrou um BPO. Só o id."""

    organization_id: UUID


class OrganizacaoDesativadaProps(_StrictProps):
    """`organizacao_desativada` (86e36ecnp) — suspensão, com o que ficou preso.

    `n_usuarios` é o staff que passa a receber 401; `n_clientes` os clientes que
    ficam sem operação. Só IDs e inteiros (§4.7).
    """

    organization_id: UUID
    n_usuarios: int = Field(ge=0)
    n_clientes: int = Field(ge=0)


class UsuarioTransferidoDeOrganizacaoProps(_StrictProps):
    """`usuario_transferido_de_organizacao` (86e3bvbfx) — staff mudou de BPO.

    `n_carteira_removida` são as linhas de `client_assignments` em cliente
    aberto que deixaram de fazer sentido; `n_favoritos_removidos`, os favoritos
    apontando para cliente fora da organização nova. Só IDs e inteiros (§4.7).
    """

    user_id: UUID
    from_organization_id: UUID
    to_organization_id: UUID
    n_carteira_removida: int = Field(ge=0)
    n_favoritos_removidos: int = Field(ge=0)
    n_notificacoes_removidas: int = Field(ge=0)


class GlossarioEditadoProps(_StrictProps):
    """`glossario_editado` — mede a suposição S-1 (alguém mantém o glossário).

    Único dos 3 sem `session_id`: edição de glossário não pertence a uma
    conciliação. O tenant vai em `props.client_id` (mesmo lugar em que
    `conciliacao_criada` grava o seu).
    """

    client_id: UUID
    n_categorias: int = Field(ge=0)


# ----------------------------------------------------------------------
# Sprint 7 (BACK 07.5) — props do lançamento no Omie
#
# ⚠️ **Conflito resolvido aqui, não contornado.** O PRD declara
# `omie_lancamento_rejeitado {codigo, faultstring}`. Mas `faultstring` é TEXTO
# LIVRE vindo do fornecedor, e a whitelist deste módulo proíbe texto livre —
# essa proibição é a única coisa que impede PII de entrar no sink, e a Omie
# ecoa no `faultstring` valores que ENVIAMOS, inclusive o `cObs`, que carrega a
# descrição da compra (§4.5).
#
# Solução: o texto integral **não entra no sink** — ele já volta ao usuário na
# resposta do lote e fica persistido em `reconciliation_omie_postings.
# error_message` (BACK 07.2 / ADR-023-BE), que é onde ele é útil e está sob a
# cripto por cliente. No evento entra uma **categoria derivada**, `Literal`
# fechado, que é o que a leitura D+30 precisa para responder "por que os
# lançamentos estão sendo recusados?". Ver ADR-031-BE.
# ----------------------------------------------------------------------

#: Código canônico do erro (`app.core.exceptions.ErrorCode`) que causou a
#: rejeição. `Literal` fechado — não é `str` livre.
OmieRejectionCode = Literal[
    "OMIE_FAULT",
    "OMIE_AUTH_ERROR",
    "OMIE_TIMEOUT",
]

#: **Família** do erro, derivada do `faultstring` sem carregar o texto.
#: Fechada de propósito: uma família nova exige código novo + teste, o que é
#: exatamente a revisão que um campo de texto livre não teria.
OmieRejectionCategory = Literal[
    "categoria_invalida",
    "conta_invalida",
    "duplicidade",
    "campo_invalido",
    "credencial",
    "indisponibilidade",
    "outro",
]


class OmieLancamentoEnviadoProps(_StrictProps):
    """`omie_lancamento_enviado` — **numerador e denominador da Sprint 7**.

    Uma linha por LOTE executado (não por linha da fatura): a métrica é
    "linhas lançadas ÷ linhas que precisam de lançamento", e o operador manda
    vários lotes na mesma sessão. `session_id` entra pela COLUNA, como já
    acontece com `conciliacao_criada` e `qualificacao_emitida`.

    `duracao_ms` é **inteiro** (CLAUDE.md §3.4 — nunca float para grandeza
    medida) e alimenta o guardrail "o tempo de conciliação não pode subir".
    """

    linhas: int = Field(ge=0)
    sucesso: int = Field(ge=0)
    falha: int = Field(ge=0)
    duracao_ms: int = Field(ge=0)


class OmieLancamentoRejeitadoProps(_StrictProps):
    """`omie_lancamento_rejeitado` — por que a Omie recusou.

    **Sem texto livre.** `codigo` é o `ErrorCode` canônico; `categoria` é a
    família derivada do `faultstring` por `classify_omie_rejection`. O texto
    integral do fornecedor NÃO entra aqui (ver o bloco de comentário acima).
    """

    codigo: OmieRejectionCode
    categoria: OmieRejectionCategory


class _UsageEventRequestBase(BaseModel):
    """Body comum: `session_id` é obrigatório e o ownership é checado no servidor."""

    model_config = ConfigDict(extra="forbid")

    session_id: UUID


class AutorNavegouForaRequest(_UsageEventRequestBase):
    event: Literal["autor_navegou_fora"]
    props: AutorNavegouForaProps


class NotificacaoEntregueRequest(_UsageEventRequestBase):
    event: Literal["notificacao_entregue"]
    props: NotificacaoEntregueProps


#: Union discriminada por `event`. Um `event` fora dos literais acima não casa
#: com nenhum membro → 422 automático do Pydantic (enum fechado sem `if/elif`).
UsageEventRequest = Annotated[
    AutorNavegouForaRequest | NotificacaoEntregueRequest,
    Field(discriminator="event"),
]


class UsageEventPayload(BaseModel):
    """Conteúdo do envelope `{data: ...}` do POST /usage-events.

    `recorded=False` significa que o evento já existia para aquela sessão (a
    idempotência do banco descartou a repetição) — não é erro: o endpoint é
    idempotente por contrato e o front não precisa tratar.
    """

    event: str
    session_id: UUID
    recorded: bool


class UsageEventResponse(BaseModel):
    """Response do POST /api/v1/usage-events."""

    data: UsageEventPayload
