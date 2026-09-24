"""Gera o inventário de consumidores de ORIGEM e de CIFRA (S9, BACK 09.6 — R6).

O PRD exige que este inventário seja **gerado por grep e versionado**, não
escrito à mão: lista feita à mão envelhece em silêncio, e o que interessa aqui é
justamente saber quando um call site NOVO aparece.

O que ele varre: os três símbolos do PRD (o vocabulário de ANTES) **e** os da
porta nova (o vocabulário de DEPOIS). Os dois conjuntos porque, com só os três
antigos, um consumidor sumiria do inventário justamente por ter sido convertido
— e a lista deixaria de responder "quem depende de origem HOJE?".

E classifica cada arquivo em UMA das quatro famílias da tabela abaixo. Arquivo
que não estiver na tabela reprova o teste
(`tests/unit/test_origin_consumers_inventory.py`) com a mensagem apontando o R6.

⚠️ **A distinção que mais importa** (interpretação registrada como decisão, ver
ADR-050-BE): `load_client_cipher`/`provision_client_cipher` que cifram **dado do
tenant** (nome de arquivo, descrição, nota, glossário) NÃO são consumidores de
origem e **não** viram dependência de conexão. Converter cifra de dado em
dependência de origem faria o glossário de um cliente sem Omie parar de
funcionar — o oposto do que a sprint quer.

Uso:

    cd apps/api
    uv run python scripts/gen_origin_consumers_inventory.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

#: Os três símbolos do PRD — o vocabulário de ANTES da sprint.
LEGACY_SYMBOLS = ("build_omie_client", "load_client_cipher", "provision_client_cipher")

#: E o vocabulário de DEPOIS. Sem eles, um consumidor convertido sumiria do
#: inventário justamente por ter sido convertido — e a lista deixaria de
#: responder "quem depende de origem hoje?", que é a pergunta que importa.
ORIGIN_SYMBOLS = (
    "build_capable_client",
    "build_origin_client",
    "client_from_credentials",
    "resolve_capable_connection",
    "resolve_origin_connections",
)

TRACKED_SYMBOLS = (*LEGACY_SYMBOLS, *ORIGIN_SYMBOLS)

OUTPUT = _PROJECT_ROOT / "docs" / "origin-consumers-sprint9.md"


class Family(StrEnum):
    """Como aquele call site se relaciona com a origem."""

    #: Falava com a origem e passou a resolver a conexão pelo contrato (09.2/09.5).
    ORIGEM = "origem — convertido para o contrato"
    #: Cifra/decifra DADO DO TENANT. Não é origem, não converte.
    CIFRA_DADO = "cifra de dado do tenant — declarado, sem conversão"
    #: Declara os helpers/constantes. Não é call site.
    DEFINICAO = "definição"
    #: Fallback e conversão do R2 (09.5).
    FALLBACK = "fallback / conversão do R2"


@dataclass(frozen=True)
class Entry:
    path: str
    family: Family
    note: str


#: A tabela. Um arquivo por linha, classificado e justificado. É ELA que o teste
#: compara com o resultado do grep: arquivo novo sem entrada → vermelho.
CLASSIFICATION: tuple[Entry, ...] = (
    Entry(
        "app/core/crypto_service.py",
        Family.DEFINICAO,
        "declara `load_client_cipher` e `provision_client_cipher`",
    ),
    Entry(
        "app/integrations/omie/mock_client.py",
        Family.DEFINICAO,
        "docstring do cliente-demo; a resolução mora no adaptador (09.2)",
    ),
    Entry(
        "app/integrations/omie/lancamento_cache.py",
        Family.DEFINICAO,
        "docstring: recebe o client já construído pelo caller, não o constrói",
    ),
    Entry(
        "app/modules/client_chart_of_accounts/service.py",
        Family.ORIGEM,
        "sincroniza o plano de contas (S10): resolve a conexão capaz e constrói o client pela "
        "PORTA, sem tocar em credencial nem nas colunas antigas",
    ),
    Entry(
        "app/modules/client_titles/service.py",
        Family.ORIGEM,
        "ingestão da carteira de títulos (S11): resolve a conexão capaz de "
        "`listar_titulos_em_aberto` e constrói o provedor pela PORTA. Não toca em credencial "
        "nem nas colunas antigas, e não fala com o `OmieClient` direto",
    ),
    Entry(
        "app/modules/client_titles/routes.py",
        Family.ORIGEM,
        "lista da carteira (S11): resolve a conexão capaz de `listar_lancamentos` só para "
        "resolver NOME de devedor em runtime (§4.5), e é FAIL-SOFT — sem origem alcançável a "
        "lista sai com o código e `supplierNameResolved=false`, nunca 409 numa leitura",
    ),
    Entry(
        "app/modules/client_connections/legacy_fallback.py",
        Family.FALLBACK,
        "único leitor das colunas antigas; sintetiza a conexão da janela (09.5)",
    ),
    Entry(
        "app/modules/client_connections/origin.py",
        Family.ORIGEM,
        "a PORTA: resolve conexão capaz + decifra a credencial dela",
    ),
    Entry(
        "app/modules/client_connections/service.py",
        Family.ORIGEM,
        "CRUD de conexão: cifra e decifra a credencial DA CONEXÃO",
    ),
    Entry(
        "app/modules/clients/accounts_cache.py",
        Family.ORIGEM,
        "sync de contas por CONEXÃO (TTL em `client_connections.accounts_synced_at`)",
    ),
    Entry(
        "app/modules/clients/repository.py",
        Family.FALLBACK,
        "projeta o predicado do cliente legado em `WHERE` (`legacy_origin_available`) para "
        "derivar `origin_status` sem N+1 — a MESMA decisão de `resolve_origin_connections`, "
        "em SQL. Não constrói client de provedor nem lê credencial",
    ),
    Entry(
        "app/modules/clients/service.py",
        Family.ORIGEM,
        "detalhe (200 sempre) e sync manual (409 acionável); crypto-shredding do encerramento",
    ),
    Entry(
        "app/modules/glossary/service.py",
        Family.CIFRA_DADO,
        "cifra ENTRADAS DO GLOSSÁRIO do tenant — nada a ver com origem. "
        "Verificado em 22/09: nenhuma chamada ao provedor. NÃO converter",
    ),
    Entry(
        "app/modules/omie_data/routes.py",
        Family.ORIGEM,
        "categorias e lançamentos: resolve conexão capaz de `listar_lancamentos`",
    ),
    Entry(
        "app/modules/reconciliations/routes.py",
        Family.ORIGEM,
        "criação exige `listar_lancamentos` ANTES de gravar; lançamento exige `escrever`. "
        "Os demais usos do cipher aqui cifram descrição de linha (cifra de dado)",
    ),
    Entry(
        "app/modules/reconciliations/service.py",
        Family.CIFRA_DADO,
        "cifra nome de arquivo e descrição das linhas — dado do tenant",
    ),
    Entry(
        "app/modules/reconciliations/export/routes.py",
        Family.ORIGEM,
        "enriquecimento de nomes em runtime: resolve conexão capaz",
    ),
    Entry(
        "app/modules/reconciliations/export/service.py",
        Family.CIFRA_DADO,
        "decifra descrição/nota para a planilha — dado do tenant",
    ),
    Entry(
        "app/modules/reconciliations/processing/job.py",
        Family.ORIGEM,
        "resolve a conexão DENTRO da sessão e carrega a credencial; auth recusada "
        "marca a conexão em erro. O cipher aqui também cifra descrição (dado)",
    ),
    Entry(
        "app/modules/reconciliations/review/routes.py",
        Family.ORIGEM,
        "lançamentos disponíveis e aba de divergências (esta com fail-soft anterior à sprint)",
    ),
    Entry(
        "app/modules/reconciliations/review/service.py",
        Family.CIFRA_DADO,
        "cifra/decifra nota do analista e contexto de anomalia — dado do tenant",
    ),
)

_BY_PATH = {entry.path: entry for entry in CLASSIFICATION}


def scan(root: Path | None = None) -> dict[str, list[str]]:
    """Arquivo (relativo a `apps/api/`) → símbolos rastreados que ele cita."""
    base = root or _PROJECT_ROOT
    app_dir = base / "app"
    found: dict[str, list[str]] = {}
    for path in sorted(app_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        hits = [symbol for symbol in TRACKED_SYMBOLS if symbol in text]
        if hits:
            found[path.relative_to(base).as_posix()] = hits
    return found


def unclassified(found: dict[str, list[str]]) -> list[str]:
    """Arquivos que o grep achou e a tabela não conhece."""
    return sorted(path for path in found if path not in _BY_PATH)


def stale(found: dict[str, list[str]]) -> list[str]:
    """Entradas da tabela que o grep não acha mais (arquivo removido ou limpo)."""
    return sorted(path for path in _BY_PATH if path not in found)


def render(found: dict[str, list[str]]) -> str:
    """O markdown do inventário. Determinístico — a ordem é a do caminho."""
    lines = [
        "# Consumidores de origem e de cifra — Sprint 9 (BACK 09.6)",
        "",
        "> **GERADO** por `scripts/gen_origin_consumers_inventory.py`. Não editar à mão:",
        "> `tests/unit/test_origin_consumers_inventory.py` regenera e compara.",
        "",
        f"Símbolos rastreados: {' · '.join(f'`{s}`' for s in TRACKED_SYMBOLS)}",
        "",
        "## Por que duas famílias diferentes",
        "",
        "`load_client_cipher`/`provision_client_cipher` aparecem em dois papéis que",
        "**não podem ser confundidos**: decifrar a CREDENCIAL de uma origem (vira",
        "dependência de conexão) e cifrar DADO DO TENANT — nome de arquivo, descrição,",
        "nota do analista, glossário (não vira). Converter o segundo em dependência de",
        "origem faria o glossário de um cliente sem Omie parar de funcionar.",
        "",
        "| Arquivo | Família | Símbolos | Nota |",
        "| --- | --- | --- | --- |",
    ]
    for path in sorted(found):
        entry = _BY_PATH.get(path)
        family = entry.family.value if entry else "**NÃO CLASSIFICADO**"
        note = entry.note if entry else "acrescente a entrada em `CLASSIFICATION` (R6)"
        symbols = " · ".join(f"`{s}`" for s in found[path])
        lines.append(f"| `{path}` | {family} | {symbols} | {note} |")
    lines.extend(
        [
            "",
            "## Contagens",
            "",
        ]
    )
    for family in Family:
        total = sum(1 for path in found if path in _BY_PATH and _BY_PATH[path].family is family)
        lines.append(f"- **{family.value}**: {total}")
    lines.extend(
        [
            "",
            "## Invariante",
            "",
            "**Zero** call sites de construção de client do provedor fora de",
            "`modules/client_connections/origin.py` (que delega ao adaptador da 09.2).",
            "O antigo `modules/clients/omie_factory.py` foi REMOVIDO nesta task — ficou",
            "sem nenhum chamador.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    found = scan()
    missing = unclassified(found)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(found), encoding="utf-8")
    print(f"{OUTPUT.relative_to(_PROJECT_ROOT)}: {len(found)} arquivo(s)")
    if missing:
        print(f"NÃO CLASSIFICADOS: {missing}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
