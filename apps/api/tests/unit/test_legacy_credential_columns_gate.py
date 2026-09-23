"""Gate de CI: quem pode tocar as colunas antigas de credencial (S9, BACK 09.5 — R2).

O critério que reprova a sprint sozinho é "nenhuma credencial já gravada pode
ficar ilegível". A conversão (`scripts/convert_credentials_to_connections.py`)
resolve o que já existe; **este gate impede que o problema volte a crescer** —
um call site novo em `clients.omie_app_*` seria mais um lugar para converter, e
ninguém saberia que ele existe até a base quebrar.

A allow-list é **FECHADA e justificada arquivo a arquivo**. Arquivo novo que
toque os nomes proibidos deixa o teste vermelho, com a mensagem apontando o R2.

⚠️ Este teste é textual de propósito — não é análise de tipos. Ele erra para o
lado do falso positivo (um comentário citando o nome já o dispara), e isso é
aceitável: o custo de justificar uma linha na allow-list é baixíssimo perto do
custo de uma credencial ilegível em produção.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_API_ROOT = Path(__file__).resolve().parents[2]

#: Os nomes que só a allow-list pode citar: as 4 colunas e as 2 constantes de
#: AAD do locator ANTIGO.
FORBIDDEN_NAMES = (
    "omie_app_key_encrypted",
    "omie_app_key_iv",
    "omie_app_secret_encrypted",
    "omie_app_secret_iv",
    "AAD_CLIENT_APP_KEY",
    "AAD_CLIENT_APP_SECRET",
)

#: Allow-list FECHADA: caminho (relativo a `apps/api/`) → por que ele pode.
#: Duas famílias: **definição** (quem declara as colunas/constantes) e os
#: **três call sites** que o PRD autoriza.
ALLOWED: dict[str, str] = {
    # --- definição -------------------------------------------------------
    "app/db/models/client.py": "declara as colunas (a definição, não um uso)",
    "app/core/crypto_service.py": "declara as constantes de AAD congeladas",
    "app/core/logging.py": (
        "só CITA o nome num comentário, explicando o redactor que mascara "
        "chaves sensíveis — não lê a coluna"
    ),
    # --- os TRÊS call sites do R2 ---------------------------------------
    "app/modules/clients/service.py": (
        "crypto-shredding do close_client — ESCRITA de '' no encerramento (§4.12). "
        "A 09.3 tirou o PATCH e a 09.4 tirou o create; sobrou só este"
    ),
    "app/modules/client_connections/legacy_fallback.py": (
        "o ÚNICO leitor autorizado: sintetiza a conexão da janela de conversão"
    ),
    "scripts/convert_credentials_to_connections.py": (
        "a conversão em si — lê do locator antigo e re-cifra no novo"
    ),
    # --- backfill legado, anterior a esta sprint -------------------------
    "scripts/rotate_encryption_key.py": (
        "backfill bare→v1 da Sprint 3, pré-existente: converte as colunas no "
        "lugar onde elas estão, sem criar caminho novo"
    ),
    # --- seed de dev/demo ------------------------------------------------
    "scripts/seed_demo_client.py": (
        "seed do cliente-demo: grava nas colunas antigas de propósito, para "
        "exercitar o caminho do fallback em dev"
    ),
}

#: `alembic/versions/` inteiro é definição de schema — migration que mexe nas
#: colunas é o oposto de um call site novo.
ALLOWED_PREFIXES = ("alembic/versions/",)


def _scan_roots() -> list[Path]:
    return [_API_ROOT / "app", _API_ROOT / "scripts", _API_ROOT / "alembic"]


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in _scan_roots():
        files.extend(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
    return sorted(files)


def _is_allowed(rel: str) -> bool:
    return rel in ALLOWED or rel.startswith(ALLOWED_PREFIXES)


def _offenders(files: list[Path], *, root: Path = _API_ROOT) -> dict[str, list[str]]:
    """Arquivo → nomes proibidos que ele cita. `root` parametrizado só para os
    testes de mutação, que escrevem num `tmp_path` fora da árvore do projeto."""
    found: dict[str, list[str]] = {}
    for path in files:
        rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.name
        if _is_allowed(rel):
            continue
        text = path.read_text(encoding="utf-8")
        hits = [name for name in FORBIDDEN_NAMES if name in text]
        if hits:
            found[rel] = hits
    return found


class TestGateDasColunasAntigas:
    def test_nenhum_arquivo_fora_da_allow_list_toca_as_colunas(self) -> None:
        offenders = _offenders(_python_files())
        assert not offenders, (
            "Sprint 9 / R2: arquivo(s) tocando as colunas ANTIGAS de credencial "
            f"fora da allow-list: {offenders}.\n"
            "A credencial mora em `client_connections` desde a 09.3. Para LER a "
            "origem de um cliente, use "
            "`modules/client_connections/legacy_fallback.resolve_origin_connections` "
            "— ele já cobre o cliente ainda não convertido. Se o acesso direto for "
            "mesmo inevitável, acrescente o arquivo em ALLOWED com a justificativa."
        )

    def test_a_allow_list_nao_tem_entrada_morta(self) -> None:
        """Entrada que não existe mais (ou que parou de tocar as colunas) sai.

        Sem isto, a lista viraria um cemitério e ninguém saberia quais exceções
        ainda valem — que é justamente o que ela existe para evitar.
        """
        mortos = []
        for rel, motivo in ALLOWED.items():
            path = _API_ROOT / rel
            if not path.exists():
                mortos.append((rel, f"arquivo não existe ({motivo})"))
                continue
            text = path.read_text(encoding="utf-8")
            if not any(name in text for name in FORBIDDEN_NAMES):
                mortos.append((rel, f"não toca mais as colunas ({motivo})"))
        assert not mortos, f"Entradas mortas na allow-list: {mortos}"

    def test_os_tres_call_sites_do_prd_estao_na_lista(self) -> None:
        """Trava o desenho: se um deles sumir da lista, alguém mudou a regra."""
        for rel in (
            "app/modules/clients/service.py",
            "app/modules/client_connections/legacy_fallback.py",
            "scripts/convert_credentials_to_connections.py",
        ):
            assert rel in ALLOWED

    def test_o_gate_pega_arquivo_novo(self, tmp_path: Path) -> None:
        """Prova por MUTAÇÃO: um arquivo fora da lista tocando a coluna reprova.

        Sem este teste, um bug no varredor (raiz errada, glob errado) deixaria o
        gate verde para sempre — e verde por não olhar nada é pior que ausente.
        """
        intruso = tmp_path / "modulo_novo.py"
        intruso.write_text("x = client.omie_app_key_encrypted\n", encoding="utf-8")
        assert _offenders([intruso])

    def test_o_gate_ignora_arquivo_limpo(self, tmp_path: Path) -> None:
        limpo = tmp_path / "modulo_limpo.py"
        limpo.write_text("x = connection.credentials_encrypted\n", encoding="utf-8")
        assert not _offenders([limpo])

    @pytest.mark.parametrize("rel", sorted(ALLOWED))
    def test_toda_excecao_tem_justificativa_nao_vazia(self, rel: str) -> None:
        assert ALLOWED[rel].strip()
