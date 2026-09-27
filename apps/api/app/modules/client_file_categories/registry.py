"""`FileCategoryRegistry` — rótulo do arquivo → código estável, por cliente (Sprint 14, BACK 14.4 — R4).

Duas operações, e só elas:

    resolve_codes(client, labels)  → {rótulo: código}, criando o que falta
    resolve_names(client)          → {código: rótulo}, para a leitura do de-para

**Casamento em memória, byte a byte.** As categorias do cliente são carregadas
(dezenas/centenas — a MESMA escala em memória que a ADR-076-BE aceita para o
universo do de-para), os rótulos são decifrados e comparados por IGUALDADE de
`str`: sem case folding, sem tirar acento, sem colapsar espaço. "Grafia não é
normalizada" é invariante do PRD — `Aluguel`, `aluguel` e `Aluguél` são TRÊS
categorias, e fundir grafias está fora de escopo global. O único pré-processamento
é o que o LEITOR (14.3) faz para toda célula, e ele mora lá, não aqui.

**O registry é agnóstico à coluna de origem.** `coluna_categoria` ou
`classificacao_livre` (R4) é decisão do mapeamento (14.1) e leitura do ingestor
(14.3); aqui chega um conjunto de rótulos, e sai um mapa.

**A criação acontece na MESMA transação do chamador** (nada de sessão própria): a
14.3 grava os movimentos com os códigos devolvidos daqui, e uma categoria criada
numa transação que depois falha seria um código órfão. O chamador serializa por
cliente (`pg_advisory_xact_lock`), então duas requests não criam a mesma grafia
duas vezes; a UNIQUE `(client_id, code)` protege o código, não o rótulo — pelo
desenho, o rótulo não é comparável no banco (ciphertext com IV novo por linha).

**Falha de decifragem = `[indecifrável]` + warning só com IDs** (§4.1), nunca
célula vazia silenciosa. Na leitura, o código sai com `categoryNameResolved=false`;
na escrita, uma linha indecifrável não casa com rótulo nenhum e a grafia ganha um
código novo — o que só acontece depois de um crypto-shredding, que é o
encerramento, e cliente encerrado não processa arquivo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

from app.core.crypto_service import (
    AAD_FILE_CATEGORY_LABEL,
    field_locator,
    load_client_cipher,
    provision_client_cipher,
)
from app.core.logging import get_logger
from app.db.models.client_file_category import ClientFileCategory, new_file_category_code
from app.modules.client_file_categories.repository import ClientFileCategoryRepository

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.config import Settings
    from app.core.crypto import ClientCipher
    from app.db.models import Client

log = get_logger(__name__)

#: O marcador de rótulo indecifrável — o MESMO do glossário e do contexto do título.
FILE_CATEGORY_UNDECIPHERABLE = "[indecifrável]"


@dataclass(frozen=True, slots=True)
class ResolvedFileCategoryNames:
    """`código → rótulo` decifrado na leitura, e os códigos que NÃO decifraram."""

    names: dict[str, str] = field(default_factory=dict)
    failed: frozenset[str] = frozenset()


def split_labels(
    existing: Mapping[str, str], labels: Iterable[str]
) -> tuple[dict[str, str], list[str]]:
    """Função PURA do casamento: `(já conhecidos: rótulo → código, novos em ordem)`.

    `existing` é `rótulo → código` das linhas decifradas. Igualdade de `str`,
    byte a byte — o teste unitário prova que `Aluguel`/`aluguel`/`Aluguél` não se
    encontram. `labels` pode repetir: o resultado é por rótulo DISTINTO, na ordem
    de primeira ocorrência (determinístico para o teste e para o log).
    """
    known: dict[str, str] = {}
    missing: list[str] = []
    seen: set[str] = set()
    for label in labels:
        if label in seen:
            continue
        seen.add(label)
        code = existing.get(label)
        if code is None:
            missing.append(label)
        else:
            known[label] = code
    return known, missing


class FileCategoryRegistry:
    def __init__(
        self,
        db: AsyncSession,
        *,
        settings: Settings,
        repository: ClientFileCategoryRepository | None = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self._repo = repository or ClientFileCategoryRepository(db)

    # ------------------------------------------------------------------ escrita

    async def resolve_codes(self, client: Client, labels: set[str]) -> dict[str, str]:
        """`rótulo → código` para TODOS os rótulos, criando os que o cliente não tem.

        Cria na transação do chamador (flush, sem commit). Os códigos novos são
        aleatórios (`arq-<hex>`), sem relação com o texto do rótulo (R4).
        `provision_client_cipher` e não `load_`: a 14.1 já provisionou a DEK na
        conexão `arquivo`, mas um cliente que ganhou a conexão pelo caminho legado
        pode chegar aqui sem ela — e escrever exige poder cifrar.
        """
        if not labels:
            return {}
        cipher = await provision_client_cipher(client, settings=self._settings)
        existing = self._decrypt_all(client.id, cipher, await self._repo.list_for_client(client.id))
        known, missing = split_labels(
            {label: code for code, label in existing.names.items()}, sorted(labels)
        )
        for label in missing:
            known[label] = await self._create(client.id, cipher, label)
        if missing:
            log.info(
                "file_categories_created",
                client_id=str(client.id),
                created=len(missing),
                total=len(labels),
            )
        return known

    async def _create(self, client_id: UUID, cipher: ClientCipher, label: str) -> str:
        category = ClientFileCategory(
            client_id=client_id,
            code=new_file_category_code(),
            # Preenchidos depois do flush: a pk entra no AAD.
            label_encrypted="",
            label_iv="0" * 24,
        )
        await self._repo.add(category)
        envelope, iv = cipher.encrypt(label, field_locator(AAD_FILE_CATEGORY_LABEL, category.id))
        category.label_encrypted = envelope
        category.label_iv = iv
        await self._db.flush()
        return category.code

    # ------------------------------------------------------------------ leitura

    async def resolve_names(self, client: Client) -> ResolvedFileCategoryNames:
        """`código → rótulo` das categorias de arquivo do cliente, decifrado NA LEITURA.

        Só leitura: `load_client_cipher` não provisiona DEK (cliente encerrado
        continua legível — os rótulos dele saem `[indecifrável]`, que é o estado
        honesto depois do crypto-shredding).
        """
        rows = await self._repo.list_for_client(client.id)
        if not rows:
            return ResolvedFileCategoryNames()
        cipher = await load_client_cipher(client, settings=self._settings)
        return self._decrypt_all(client.id, cipher, rows)

    def _decrypt_all(
        self, client_id: UUID, cipher: ClientCipher, rows: list[ClientFileCategory]
    ) -> ResolvedFileCategoryNames:
        names: dict[str, str] = {}
        failed: set[str] = set()
        for row in rows:
            try:
                names[row.code] = cipher.decrypt(
                    row.label_encrypted,
                    row.label_iv,
                    field_locator(AAD_FILE_CATEGORY_LABEL, row.id),
                )
            except Exception:
                # Só IDs (§4.1): nunca plaintext, ciphertext ou IV. `except: pass`
                # é proibido — sem este warning a categoria sumiria da tela em
                # silêncio.
                log.warning(
                    "file_category_decrypt_failed",
                    client_id=str(client_id),
                    category_id=str(row.id),
                )
                names[row.code] = FILE_CATEGORY_UNDECIPHERABLE
                failed.add(row.code)
        return ResolvedFileCategoryNames(names=names, failed=frozenset(failed))
