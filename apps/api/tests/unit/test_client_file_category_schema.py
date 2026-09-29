"""As garantias das categorias de arquivo têm DUAS fontes: modelo e migration (BACK 14.4).

Trava também as leis do R4 que o schema carrega:

1. o código é gerado, ESTÁVEL e sem relação com o rótulo (`arq-<hex>`);
2. o rótulo só existe cifrado (par ciphertext/IV com CHECK);
3. `UNIQUE(client_id, code)`; FK CASCADE; append-only (sem `updated_at`);
4. purga no encerramento (lista declarada) e AAD 13 → 14.
"""

from __future__ import annotations

import importlib.util
import inspect
import re
from pathlib import Path
from types import ModuleType

from sqlalchemy import CheckConstraint, UniqueConstraint

from app.core import crypto_service
from app.db.models import (
    FILE_CATEGORY_CODE_PREFIX,
    FILE_CATEGORY_LABEL_PAIR_CONSTRAINT,
    MAX_MOVEMENT_CATEGORY_CODE_CHARS,
    UQ_CLIENT_FILE_CATEGORY_CLIENT_CODE,
    ClientFileCategory,
    file_category_label_pair_check,
    new_file_category_code,
)
from app.db.models.client import IV_HEX_LENGTH
from app.modules.clients.repository import ClientRepository

_MIGRATION = "c7d3f8a24e61_s14_client_file_categories.py"
_VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"


def _load_migration() -> ModuleType:
    path = _VERSIONS / _MIGRATION
    spec = importlib.util.spec_from_file_location("_migration_c7d3f8a24e61", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestModeloEMigrationBatem:
    def test_predicado_do_check_do_par_bate(self) -> None:
        assert file_category_label_pair_check() == _load_migration()._CK_LABEL_PAIR

    def test_nomes_e_tamanhos_batem(self) -> None:
        mig = _load_migration()
        assert mig._UQ_CLIENT_CODE == UQ_CLIENT_FILE_CATEGORY_CLIENT_CODE
        assert (
            f"ck_client_file_categories_{mig._CK_LABEL_PAIR_LABEL}"
            == FILE_CATEGORY_LABEL_PAIR_CONSTRAINT
        )
        assert mig._CODE_MAX == MAX_MOVEMENT_CATEGORY_CODE_CHARS
        assert mig._IV_HEX_LENGTH == IV_HEX_LENGTH

    def test_colunas_do_modelo_e_da_migration_batem(self) -> None:
        source = (_VERSIONS / _MIGRATION).read_text(encoding="utf-8")
        upgrade = source.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
        for column in ClientFileCategory.__table__.c:
            assert f'"{column.name}"' in upgrade, column.name

    def test_migration_encadeia_na_da_14_1(self) -> None:
        mig = _load_migration()
        assert mig.revision == "c7d3f8a24e61"
        assert mig.down_revision == "b4c8e2d71f95"

    def test_nenhuma_outra_migration_encadeia_no_mesmo_pai(self) -> None:
        pais = [
            match.group(1)
            for path in _VERSIONS.glob("*.py")
            if (
                match := re.search(
                    r'^down_revision[^=]*=\s*"([0-9a-f]+)"',
                    path.read_text(encoding="utf-8"),
                    re.MULTILINE,
                )
            )
        ]
        repetidos = {pai for pai in pais if pais.count(pai) > 1}
        assert not repetidos, f"mais de uma migration encadeada em {repetidos}"

    def test_downgrade_e_real(self) -> None:
        source = (_VERSIONS / _MIGRATION).read_text(encoding="utf-8")
        assert "op.drop_table(_TABLE)" in source.split("def downgrade()", 1)[1]


class TestModeloDeclaraAsGarantias:
    def test_unicidade_e_cliente_mais_codigo(self) -> None:
        unique = next(
            c
            for c in ClientFileCategory.__table__.constraints
            if isinstance(c, UniqueConstraint) and c.name == UQ_CLIENT_FILE_CATEGORY_CLIENT_CODE
        )
        assert [c.name for c in unique.columns] == ["client_id", "code"]

    def test_tabela_declara_o_check_do_par(self) -> None:
        check = next(
            c
            for c in ClientFileCategory.__table__.constraints
            if isinstance(c, CheckConstraint) and c.name == FILE_CATEGORY_LABEL_PAIR_CONSTRAINT
        )
        assert str(check.sqltext) == file_category_label_pair_check()

    def test_rotulo_so_existe_cifrado(self) -> None:
        columns = set(ClientFileCategory.__table__.c.keys())
        assert {"label_encrypted", "label_iv"} <= columns
        for forbidden in ("label", "name", "nome", "descricao", "description", "label_hash"):
            assert forbidden not in columns, forbidden
        table = ClientFileCategory.__table__
        assert table.c.label_encrypted.nullable is False
        assert table.c.label_iv.nullable is False
        assert table.c.label_iv.type.length == IV_HEX_LENGTH

    def test_sem_hash_de_lookup(self) -> None:
        """O casamento é em memória (registry) — nenhuma coluna de HMAC/hash."""
        assert not any("hash" in column.name for column in ClientFileCategory.__table__.c)

    def test_append_only_sem_updated_at(self) -> None:
        assert "updated_at" not in ClientFileCategory.__table__.c
        assert ClientFileCategory.__table__.c.first_seen_at.server_default is not None

    def test_fk_para_clients_cascade(self) -> None:
        fk = next(iter(ClientFileCategory.__table__.c.client_id.foreign_keys))
        assert fk.column.table.name == "clients"
        assert fk.ondelete == "CASCADE"

    def test_codigo_tem_o_teto_da_base_de_movimentos(self) -> None:
        """É o MESMO código que a base grava em `category_code`."""
        assert ClientFileCategory.__table__.c.code.type.length == MAX_MOVEMENT_CATEGORY_CODE_CHARS


class TestCodigoEstavel:
    def test_codigo_novo_tem_prefixo_e_cabe_na_coluna(self) -> None:
        code = new_file_category_code()
        assert code.startswith(FILE_CATEGORY_CODE_PREFIX)
        assert re.fullmatch(r"arq-[0-9a-f]{12}", code)
        assert len(code) <= MAX_MOVEMENT_CATEGORY_CODE_CHARS

    def test_dois_codigos_sao_diferentes_e_nao_derivam_de_nada(self) -> None:
        """Sem entrada: não há de onde derivar. Dois seguidos nunca coincidem."""
        assert new_file_category_code() != new_file_category_code()
        assert len({new_file_category_code() for _ in range(200)}) == 200


class TestPurgaEInventarioDeAad:
    def test_close_client_purge_declara_a_tabela(self) -> None:
        source = inspect.getsource(ClientRepository.close_client_purge)
        assert "delete(ClientFileCategory)" in source

    def test_o_par_de_aad_e_a_14a_constante(self) -> None:
        """14ª é a da 14.4; a 15ª (`client_movements.description_encrypted`) é da 14.3."""
        assert crypto_service.AAD_FILE_CATEGORY_LABEL == (
            "client_file_categories",
            "label_encrypted",
        )
        declared = [n for n in vars(crypto_service) if n.startswith("AAD_")]
        assert declared.index("AAD_FILE_CATEGORY_LABEL") == 13
        # Cresce a cada sprint que cifra campo novo; a contagem canônica é a de
        # `test_crypto_service.py` (16 desde a BACK 16.1).
        assert len(declared) >= 15
