"""Mede o cache do bloco de glossário contra a API Anthropic REAL (QA S6-FUP).

Guardrail S-2 do PRD da Sprint 6: "o glossário não pode encarecer a análise — o
bloco por cliente precisa cachear". Este script produz o NÚMERO, não a
estimativa: roda o caminho real (`analyze_pairs` → `_analyze_batch`) e lê o
`usage` que a Anthropic devolve em cada chamada.

Por que um wrapper em volta do SDK: `TokenUsage` (o agregado que o
`analyze_pairs` devolve) carrega só `cache_read_input_tokens`. O custo do
cache-WRITE vive em `cache_creation_input_tokens`, que o agregado descarta —
então o recorder abaixo captura o `usage` bruto por chamada. O caminho de
produção NÃO é alterado: o wrapper só encaminha e anota.

Uso (precisa de ANTHROPIC_API_KEY real em apps/api/.env):
    uv run python -m scripts.measure_glossary_cache
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from anthropic import AsyncAnthropic

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.db.models.client_glossary_entry import (
    MAX_DESCRIPTION_CHARS,
    MAX_ENTRIES_PER_CLIENT,
    MAX_NAME_CHARS,
    GlossaryEntryKind,
)
from app.integrations.anthropic.client import AnthropicClient
from app.modules.glossary.schemas import GlossaryEntryPlain, GlossarySnapshot
from app.modules.reconciliations.qualification.schemas import QualificationPair
from app.modules.reconciliations.qualification.semantic import (
    analyze_pairs,
    render_glossary_block,
)

CLIENT_A = UUID("aaaaaaaa-0000-4000-8000-000000000001")
CLIENT_B = UUID("bbbbbbbb-0000-4000-8000-000000000002")


# ----------------------------------------------------------------------
# Recorder: encaminha para o SDK real e guarda o `usage` de cada chamada
# ----------------------------------------------------------------------


@dataclass
class CallUsage:
    label: str
    input_tokens: int
    cache_creation: int
    cache_read: int
    output_tokens: int


class _RecordingMessages:
    def __init__(self, inner: Any, sink: list[CallUsage], label_ref: list[str]) -> None:
        self._inner = inner
        self._sink = sink
        self._label_ref = label_ref

    async def create(self, **kwargs: Any) -> Any:
        message = await self._inner.create(**kwargs)
        u = message.usage
        self._sink.append(
            CallUsage(
                label=self._label_ref[0],
                input_tokens=int(getattr(u, "input_tokens", 0) or 0),
                cache_creation=int(getattr(u, "cache_creation_input_tokens", 0) or 0),
                cache_read=int(getattr(u, "cache_read_input_tokens", 0) or 0),
                output_tokens=int(getattr(u, "output_tokens", 0) or 0),
            )
        )
        return message


class RecordingClient:
    """`_AsyncAnthropicLike` que embrulha o SDK real e anota o usage."""

    def __init__(self, inner: AsyncAnthropic) -> None:
        self._inner = inner
        self.calls: list[CallUsage] = []
        self._label_ref = ["?"]
        self.messages = _RecordingMessages(inner.messages, self.calls, self._label_ref)

    def label(self, text: str) -> None:
        self._label_ref[0] = text


# ----------------------------------------------------------------------
# Fixtures de glossário e de pares
# ----------------------------------------------------------------------


def _entry(kind: GlossaryEntryKind, code: str | None, name: str, desc: str) -> GlossaryEntryPlain:
    return GlossaryEntryPlain(
        id=uuid4(), kind=kind, code=code, name=name, description=desc, decrypt_failed=False
    )


def glossary_realista(client_id: UUID, *, marcador: str) -> GlossarySnapshot:
    """Glossário de tamanho plausível para um cliente real."""
    entries = [
        _entry(
            GlossaryEntryKind.CATEGORIA,
            "1.01.001",
            "Compra de materia-prima",
            f"Usar para compras de insumos de marcenaria {marcador}: madeira bruta, "
            "MDF, laminados e ferragens destinadas a producao. Nao usar para "
            "ferramentas nem para material de escritorio.",
        ),
        _entry(
            GlossaryEntryKind.CATEGORIA,
            "2.03.014",
            "Servicos de terceiros PF",
            "Pagamentos a marceneiros e ajudantes contratados por empreitada, sem "
            "vinculo empregaticio. Se houver vinculo, a categoria correta e folha.",
        ),
        _entry(
            GlossaryEntryKind.CATEGORIA,
            "3.02.007",
            "Frete e entrega",
            "Fretes de entrega de moveis prontos ao cliente final e transporte de "
            "insumos ate a oficina.",
        ),
        _entry(
            GlossaryEntryKind.FORNECEDOR,
            None,
            "Madeireira Sao Jorge",
            "Fornecedor recorrente de MDF e compensado. Pagamentos quinzenais por "
            "boleto, normalmente entre R$ 2.000 e R$ 8.000.",
        ),
        _entry(
            GlossaryEntryKind.FORNECEDOR,
            None,
            "Ferragens Uniao",
            "Dobradicas, corredicas e puxadores. Compras semanais de baixo valor.",
        ),
        _entry(
            GlossaryEntryKind.FORNECEDOR,
            None,
            "Cleidson Quiteria de Souza",
            "Marceneiro terceirizado. Recebe por empreitada via Pix, valores "
            "redondos. A descricao do extrato costuma trazer o nome completo.",
        ),
        _entry(
            GlossaryEntryKind.REGRA,
            None,
            "Pix para pessoa fisica",
            "Pix para PF nesta empresa e quase sempre pagamento de empreitada de "
            "marcenaria. Nao tratar como despesa pessoal do socio sem evidencia.",
        ),
        _entry(
            GlossaryEntryKind.REGRA,
            None,
            "Compras no cartao",
            "Compras em home centers no cartao corporativo sao insumo de producao, "
            "mesmo quando a descricao do extrato traz apenas o nome da loja.",
        ),
    ]
    return GlossarySnapshot(client_id=client_id, version=1, entries=tuple(entries))


def glossary_grande(client_id: UUID) -> GlossarySnapshot:
    """Pior caso DENTRO dos limites da BACK 06.3: 200 entradas no teto."""
    nome = "N" * MAX_NAME_CHARS
    desc = "D" * MAX_DESCRIPTION_CHARS
    entries = [
        _entry(GlossaryEntryKind.CATEGORIA, f"C{i:039d}"[:40], nome, desc)
        for i in range(MAX_ENTRIES_PER_CLIENT)
    ]
    return GlossarySnapshot(client_id=client_id, version=1, entries=tuple(entries))


def pares() -> list[QualificationPair]:
    return [
        QualificationPair(
            pair_id="p1",
            file_entry_id=uuid4(),
            omie_lancamento_id=1001,
            description='Pix enviado: "Cp:18236120-Cleidson Quiteria de Souza"',
            supplier="Cleidson Quiteria de Souza",
            category="Servicos de terceiros PF",
            amount=Decimal("-2800.00"),
        ),
        QualificationPair(
            pair_id="p2",
            file_entry_id=uuid4(),
            omie_lancamento_id=1002,
            description="Compra MADEIREIRA SAO JORGE LTDA",
            supplier="Madeireira Sao Jorge",
            category="Compra de materia-prima",
            amount=Decimal("-4622.96"),
        ),
    ]


# ----------------------------------------------------------------------
# Execução
# ----------------------------------------------------------------------


async def _run(
    rec: RecordingClient,
    client: AnthropicClient,
    *,
    label: str,
    client_id: UUID,
    block: str | None,
) -> None:
    rec.label(label)
    await analyze_pairs(
        pares(),
        anthropic_client=client,
        account_type="checking",
        client_id=client_id,
        glossary_block=block,
    )


async def main() -> None:
    settings = get_settings()
    setup_logging(settings)

    sdk = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY.get_secret_value())
    rec = RecordingClient(sdk)
    client = AnthropicClient(
        api_key=settings.ANTHROPIC_API_KEY,
        model=settings.ANTHROPIC_MODEL_DEFAULT,
        timeout=float(settings.ANTHROPIC_TIMEOUT_SECONDS),
        anthropic_client=rec,
    )

    snap_a = glossary_realista(CLIENT_A, marcador="alfa")
    block_a = render_glossary_block(snap_a)
    assert block_a is not None

    # Cenário 1 — duas análises consecutivas do MESMO cliente, bloco idêntico.
    await _run(rec, client, label="A#1 (1a analise)", client_id=CLIENT_A, block=block_a)
    await _run(
        rec, client, label="A#2 (2a analise, mesmo bloco)", client_id=CLIENT_A, block=block_a
    )

    # Cenário 2 — edita UMA entrada do glossário de A (invalidação esperada).
    editado = replace(
        snap_a,
        version=2,
        entries=(
            replace(snap_a.entries[0], description="DESCRICAO EDITADA para invalidar o cache."),
            *snap_a.entries[1:],
        ),
    )
    block_a_editado = render_glossary_block(editado)
    assert block_a_editado is not None
    await _run(
        rec, client, label="A#3 (apos editar entrada)", client_id=CLIENT_A, block=block_a_editado
    )

    # Cenário 3 — outro cliente não é afetado pelo bloco de A.
    block_b = render_glossary_block(glossary_realista(CLIENT_B, marcador="beta"))
    assert block_b is not None
    await _run(rec, client, label="B#1 (outro tenant)", client_id=CLIENT_B, block=block_b)

    # Cenário 4 — custo do cache-WRITE de um glossário no teto da 06.3.
    block_grande = render_glossary_block(glossary_grande(CLIENT_B))
    assert block_grande is not None
    await _run(rec, client, label="B#2 (glossario no teto)", client_id=CLIENT_B, block=block_grande)

    # ---- Relatório -----------------------------------------------------
    print("\n" + "=" * 96)
    print("MEDICAO DO CACHE DO BLOCO DE GLOSSARIO — API ANTHROPIC REAL")
    print(f"modelo: {settings.ANTHROPIC_MODEL_DEFAULT}")
    print(f"bloco A: {len(block_a)} chars | bloco A editado: {len(block_a_editado)} chars")
    print(f"bloco B: {len(block_b)} chars | bloco no teto: {len(block_grande)} chars")
    print("=" * 96)
    hdr = f"{'chamada':<32}{'input':>9}{'cache_write':>13}{'cache_read':>12}{'output':>9}"
    print(hdr)
    print("-" * 96)
    for c in rec.calls:
        print(
            f"{c.label:<32}{c.input_tokens:>9}{c.cache_creation:>13}"
            f"{c.cache_read:>12}{c.output_tokens:>9}"
        )
    print("=" * 96)


if __name__ == "__main__":
    asyncio.run(main())
