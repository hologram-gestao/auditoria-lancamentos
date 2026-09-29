"""Partida contábil pura (Sprint 16, BACK 16.3 — R3), provada contra a amostra REAL.

As 32 linhas de `extrato_cliente.csv` (MSFG anonimizada, agosto/2026) + as decisões de
`decisoes_depara.csv` + a conta do banco `649` como conta PADRÃO (o arquivo não tem
coluna de conta) têm de produzir, linha a linha, o débito e o crédito das colunas 2 e 3
de `lancamentos_esperados.csv` (o arquivo que o sistema contábil recebeu: Latin-1, CRLF,
sem cabeçalho). Também: a resolução da conta do banco, o predicado ÚNICO de "partida
completa" e o teste de fonte de que ninguém mais o recalcula.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.db.models import DecimalSeparator, MaterializedSituation
from app.modules.client_file_ingestion.reader import parse_amount
from app.modules.client_mapping.apply import apply_mapping
from app.modules.client_mapping.partida import (
    Partida,
    derive_partida,
    is_partida_completa,
    pending_source_accounts,
    resolve_bank_code,
)

pytestmark = pytest.mark.unit

_SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "accounting_sample"
_MONTH = _SAMPLE / "cliente_exemplo_2026_08"
_BANK = "649"
AGO = date(2026, 8, 1)


@dataclass(frozen=True)
class _Line:
    entry_date: date
    description: str
    amount: Decimal


def _extrato() -> list[_Line]:
    text = (_MONTH / "extrato_cliente.csv").read_text(encoding="utf-8")
    rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
    return [
        _Line(
            entry_date=datetime.strptime(r["Data"], "%d/%m/%Y").date(),
            # O leitor da S14 apara as células (o extrato real tem espaço no fim).
            description=r["Descricao"].strip(),
            amount=parse_amount(r["Valor"], DecimalSeparator.COMMA),
        )
        for r in rows
    ]


def _decisoes() -> dict[str, tuple[str, str]]:
    text = (_MONTH / "decisoes_depara.csv").read_text(encoding="utf-8")
    rows = csv.DictReader(io.StringIO(text), delimiter=";")
    return {
        r["categoria_origem"].strip(): (r["conta_contabil"], r["historico_padrao"]) for r in rows
    }


def _esperados() -> list[list[str]]:
    raw = (_MONTH / "lancamentos_esperados.csv").read_bytes().decode("latin-1")
    assert "\r\n" in raw, "o arquivo real é CRLF"
    return [line.split(";", 4) for line in raw.split("\r\n") if line]


def _brl(amount: Decimal) -> str:
    """`R$ 1.234,56` — só para COMPARAR com o arquivo (a formatação é da Sprint 13)."""
    inteiro, centavos = f"{abs(amount):.2f}".split(".")
    grupos = re.sub(r"(?<=\d)(?=(\d{3})+$)", ".", inteiro)
    return f"R$ {grupos},{centavos}"


class TestAmostraReal:
    def test_a_amostra_tem_32_linhas_e_os_totais_do_readme(self) -> None:
        linhas = _extrato()
        assert len(linhas) == 32
        assert sum(x.amount for x in linhas if x.amount > 0) == Decimal("29377.33")
        assert sum(-x.amount for x in linhas if x.amount < 0) == Decimal("24193.66")
        assert len(_esperados()) == 32

    def test_debito_e_credito_batem_linha_a_linha_32_de_32(self) -> None:
        decisoes = _decisoes()
        bindings = {("arquivo", None): _BANK}
        batem = 0
        for linha, esperado in zip(_extrato(), _esperados(), strict=True):
            conta, _hist = decisoes[linha.description]
            bank = resolve_bank_code("arquivo", None, bindings)
            assert bank == _BANK
            partida = derive_partida(linha.amount, conta, bank)
            assert partida is not None
            data, debito, credito, valor, _historico = esperado
            assert data == linha.entry_date.strftime("%d/%m/%Y")
            assert valor == _brl(linha.amount)
            assert (partida.debit, partida.credit) == (debito, credito), esperado
            batem += 1
        assert batem == 32

    def test_o_historico_fixo_por_decisao_reproduz_a_coluna_5(self) -> None:
        """Suposição S-2 na amostra: o histórico fixo por decisão cobre as 32 linhas."""
        decisoes = _decisoes()
        for linha, esperado in zip(_extrato(), _esperados(), strict=True):
            assert decisoes[linha.description][1] == esperado[4]


class TestPartida:
    def test_entrada_debita_o_banco_e_saida_credita(self) -> None:
        assert derive_partida(Decimal("0.13"), "650", "649") == Partida(debit="649", credit="650")
        assert derive_partida(Decimal("-1.75"), "542", "649") == Partida(debit="542", credit="649")

    def test_valor_zero_nao_e_lancamento(self) -> None:
        assert derive_partida(Decimal("0.00"), "650", "649") is None


class TestContaDoBanco:
    def test_explicita_vence(self) -> None:
        bindings = {("omie", "111"): "649", ("omie", None): "700"}
        assert resolve_bank_code("omie", "111", bindings) == "649"

    def test_padrao_so_cobre_linha_sem_conta_de_origem(self) -> None:
        """Decisão do planejador: conta de origem sem associação NUNCA cai na padrão."""
        bindings = {("arquivo", None): "649"}
        assert resolve_bank_code("arquivo", None, bindings) == "649"
        assert resolve_bank_code("arquivo", "0001-2", bindings) is None
        assert resolve_bank_code("omie", None, bindings) is None

    def test_sem_nada_e_pendente(self) -> None:
        assert resolve_bank_code("omie", "111", {}) is None


@dataclass(frozen=True)
class _Snap:
    source_type: str = "arquivo"
    source_account_id: str | None = None
    situation: str = "alvo"
    accounting_account_code: str | None = "662"
    bank_account_code: str | None = "649"
    history_present: bool | None = True


class TestPredicadoUnico:
    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            (_Snap(), True),
            (_Snap(history_present=False), False),
            (_Snap(bank_account_code=None), False),
            (_Snap(accounting_account_code=None), False),  # legado do catálogo
            (_Snap(situation="nao_mapear", accounting_account_code=None), False),
        ],
    )
    def test_casos(self, line: _Snap, *, expected: bool) -> None:
        assert is_partida_completa(line) is expected

    def test_pendentes_so_das_linhas_com_alvo(self) -> None:
        lines = [
            _Snap(bank_account_code=None),
            _Snap(source_type="omie", source_account_id="222", bank_account_code=None),
            _Snap(situation="nao_mapear", accounting_account_code=None, bank_account_code=None),
            _Snap(),
        ]
        assert pending_source_accounts(lines) == (("arquivo", None), ("omie", "222"))

    def test_nenhum_segundo_calculo_de_completude_no_modulo(self) -> None:
        """O predicado mora em `partida.py`: ninguém mais testa os três lados juntos."""
        base = Path(__file__).resolve().parents[2] / "app" / "modules"
        for path in base.rglob("*.py"):
            if path.name == "partida.py":
                continue
            source = path.read_text(encoding="utf-8")
            assert "bank_account_code is not None" not in source, path
            assert "and bool(line.history_present)" not in source, path


class _Mv:
    def __init__(self, mid: str, amount: str, account: str | None) -> None:
        self.source_type = "arquivo"
        self.source_movement_id = mid
        self.source_account_id = account
        self.movement_date = date(2026, 8, 5)
        self.amount = Decimal(amount)
        self.category_code = "aluguel"
        self.status = "presente"


class _Dec:
    def __init__(self) -> None:
        self.id: UUID = uuid4()
        self.source_type = "arquivo"
        self.category_code = "aluguel"
        self.effective_from = AGO
        self.decision_type = "alvo"


class TestAplicacaoComBanco:
    def _apply(self, bindings: dict[tuple[str, str | None], str], movements: list[_Mv]) -> object:
        key = ("arquivo", "aluguel")
        return apply_mapping(
            movements,
            [_Dec()],
            AGO,
            target_code_of={key: None},
            accounting_code_of={key: "662"},
            bank_bindings=bindings,
            history_present_of={key: True},
        )

    def test_item_leva_o_banco_e_a_partida_completa(self) -> None:
        result = self._apply({("arquivo", None): "649"}, [_Mv("1", "10.00", None)])
        (item,) = result.items  # type: ignore[attr-defined]
        assert item.bank_account_code == "649"
        assert item.history_present is True
        assert item.situation is MaterializedSituation.ALVO
        assert item.partida_completa is True

    def test_conta_de_origem_sem_associacao_fica_pendente(self) -> None:
        result = self._apply(
            {("arquivo", None): "649"}, [_Mv("1", "10.00", None), _Mv("2", "-3.00", "cc-9")]
        )
        items = result.items  # type: ignore[attr-defined]
        assert [i.bank_account_code for i in items] == ["649", None]
        assert pending_source_accounts(items) == (("arquivo", "cc-9"),)

    def test_trocar_a_associacao_muda_o_token(self) -> None:
        movements = [_Mv("1", "10.00", None)]
        decision = _Dec()
        key = ("arquivo", "aluguel")

        def token(bank: str) -> str:
            return apply_mapping(
                movements,
                [decision],
                AGO,
                target_code_of={key: None},
                accounting_code_of={key: "662"},
                bank_bindings={("arquivo", None): bank},
                history_present_of={key: True},
            ).fingerprint(destination_id="d")

        assert token("649") != token("650")
        assert token("649") == token("649")
