"""As relações de clientes e fornecedores do Domínio viram vínculo conta ↔ pessoa (86e3qhhrn).

O que é provado aqui, sem banco:
- o teste-ouro: as DUAS fixtures anonimizadas do `.xls` real (com o defeito dos BLANKs
  órfãos, que o xlrd puro recusa) entram pelo leitor e batem linha a linha com
  `vinculos_esperados.csv`, inclusive as linhas que ficam SEM vínculo (conta `0` e conta
  compartilhada), o CNPJ que começa com zeros e a troca de posição de `Código` e `Conta`
  entre os dois relatórios;
- a política em duas camadas: `conta_ausente` e `linha_fora_do_bloco` recusam o arquivo;
  `sem_conta`, `documento_ausente`, `documento_invalido` e `conta_compartilhada` só pulam;
- sem título, sem o CNPJ da empresa, sem cabeçalho e fora de planilha são recusas tipadas,
  e nenhuma recusa ecoa documento, nome ou razão social;
- `normalize_document` aceita CNPJ e CPF com e sem máscara, repõe UM zero perdido e
  recusa verificador errado; `mask_document` esconde a raiz e só a raiz;
- `normalize_cpf` (irmão do `normalize_cnpj`) e o espelho do evento de métrica.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import get_args

import pytest

from app.core.cpf import format_cpf, normalize_cpf
from app.core.exceptions import (
    AppError,
    FileFormatNotSupportedError,
    FileHeaderMismatchError,
    FileInvalidError,
    FileLinesInvalidError,
)
from app.db.models.client_accounting_account import AccountingPartyType
from app.modules.client_accounting_chart.parties import (
    PartySkipReason,
    normalize_document,
    parse_parties_report,
)
from app.modules.client_accounting_chart.service import mask_document
from app.modules.usage_events.schemas import PartyReportKind
from tests.parties_builder import (
    COMPANY_CNPJ,
    INVALID_CNPJ,
    VALID_CNPJS,
    VALID_CPFS,
    build_parties_xlsx,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "accounting_chart_dominio_parties"
_EXPECTED = _FIXTURES / "vinculos_esperados.csv"
#: Textos que existem nas fixtures e NUNCA podem aparecer numa resposta de recusa.
_FIXTURE_TEXTS = ("de exemplo", "EXEMPLO", "0001-91")
_SECRET_NAME = "Fulano Sigiloso ME"


def _body(exc: AppError) -> str:
    return exc.user_message + json.dumps(exc.details, ensure_ascii=False)


def _rows(
    kind: str, *people: tuple[object, object, str]
) -> list[tuple[object, object, str, str, object]]:
    return [
        (i + 1, account, name, name, document) for i, (account, document, name) in enumerate(people)
    ]


class TestTesteOuro:
    @pytest.mark.parametrize(
        ("filename", "relatorio"),
        [("relacao_clientes.xls", "cliente"), ("relacao_fornecedores.xls", "fornecedor")],
    )
    def test_a_fixture_bate_linha_a_linha_com_o_esperado(
        self, filename: str, relatorio: str
    ) -> None:
        report = parse_parties_report((_FIXTURES / filename).read_bytes())
        assert report.party_type.value == relatorio
        assert len(report.company_document) == 14
        with _EXPECTED.open(encoding="utf-8", newline="") as fh:
            expected = [r for r in csv.DictReader(fh, delimiter=";") if r["relatorio"] == relatorio]
        linkable = [
            (int(r["linha"]), r["conta"], r["documento"], r["tipo_documento"])
            for r in expected
            if r["situacao"] == "vinculavel"
        ]
        skipped = [
            (int(r["linha"]), r["situacao"], r["conta"] or None)
            for r in expected
            if r["situacao"] != "vinculavel"
        ]
        assert [
            (item.line, item.account_code, item.document, item.kind) for item in report.lines
        ] == linkable
        assert [(p.line, p.reason, p.account_code) for p in report.skipped] == skipped
        assert linkable, "a fixture tem de ter vínculo"

    def test_as_duas_amostras_reais_tem_o_que_a_task_descreve(self) -> None:
        """A de clientes tem CNPJ e CPF (89 pessoas, todas vinculáveis: um CNPJ que começa
        com zeros é lido como texto, nunca por `int`); a de fornecedores tem a conta `0`
        e uma conta compartilhada (93 pessoas, 88 vinculáveis)."""
        clients = parse_parties_report((_FIXTURES / "relacao_clientes.xls").read_bytes())
        suppliers = parse_parties_report((_FIXTURES / "relacao_fornecedores.xls").read_bytes())
        assert (len(clients.lines), len(suppliers.lines)) == (89, 88)
        assert {item.kind for item in clients.lines} == {"cnpj", "cpf"}
        assert clients.skipped == []
        assert any(item.document.startswith("0") for item in clients.lines)
        assert sorted({p.reason for p in suppliers.skipped}) == ["conta_compartilhada", "sem_conta"]
        # 685 e 918: as contas do critério de aceite, na relação de clientes da Hologram.
        assert {"685", "918"} <= {item.account_code for item in clients.lines}

    def test_a_fixture_ainda_tem_o_defeito_do_dominio(self) -> None:
        import xlrd

        with pytest.raises(xlrd.XLRDError):
            xlrd.open_workbook(str(_FIXTURES / "relacao_clientes.xls"))


class TestPoliticaEmDuasCamadas:
    def test_pulos_nao_recusam_e_saem_com_motivo_e_conta(self) -> None:
        content = build_parties_xlsx(
            "fornecedor",
            _rows(
                "fornecedor",
                (685, VALID_CNPJS[0], "A"),
                (0, VALID_CNPJS[1], "Sem conta"),
                (700, INVALID_CNPJ, "Verificador errado"),
                (701, None, "Sem documento"),
                (746, VALID_CNPJS[1], "Diversos 1"),
                (746, VALID_CNPJS[2], "Diversos 2"),
                (918, VALID_CPFS[0], "Pessoa"),
            ),
            swap_code_and_account=True,
        )
        report = parse_parties_report(content)
        assert [(item.account_code, item.kind) for item in report.lines] == [
            ("685", "cnpj"),
            ("918", "cpf"),
        ]
        assert [(p.line, p.reason, p.account_code) for p in report.skipped] == [
            (9, "sem_conta", "0"),
            (10, "documento_invalido", "700"),
            (11, "documento_ausente", "701"),
            (12, "conta_compartilhada", "746"),
            (13, "conta_compartilhada", "746"),
        ]

    def test_a_mesma_pessoa_repetida_e_uma_linha_so(self) -> None:
        content = build_parties_xlsx(
            "cliente",
            _rows("cliente", (685, VALID_CNPJS[0], "A"), (685, VALID_CNPJS[0], "A de novo")),
        )
        report = parse_parties_report(content)
        assert [item.account_code for item in report.lines] == ["685"]
        assert report.skipped == []

    def test_documento_sem_conta_na_linha_recusa_o_arquivo(self) -> None:
        content = build_parties_xlsx(
            "cliente",
            _rows("cliente", (685, VALID_CNPJS[0], "A"), (None, VALID_CNPJS[1], _SECRET_NAME)),
        )
        with pytest.raises(FileLinesInvalidError) as caught:
            parse_parties_report(content)
        assert caught.value.details == {
            "lines": [{"line": 9, "reason": "conta_ausente", "accountCode": None}],
            "total": 1,
        }
        assert _SECRET_NAME not in _body(caught.value)
        assert VALID_CNPJS[1] not in _body(caught.value)

    def test_pessoa_depois_do_total_recusa_o_arquivo(self) -> None:
        content = build_parties_xlsx(
            "cliente",
            _rows("cliente", (685, VALID_CNPJS[0], "A")),
            trailing=[
                [
                    None,
                    9,
                    None,
                    None,
                    None,
                    919,
                    None,
                    None,
                    None,
                    _SECRET_NAME,
                    None,
                    None,
                    None,
                    None,
                    None,
                    VALID_CNPJS[1],
                ]
            ],
        )
        with pytest.raises(FileLinesInvalidError) as caught:
            parse_parties_report(content)
        assert [item["reason"] for item in caught.value.details["lines"]] == ["linha_fora_do_bloco"]
        assert _SECRET_NAME not in _body(caught.value)

    def test_so_pulos_e_nenhum_vinculo_nao_e_arquivo_vazio(self) -> None:
        content = build_parties_xlsx("cliente", _rows("cliente", (0, VALID_CNPJS[0], "Sem conta")))
        report = parse_parties_report(content)
        assert report.lines == []
        assert [p.reason for p in report.skipped] == ["sem_conta"]


class TestRecusasDoArquivo:
    def test_sem_titulo_e_relatorio_nao_reconhecido(self) -> None:
        content = build_parties_xlsx(
            "cliente", _rows("cliente", (685, VALID_CNPJS[0], "A")), title=""
        )
        with pytest.raises(FileInvalidError) as caught:
            parse_parties_report(content)
        assert caught.value.details == {"reason": "relatorio_nao_reconhecido"}

    def test_sem_cnpj_da_empresa_e_empresa_nao_identificada(self) -> None:
        content = build_parties_xlsx(
            "cliente", _rows("cliente", (685, VALID_CNPJS[0], "A")), company=None
        )
        with pytest.raises(FileInvalidError) as caught:
            parse_parties_report(content)
        assert caught.value.details == {"reason": "empresa_nao_identificada"}

    def test_sem_nenhuma_pessoa_e_sem_pessoas(self) -> None:
        content = build_parties_xlsx("cliente", [])
        with pytest.raises(FileInvalidError) as caught:
            parse_parties_report(content)
        assert caught.value.details == {"reason": "sem_pessoas"}

    def test_planilha_qualquer_e_cabecalho_divergente_sem_texto_do_arquivo(self) -> None:
        import io

        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.append(["codigo_reduzido", "nome", "tipo"])
        ws.append(["649", _SECRET_NAME, "analitica"])
        buffer = io.BytesIO()
        wb.save(buffer)
        with pytest.raises(FileHeaderMismatchError) as caught:
            parse_parties_report(buffer.getvalue())
        assert caught.value.details["foundColumnCount"] == 0
        assert _SECRET_NAME not in _body(caught.value)

    def test_csv_e_formato_nao_suportado(self) -> None:
        with pytest.raises(FileFormatNotSupportedError):
            parse_parties_report(b"Codigo;Conta;Nome\n1;685;A\n")

    def test_recusas_nao_ecoam_texto_da_fixture(self) -> None:
        content = build_parties_xlsx(
            "cliente", _rows("cliente", (685, VALID_CNPJS[0], "A"), (None, VALID_CNPJS[1], "B"))
        )
        with pytest.raises(FileLinesInvalidError) as caught:
            parse_parties_report(content)
        for text in (*_FIXTURE_TEXTS, COMPANY_CNPJ):
            assert text not in _body(caught.value)


class TestDocumentos:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("11.222.333/0001-81", ("11222333000181", "cnpj")),
            ("11222333000181", ("11222333000181", "cnpj")),
            (11222333000181, ("11222333000181", "cnpj")),
            ("529.982.247-25", ("52998224725", "cpf")),
            (52998224725, ("52998224725", "cpf")),
            # Zero à esquerda perdido pelo Excel numa célula NUMÉRICA: um só é reposto.
            (4252011000110, ("04252011000110", "cnpj")),
            # Texto fica texto: os dois zeros do CNPJ não se perdem.
            ("00000000000191", ("00000000000191", "cnpj")),
            (191, None),
            (INVALID_CNPJ, None),
            ("529.982.247-26", None),
            ("123", None),
            ("", None),
            (None, None),
            (True, None),
        ],
    )
    def test_normalize_document(self, raw: object, expected: object) -> None:
        assert normalize_document(raw) == expected

    def test_mask_esconde_a_raiz_e_so_a_raiz(self) -> None:
        assert mask_document("11222333000181") == "**.***.***/0001-81"
        assert mask_document("52998224725") == "***.***.*47-25"
        assert mask_document("123") == "***"

    def test_normalize_cpf_e_format(self) -> None:
        assert normalize_cpf("529.982.247-25") == "52998224725"
        assert format_cpf("52998224725") == "529.982.247-25"
        for bad in ("529.982.247-26", "111.111.111-11", "5299822472", "52998224725x"):
            with pytest.raises(ValueError, match="CPF"):
                normalize_cpf(bad)
        with pytest.raises(ValueError, match="normalizados"):
            format_cpf("123")


class TestEspelhos:
    def test_tipo_do_relatorio_do_evento_espelha_o_enum(self) -> None:
        assert set(get_args(PartyReportKind)) == {m.value for m in AccountingPartyType}

    def test_vocabulario_dos_pulos_e_fechado(self) -> None:
        assert set(get_args(PartySkipReason.__value__)) == {
            "sem_conta",
            "documento_ausente",
            "documento_invalido",
            "conta_compartilhada",
        }
