"""Os baldes de aging particionam os vencidos EXATAMENTE (Sprint 11).

A promessa da tela e do relatório é `1_30 + 31_60 + 61_90 + 90_mais == total
vencido`. Ela só vale se os limites não se sobrepuserem nem deixarem buraco — e
basta um `>=` virar `>` para a soma parar de fechar sem ninguém ver, porque cada
parcela continua sendo um número plausível.

Este teste é sobre o VOCABULÁRIO (`aging.py`), não sobre o SQL: ele roda sem
banco, e é a razão de os limites morarem num módulo só em vez de aparecerem
escritos à mão no filtro da lista e na agregação.
"""

from __future__ import annotations

from app.modules.client_titles.aging import (
    AGING_BUCKET_BOUNDS,
    OVERDUE_BUCKETS,
    AgingBucket,
)


def _bucket_for(days_overdue: int) -> AgingBucket | None:
    """O balde de vencidos de um atraso — `None` se o título ainda não venceu."""
    if days_overdue <= 0:
        return None
    for bucket in OVERDUE_BUCKETS:
        low, high = AGING_BUCKET_BOUNDS[bucket]
        if days_overdue >= low and (high is None or days_overdue <= high):
            return bucket
    return None


class TestVocabulario:
    def test_sao_quatro_baldes_de_vencidos_na_ordem_da_planilha(self) -> None:
        """A mesma segmentação que o escritório já faz à mão (17/06/2026)."""
        assert [b.value for b in OVERDUE_BUCKETS] == ["1_30", "31_60", "61_90", "90_mais"]

    def test_a_vencer_nao_e_balde_de_vencido(self) -> None:
        """Ele existe para poder ser FILTRADO, não para entrar na soma do vencido."""
        assert AgingBucket.A_VENCER not in OVERDUE_BUCKETS
        assert AgingBucket.A_VENCER not in AGING_BUCKET_BOUNDS

    def test_os_limites_cobrem_exatamente_os_quatro_baldes(self) -> None:
        assert set(AGING_BUCKET_BOUNDS) == set(OVERDUE_BUCKETS)

    def test_so_o_ultimo_balde_e_aberto(self) -> None:
        """`90+` não tem teto; os outros três têm, senão se sobrepõem."""
        sem_teto = [b for b, (_, high) in AGING_BUCKET_BOUNDS.items() if high is None]
        assert sem_teto == [AgingBucket.D90_MAIS]


class TestParticaoExata:
    def test_todo_atraso_cai_em_exatamente_um_balde(self) -> None:
        """Sem sobreposição e sem buraco, de 1 a 400 dias de atraso."""
        for days in range(1, 401):
            casam = [
                b
                for b in OVERDUE_BUCKETS
                for low, high in [AGING_BUCKET_BOUNDS[b]]
                if days >= low and (high is None or days <= high)
            ]
            assert len(casam) == 1, f"{days} dias caiu em {casam}"

    def test_as_fronteiras_estao_onde_a_planilha_espera(self) -> None:
        """Os dias exatos de virada — é aqui que um `>` no lugar de `>=` aparece."""
        assert _bucket_for(1) is AgingBucket.D1_30
        assert _bucket_for(30) is AgingBucket.D1_30
        assert _bucket_for(31) is AgingBucket.D31_60
        assert _bucket_for(60) is AgingBucket.D31_60
        assert _bucket_for(61) is AgingBucket.D61_90
        assert _bucket_for(90) is AgingBucket.D61_90
        assert _bucket_for(91) is AgingBucket.D90_MAIS

    def test_quem_ainda_nao_venceu_fica_fora_dos_quatro(self) -> None:
        """Vencer HOJE ainda não é atraso: `dias == 0` não entra em balde nenhum."""
        assert _bucket_for(0) is None
        assert _bucket_for(-1) is None

    def test_o_titulo_de_90_mais_da_sprint_cai_no_balde_certo(self) -> None:
        """O caso que justifica a sprint: 120 dias de atraso, que a conciliação
        do mês corrente jamais traria."""
        assert _bucket_for(120) is AgingBucket.D90_MAIS
