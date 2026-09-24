"""Regra de negócio da instrumentação de outcome (Sprint 4, BACK 04.1).

Duas responsabilidades:

1. **Emitir fail-soft.** Instrumentação NUNCA derruba o fluxo de negócio. Se o
   INSERT falhar (banco indisponível, coluna divergente, o que for), `emit`
   loga e devolve `False` — a conciliação segue. O SAVEPOINT que protege a
   transação de quem chamou está no repository.
2. **Aceitar evento do cliente com RBAC.** `record_client_event` valida que o
   `session_id` pertence a um cliente que o usuário PODE ver
   (`client_assignments`) antes de gravar. O `client_id` nunca vem do corpo.

O log de falha carrega apenas `event` e `session_id` — nunca o `props` inteiro,
nunca o texto da exceção do driver (pode conter fragmento do statement).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.authz import CurrentUser
from app.core.dependencies import require_client_access
from app.core.exceptions import NotFoundError
from app.core.logging import get_logger
from app.modules.reconciliations.tenant_scope import audit_session_tenant_miss
from app.modules.usage_events.omie_rejection import classify_omie_rejection
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.schemas import (
    CarteiraSincronizadaProps,
    ClienteCriadoProps,
    ClienteEncerradoProps,
    ClienteExcluidoProps,
    ContextoTituloRegistradoProps,
    FlagRevisadoProps,
    GlossarioEditadoProps,
    OmieLancamentoEnviadoProps,
    OmieLancamentoRejeitadoProps,
    OrganizacaoCriadaProps,
    OrganizacaoDesativadaProps,
    PlanoContasSincronizadoProps,
    QualificacaoEmitidaProps,
    RecebiveisClassificadosProps,
    UsageEventName,
    UsuarioTransferidoDeOrganizacaoProps,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.modules.usage_events.schemas import (
        AutorNavegouForaRequest,
        NotificacaoEntregueRequest,
        OmieRejectionCode,
        QualificationVerdict,
        TitleContextTypeName,
    )

logger = get_logger(__name__)

_SESSION_NOT_FOUND_MSG = "Sessão de conciliação não encontrada."


class UsageEventService:
    """Emissão de eventos de uso (sink `usage_events`)."""

    def __init__(self, repository: UsageEventRepository) -> None:
        self._repo = repository

    # ------------------------------------------------------------------
    # Emissão genérica (fail-soft)
    # ------------------------------------------------------------------

    async def emit(
        self,
        event: UsageEventName,
        *,
        session_id: UUID | None = None,
        props: dict[str, Any] | None = None,
    ) -> bool:
        """Grava um evento. Devolve `True` se gravou, `False` em qualquer outro caso.

        `False` cobre dois cenários distintos e ambos são normais:
            - duplicata (a UNIQUE parcial descartou — emissor idempotente);
            - falha de gravação (logada como warning; o fluxo de negócio segue).
        """
        try:
            return await self._repo.insert_ignore_duplicate(
                event=event.value,
                session_id=session_id,
                props=props or {},
            )
        except Exception:
            # Sem `exc_info`/`props` no log: instrumentação com defeito não pode
            # virar vazamento. `except: pass` é proibido — daí o warning.
            # A chave é `usage_event`, não `event`: `event` é o nome do campo da
            # MENSAGEM no structlog — colidir vira TypeError dentro do except.
            logger.warning(
                "usage_event_emit_failed",
                usage_event=event.value,
                session_id=str(session_id) if session_id else None,
            )
            return False

    # ------------------------------------------------------------------
    # Emissores do backend (props em UM lugar só — CLAUDE.md: fonte única)
    # ------------------------------------------------------------------

    async def emit_conciliacao_criada(
        self,
        *,
        session_id: UUID,
        client_id: UUID,
        n_arquivos: int,
        criado_por: UUID,
    ) -> bool:
        """Denominador da métrica: emitido ao criar a sessão de conciliação."""
        return await self.emit(
            UsageEventName.CONCILIACAO_CRIADA,
            session_id=session_id,
            props={
                "client_id": str(client_id),
                "n_arquivos": n_arquivos,
                "criado_por": str(criado_por),
            },
        )

    async def emit_conciliacao_concluida(
        self,
        *,
        session_id: UUID,
        duracao_s: int,
        status: str,
    ) -> bool:
        """Marco temporal: emitido ao fim do processamento (reviewing OU error).

        `duracao_s` é `int` (CLAUDE.md §3.4 — nunca float para grandeza medida).
        `status` é o status REAL lido do banco no momento da emissão, não o que o
        job esperava gravar (uma sessão cancelada no meio termina em `error`
        mesmo com o caminho feliz do job chegando ao fim).
        """
        return await self.emit(
            UsageEventName.CONCILIACAO_CONCLUIDA,
            session_id=session_id,
            props={"duracao_s": duracao_s, "status": status},
        )

    # ------------------------------------------------------------------
    # Sprint 6 (BACK 06.1) — emissores do experimento de glossário
    #
    # Os três montam `props` pelo MODELO PYDANTIC correspondente, nunca por
    # `dict` solto: `extra="forbid"` + campos só bool/int/enum/UUID passam a
    # valer na EMISSÃO, não só na borda HTTP. Não existe caminho pelo qual o
    # `motivo` da IA, a descrição de um lançamento ou uma razão social entre
    # no sink (CLAUDE.md §3.3 / §4.7).
    #
    # Nenhum deles é deduplicado pelo banco (ADR-010): `qualificacao_emitida`
    # e `flag_revisado` ocorrem N vezes por sessão, e `glossario_editado` nem
    # tem `session_id`. Cada chamada é uma linha contável.
    # ------------------------------------------------------------------

    async def emit_qualificacao_emitida(
        self,
        *,
        session_id: UUID,
        veredito: QualificationVerdict,
        com_glossario: bool,
    ) -> bool:
        """DENOMINADOR da métrica: um veredito da Camada 1 foi emitido.

        `com_glossario` vem do CALLER (nunca hard-coded aqui): hoje o
        `qualify_session` passa `False` porque o glossário só nasce na BACK
        06.4 — a partir dela, `True` quando o bloco de system do cliente
        realmente entrou no prompt.
        """
        return (
            await self.emit_qualificacao_emitida_many(
                session_id=session_id,
                vereditos=[veredito],
                com_glossario=com_glossario,
            )
            == 1
        )

    async def emit_qualificacao_emitida_many(
        self,
        *,
        session_id: UUID,
        vereditos: Sequence[QualificationVerdict],
        com_glossario: bool,
    ) -> int:
        """Versão em lote do anterior; devolve quantas linhas gravaram.

        Uma qualificação analisa até 50 pares por chamada de IA e emite um
        veredito por par. Em lote isso é UM `INSERT ... VALUES` em vez de N
        round-trips dentro da transação do job (guardrail de latência do PRD).

        Fail-soft igual ao `emit`: qualquer erro vira warning e devolve 0 — a
        transação da qualificação segue viva graças ao SAVEPOINT do repository.
        """
        if not vereditos:
            return 0
        rows = [
            (
                session_id,
                QualificacaoEmitidaProps(veredito=veredito, com_glossario=com_glossario).model_dump(
                    mode="json"
                ),
            )
            for veredito in vereditos
        ]
        try:
            return await self._repo.insert_many_ignore_duplicate(
                event=UsageEventName.QUALIFICACAO_EMITIDA.value,
                rows=rows,
            )
        except Exception:
            logger.warning(
                "usage_event_emit_failed",
                usage_event=UsageEventName.QUALIFICACAO_EMITIDA.value,
                session_id=str(session_id),
            )
            return 0

    async def emit_flag_revisado(self, *, session_id: UUID, procedente: bool) -> bool:
        """NUMERADOR da métrica: o revisor julgou um flag da qualificação.

        Grão = **uma transição de estado da marcação**, não uma requisição
        (ADR-010). Quem garante isso é o call site (BACK 06.5): remarcar com o
        MESMO veredito não chama este emissor, então repetir o PATCH não infla
        o denominador; mudar de procedente↔improcedente chama de novo, então a
        mudança não some.
        """
        return await self.emit(
            UsageEventName.FLAG_REVISADO,
            session_id=session_id,
            props=FlagRevisadoProps(procedente=procedente).model_dump(mode="json"),
        )

    async def emit_glossario_editado(self, *, client_id: UUID, n_categorias: int) -> bool:
        """Suposição S-1: alguém de fato mantém o glossário do cliente.

        Sem `session_id` (edição de glossário não pertence a uma conciliação),
        logo fora do índice parcial por construção: toda edição é uma linha.
        `n_categorias` é o total de entradas do tenant DEPOIS da escrita — o
        call site é a BACK 06.3.
        """
        return await self.emit(
            UsageEventName.GLOSSARIO_EDITADO,
            props=GlossarioEditadoProps(client_id=client_id, n_categorias=n_categorias).model_dump(
                mode="json"
            ),
        )

    async def emit_cliente_excluido(
        self, *, client_id: UUID, n_conciliacoes: int, n_usuarios: int
    ) -> bool:
        """86e34jd1d — exclusão definitiva de cliente, com o que foi junto.

        Sem `session_id` (o alvo é o cliente inteiro), logo fora do índice
        parcial de dedup por construção. Só IDs e contagens — nunca o nome.
        """
        return await self.emit(
            UsageEventName.CLIENTE_EXCLUIDO,
            props=ClienteExcluidoProps(
                client_id=client_id, n_conciliacoes=n_conciliacoes, n_usuarios=n_usuarios
            ).model_dump(mode="json"),
        )

    async def emit_cliente_encerrado(
        self, *, client_id: UUID, n_conciliacoes: int, n_usuarios: int
    ) -> bool:
        """86e36pm1z — encerramento com retenção (irmão do `cliente_excluido`).

        Aqui as conciliações FICAM (a contagem diz o que foi retido) e os
        usuários foram anonimizados, não apagados. Sem `session_id` — fora do
        índice parcial de dedup por construção; evento novo nasce SEM dedup.
        """
        return await self.emit(
            UsageEventName.CLIENTE_ENCERRADO,
            props=ClienteEncerradoProps(
                client_id=client_id, n_conciliacoes=n_conciliacoes, n_usuarios=n_usuarios
            ).model_dump(mode="json"),
        )

    async def emit_cliente_criado(
        self,
        *,
        client_id: UUID,
        organization_id: UUID,
        tem_conexao: bool,
        tipo_conexao: str | None,
    ) -> bool:
        """S9 BACK 09.4 — cadastro de cliente. **A métrica da Sprint 9.**

        Emitido nos DOIS ramos (com e sem origem): sem o ramo "com", a leitura
        não teria denominador e um zero em `tem_conexao=false` não distinguiria
        "ninguém cadastrou sem origem" de "ninguém cadastrou". Sem `session_id`,
        logo fora do índice parcial de dedup por construção.
        """
        return await self.emit(
            UsageEventName.CLIENTE_CRIADO,
            props=ClienteCriadoProps(
                client_id=client_id,
                organization_id=organization_id,
                tem_conexao=tem_conexao,
                tipo_conexao=tipo_conexao,
            ).model_dump(mode="json"),
        )

    async def emit_plano_contas_sincronizado(
        self,
        *,
        client_id: UUID,
        total_categorias: int,
        ativas: int,
        com_destino: int,
        com_conta_contabil: int,
    ) -> bool:
        """S10 BACK 10.4 — **a métrica da Sprint 10**. Sem `session_id`.

        Emitido no fim de TODA sincronização bem-sucedida, inclusive a forçada,
        e **sem dedup**: a fórmula lê a ÚLTIMA linha por `client_id` no período,
        então 30 sincronizações do mesmo cliente precisam gerar 30 linhas. Este
        evento nasce fora de `DEDUPED_EVENT_NAMES` por construção (sem
        `session_id`, o índice parcial nem o alcança).

        `com_destino` e `com_conta_contabil` são contados sobre as ATIVAS, a
        mesma base de `ativas` — é `com_destino ÷ ativas` que a leitura D+30
        calcula, e misturar bases daria um percentual que não significa nada.
        """
        return await self.emit(
            UsageEventName.PLANO_CONTAS_SINCRONIZADO,
            props=PlanoContasSincronizadoProps(
                client_id=client_id,
                total_categorias=total_categorias,
                ativas=ativas,
                com_destino=com_destino,
                com_conta_contabil=com_conta_contabil,
            ).model_dump(mode="json"),
        )

    async def emit_carteira_sincronizada(
        self,
        *,
        client_id: UUID,
        titulos_receber: int,
        titulos_pagar: int,
        vencidos: int,
        mais_antigo_dias: int,
    ) -> bool:
        """S11 BACK 11.3 — **a métrica da Sprint 11**. Sem `session_id`.

        Emitido só ao concluir uma sincronização **ÍNTEGRA**, nunca numa que
        falhou no meio: a cobertura é sobre o que a plataforma de fato enxerga, e
        contar uma carteira parcial como se fosse completa é exatamente o erro
        que o R1 existe para impedir.

        **Sem dedup**, e não por esquecimento: a fórmula lê a ÚLTIMA linha por
        `client_id` no período, então 30 sincronizações do mesmo cliente precisam
        gerar 30 linhas. Este evento nasce fora de `DEDUPED_EVENT_NAMES` por
        construção (sem `session_id`, o índice parcial nem o alcança) — e entrar
        na allow-list exigiria migration, o que é a revisão que se quer ter.

        `vencidos` é SUBCONJUNTO do total, não uma terceira parcela. As cinco
        chaves são as declaradas no PRD, e `CarteiraSincronizadaProps` recusa
        qualquer outra.
        """
        return await self.emit(
            UsageEventName.CARTEIRA_SINCRONIZADA,
            props=CarteiraSincronizadaProps(
                client_id=client_id,
                titulos_receber=titulos_receber,
                titulos_pagar=titulos_pagar,
                vencidos=vencidos,
                mais_antigo_dias=mais_antigo_dias,
            ).model_dump(mode="json"),
        )

    async def emit_contexto_titulo_registrado(
        self, *, client_id: UUID, tipo_contexto: TitleContextTypeName
    ) -> bool:
        """S15 BACK 15.1 — instrumenta a suposição S-1. Sem `session_id`.

        Uma linha por REGISTRO (não por título): o mesmo título pode acumular
        vários contextos no histórico, e cada um é um evento de quem atende
        agindo — contar só o mais recente subestimaria a adoção. Sem dedup, pelo
        mesmo motivo de `carteira_sincronizada`: aqui não há nem `client_id`
        único por período que justificasse colapsar.
        """
        return await self.emit(
            UsageEventName.CONTEXTO_TITULO_REGISTRADO,
            props=ContextoTituloRegistradoProps(
                client_id=client_id, tipo_contexto=tipo_contexto
            ).model_dump(mode="json"),
        )

    async def emit_recebiveis_classificados(
        self,
        *,
        client_id: UUID,
        valor_vencido_total_centavos: int,
        valor_sem_contexto_centavos: int,
        titulos_vencidos: int,
    ) -> bool:
        """S15 BACK 15.2 — **a métrica da Sprint 15**. Sem `session_id`.

        Emitido a cada CÁLCULO do relatório (não só quando algo muda): a
        leitura D+30 lê a última linha por `client_id`, então abrir o relatório
        30 vezes precisa gerar 30 linhas. Sem dedup, mesmo motivo de
        `carteira_sincronizada` — este evento nasce fora de
        `DEDUPED_EVENT_NAMES` por construção (sem `session_id`).
        """
        return await self.emit(
            UsageEventName.RECEBIVEIS_CLASSIFICADOS,
            props=RecebiveisClassificadosProps(
                client_id=client_id,
                valor_vencido_total_centavos=valor_vencido_total_centavos,
                valor_sem_contexto_centavos=valor_sem_contexto_centavos,
                titulos_vencidos=titulos_vencidos,
            ).model_dump(mode="json"),
        )

    async def emit_organizacao_criada(self, *, organization_id: UUID) -> bool:
        """86e36ecnp — a plataforma cadastrou uma organização. Sem `session_id`."""
        return await self.emit(
            UsageEventName.ORGANIZACAO_CRIADA,
            props=OrganizacaoCriadaProps(organization_id=organization_id).model_dump(mode="json"),
        )

    async def emit_organizacao_desativada(
        self, *, organization_id: UUID, n_usuarios: int, n_clientes: int
    ) -> bool:
        """86e36ecnp — suspensão de organização, com o que ficou preso (contagens)."""
        return await self.emit(
            UsageEventName.ORGANIZACAO_DESATIVADA,
            props=OrganizacaoDesativadaProps(
                organization_id=organization_id, n_usuarios=n_usuarios, n_clientes=n_clientes
            ).model_dump(mode="json"),
        )

    async def emit_usuario_transferido_de_organizacao(
        self,
        *,
        user_id: UUID,
        from_organization_id: UUID,
        to_organization_id: UUID,
        n_carteira_removida: int,
        n_favoritos_removidos: int,
        n_notificacoes_removidas: int,
    ) -> bool:
        """86e3bvbfx — a plataforma transferiu um staff. Sem `session_id`."""
        return await self.emit(
            UsageEventName.USUARIO_TRANSFERIDO_DE_ORGANIZACAO,
            props=UsuarioTransferidoDeOrganizacaoProps(
                user_id=user_id,
                from_organization_id=from_organization_id,
                to_organization_id=to_organization_id,
                n_carteira_removida=n_carteira_removida,
                n_favoritos_removidos=n_favoritos_removidos,
                n_notificacoes_removidas=n_notificacoes_removidas,
            ).model_dump(mode="json"),
        )

    # ------------------------------------------------------------------
    # Sprint 7 (BACK 07.5) — emissores do lançamento no Omie
    #
    # Ambos montam `props` pelo MODELO Pydantic, nunca por `dict` solto — a
    # garantia "só int/Literal, `extra=forbid`" vale na EMISSÃO e não só na
    # borda HTTP. Nenhum dos dois é deduplicado (ADR-010): o mesmo operador
    # manda vários lotes na mesma sessão e **cada lote é um fato**; deduplicar
    # por sessão apagaria o 2º lote em silêncio e a métrica ficaria menor que a
    # realidade — justamente o erro que a allow-list existe para evitar.
    # ------------------------------------------------------------------

    async def emit_omie_lancamento_enviado(
        self,
        *,
        session_id: UUID,
        linhas: int,
        sucesso: int,
        falha: int,
        duracao_ms: int,
    ) -> bool:
        """NUMERADOR da Sprint 7: um lote de lançamento foi executado.

        Uma linha por LOTE. `duracao_ms` é `int` (§3.4) e alimenta o guardrail
        "o tempo de conciliação não pode subir".
        """
        return await self.emit(
            UsageEventName.OMIE_LANCAMENTO_ENVIADO,
            session_id=session_id,
            props=OmieLancamentoEnviadoProps(
                linhas=linhas,
                sucesso=sucesso,
                falha=falha,
                duracao_ms=duracao_ms,
            ).model_dump(mode="json"),
        )

    async def emit_omie_lancamento_rejeitado(
        self,
        *,
        session_id: UUID,
        codigo: OmieRejectionCode,
        fault_message: str | None,
    ) -> bool:
        """A Omie recusou uma linha — com a FAMÍLIA do erro, não com o texto.

        `fault_message` entra aqui só para ser **classificado**; o texto
        integral não vai para o sink (ADR-031-BE). Ele já volta ao usuário na
        resposta do lote e fica em `reconciliation_omie_postings.error_message`.
        """
        return await self.emit(
            UsageEventName.OMIE_LANCAMENTO_REJEITADO,
            session_id=session_id,
            props=OmieLancamentoRejeitadoProps(
                codigo=codigo,
                categoria=classify_omie_rejection(fault_message),
            ).model_dump(mode="json"),
        )

    # ------------------------------------------------------------------
    # Evento vindo do frontend (com RBAC)
    # ------------------------------------------------------------------

    async def record_client_event(
        self,
        payload: AutorNavegouForaRequest | NotificacaoEntregueRequest,
        *,
        user: CurrentUser,
        db: AsyncSession,
    ) -> bool:
        """Valida ownership do `session_id` e grava o evento do frontend.

        RBAC no SERVICE (CLAUDE.md), não só no router:
            - sessão inexistente/descartada → 404;
            - sessão de cliente fora da carteira → 403 via `require_client_access`
              (que ainda grava a linha `denied` em `access_audit`).

        Diferente dos GETs de leitura, aqui NÃO convertemos 403→404: o critério de
        aceite da task pede 403 explicitamente, e este endpoint não expõe dado do
        cliente — o vetor de enumeração que motiva a conversão nos GETs não se
        aplica (o atacante precisaria já conhecer o UUID da sessão).
        """
        client_id = await self._repo.get_session_client_id(payload.session_id, user=user)
        if client_id is None:
            # O SELECT já vem filtrado por tenant (S5/R3): sessão de outro
            # tenant é indistinguível de inexistente para o cliente. A trilha,
            # porém, precisa registrar a tentativa cross-tenant (R6).
            await audit_session_tenant_miss(db, user, payload.session_id)
            raise NotFoundError(_SESSION_NOT_FOUND_MSG)

        await require_client_access(client_id, user, db)

        return await self.emit(
            UsageEventName(payload.event),
            session_id=payload.session_id,
            props=payload.props.model_dump(mode="json"),
        )
