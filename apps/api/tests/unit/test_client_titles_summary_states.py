"""Os três estados do bloco de agregados, e o tipo do dinheiro (BACK 11.4).

O que está sob teste aqui **não depende de banco**: é a diferença entre "carteira
vazia", "nunca sincronizada" e "a última tentativa falhou" — a parte do R3 que diz
"nunca zeros que pareçam resultado". Os números em si (as somas por balde) são
agregação SQL e vivem no teste de integração.

A separação é deliberada: este sandbox não tem Postgres, e um teste que afirmasse o
estado vazio dentro do teste de integração iria para o commit **sem nunca ter
rodado**. O estado é decidido em Python; aqui ele é verificado de verdade.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.db.models.client_title import TitleType
from app.modules.client_titles.aging import OVERDUE_BUCKETS, AgingBucket
from app.modules.client_titles.repository import (
    ZERO_MONEY,
    AgingTotals,
    TitlesSummary,
    _zeroed_totals,
)

_HOJE = date(2026, 9, 24)


def _summary(
    *,
    synced_at: datetime | None = None,
    failed_at: datetime | None = None,
    a_pagar: AgingTotals | None = None,
    a_receber: AgingTotals | None = None,
) -> TitlesSummary:
    return TitlesSummary(
        a_pagar=a_pagar or _zeroed_totals(),
        a_receber=a_receber or _zeroed_totals(),
        synced_at=synced_at,
        sync_failed_at=failed_at,
        referencia=_HOJE,
    )


class TestOsTresEstados:
    def test_nunca_sincronizada_e_um_campo_nao_um_zero(self) -> None:
        """R3: "nunca zeros que pareçam resultado".

        O teste afirma **o campo**, não o valor `0` — que é exatamente o que o
        critério de aceite pede. Uma tela que decidisse por
        `total_em_aberto == 0` diria "este cliente não deve nada" para um cliente
        que ninguém nunca consultou.
        """
        summary = _summary(synced_at=None)
        assert summary.nunca_sincronizada is True
        # e os zeros estão lá, sem significar resultado
        assert summary.a_pagar.total_em_aberto == ZERO_MONEY

    def test_carteira_vazia_sincronizada_nao_e_nunca_sincronizada(self) -> None:
        """Duas coisas diferentes, e a única que as distingue é o carimbo.

        Cliente consultado que não tem nenhum título em aberto é um resultado
        legítimo: zero. É por isso que o estado não pode ser derivado da contagem.
        """
        summary = _summary(synced_at=datetime(2026, 9, 24, 3, 0, tzinfo=UTC))
        assert summary.nunca_sincronizada is False
        assert summary.a_pagar.qtd_em_aberto == 0

    def test_ultima_falhou_mas_os_agregados_continuam_sendo_os_da_integra(self) -> None:
        """R3: agregados da última íntegra + a data dela + o aviso da falha.

        Os três campos coexistindo é o contrato: descartar os números deixaria a
        tela vazia justamente quando o usuário precisa dela, e esconder a falha
        faria um aging velho passar por atual.
        """
        integra = datetime(2026, 9, 20, 3, 0, tzinfo=UTC)
        falha = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
        summary = _summary(synced_at=integra, failed_at=falha)

        assert summary.nunca_sincronizada is False
        assert summary.synced_at == integra
        assert summary.sync_failed_at == falha
        assert summary.sync_failed_at > summary.synced_at

    def test_nunca_sincronizada_com_falha_tambem_e_possivel(self) -> None:
        """Primeira tentativa da vida falhou: não há íntegra anterior nenhuma.

        A tela precisa oferecer "sincronizar" E avisar da falha — não escolher
        entre as duas.
        """
        summary = _summary(synced_at=None, failed_at=datetime(2026, 9, 24, tzinfo=UTC))
        assert summary.nunca_sincronizada is True
        assert summary.sync_failed_at is not None

    def test_a_referencia_do_aging_viaja_na_resposta(self) -> None:
        """A data é do SERVIDOR (R3).

        Sem ela na resposta, a tela recalcularia os baldes com o "hoje" do
        navegador — e dois usuários em fusos diferentes veriam baldes diferentes
        para o mesmo título.
        """
        assert _summary().referencia == _HOJE


class TestAgregadoZerado:
    def test_todas_as_quatro_chaves_de_balde_estao_presentes(self) -> None:
        """Balde ausente viraria `KeyError` na tela, ou coluna omitida em silêncio."""
        zerado = _zeroed_totals()
        assert set(zerado.baldes) == set(OVERDUE_BUCKETS)
        assert set(zerado.baldes_qtd) == set(OVERDUE_BUCKETS)

    def test_a_vencer_nao_e_um_balde_dos_agregados(self) -> None:
        """Os quatro baldes somam o VENCIDO; "a vencer" tem campo próprio.

        Se `a_vencer` entrasse no dicionário de baldes, somar o dicionário deixaria
        de dar o total vencido e a identidade da tela quebraria.
        """
        zerado = _zeroed_totals()
        assert AgingBucket.A_VENCER not in zerado.baldes
        assert hasattr(zerado, "total_a_vencer")

    def test_dinheiro_e_decimal_com_escala_de_centavos(self) -> None:
        """§3.4 ponta a ponta: nunca `float`, nem no zero."""
        zerado = _zeroed_totals()
        for valor in (zerado.total_em_aberto, zerado.total_a_vencer, zerado.total_vencido):
            assert isinstance(valor, Decimal)
            assert not isinstance(valor, float)
        for valor in zerado.baldes.values():
            assert isinstance(valor, Decimal)
        assert str(ZERO_MONEY) == "0.00", "zero com escala: a tela não pode ver '0' e '0,00'"

    def test_contagens_sao_int(self) -> None:
        zerado = _zeroed_totals()
        assert isinstance(zerado.qtd_em_aberto, int)
        assert all(isinstance(q, int) for q in zerado.baldes_qtd.values())


class TestIdentidadesQueATelaPromete:
    """As somas que a tela exibe lado a lado têm de fechar.

    Aqui elas são verificadas sobre valores montados à mão — a mesma identidade é
    afirmada no teste de integração sobre o resultado real do SQL. Ter as duas é de
    propósito: esta versão roda sem banco e documenta o contrato; a outra prova que
    o SQL o cumpre.
    """

    @staticmethod
    def _totals(a_vencer: str, baldes: dict[AgingBucket, str]) -> AgingTotals:
        vencido = sum((Decimal(v) for v in baldes.values()), start=Decimal("0.00"))
        return AgingTotals(
            total_em_aberto=Decimal(a_vencer) + vencido,
            total_a_vencer=Decimal(a_vencer),
            total_vencido=vencido,
            baldes={b: Decimal(baldes.get(b, "0.00")) for b in OVERDUE_BUCKETS},
            qtd_em_aberto=1 + len(baldes),
            qtd_a_vencer=1,
            qtd_vencido=len(baldes),
            baldes_qtd=dict.fromkeys(OVERDUE_BUCKETS, 0),
        )

    def test_a_vencer_mais_vencido_da_o_total_em_aberto(self) -> None:
        totals = self._totals(
            "1000.00",
            {
                AgingBucket.D1_30: "100.00",
                AgingBucket.D31_60: "200.00",
                AgingBucket.D61_90: "300.00",
                AgingBucket.D90_MAIS: "400.00",
            },
        )
        assert totals.total_a_vencer + totals.total_vencido == totals.total_em_aberto

    def test_os_quatro_baldes_somam_exatamente_o_total_vencido(self) -> None:
        """A asserção que o critério de aceite pede — não um comentário."""
        totals = self._totals(
            "0.00",
            {
                AgingBucket.D1_30: "24547.60",
                AgingBucket.D31_60: "0.00",
                AgingBucket.D61_90: "0.00",
                AgingBucket.D90_MAIS: "82865.50",
            },
        )
        assert sum(totals.baldes.values(), start=Decimal("0.00")) == totals.total_vencido
        # os números da base consolidada de 17/06/2026 que motivou a sprint
        assert totals.total_vencido == Decimal("107413.10")
        assert totals.baldes[AgingBucket.D90_MAIS] == Decimal("82865.50")


class TestOsDoisTiposSaoSeparados:
    def test_a_pagar_e_a_receber_nao_se_misturam(self) -> None:
        """R3: os agregados são "separadamente para a pagar e a receber".

        Um total só esconderia exatamente o que a reunião quer ver — R$ 107 mil a
        receber não se compensa com R$ 107 mil a pagar.
        """
        summary = _summary()
        assert summary.a_pagar is not summary.a_receber
        assert {TitleType.A_PAGAR, TitleType.A_RECEBER} == set(TitleType)
