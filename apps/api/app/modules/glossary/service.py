"""Leitura decifrada do glossário por tenant (Sprint 6, BACK 06.2).

Uma responsabilidade: transformar as linhas cifradas de `client_glossary_entries`
num `GlossarySnapshot` em claro, com a versão do tenant lida na MESMA transação.

É esta função que a BACK 06.4 chama para montar o bloco de system da
qualificação. Por isso ela:

    - recebe o `client_id` **por assinatura** (nunca estado global/contextvar —
      um cliente vazando para a análise de outro é o pior defeito possível aqui);
    - devolve as entradas em ordem **determinística** (o repository ordena por
      `kind, created_at, id`), condição do cache-hit do prefixo;
    - marca a entrada indecifrável em vez de derrubar a leitura: campo vira
      `[indecifrável]` + warning `glossary_decrypt_failed`, e a 06.4 a omite do
      bloco. Célula silenciosamente vazia é proibida (CLAUDE.md §4.1).

Nunca loga plaintext, ciphertext nem IV — só `client_id`, `entry_id` e o nome do
campo (CLAUDE.md §3.3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from app.core.crypto_service import (
    AAD_GLOSSARY_CODE,
    AAD_GLOSSARY_DESCRIPTION,
    AAD_GLOSSARY_NAME,
    field_locator,
    load_client_cipher,
    provision_client_cipher,
)
from app.core.exceptions import GlossaryLimitExceededError, NotFoundError
from app.core.logging import get_logger
from app.db.models import (
    MAX_ENTRIES_PER_CLIENT,
    Client,
    ClientGlossaryEntry,
    GlossaryEntryKind,
)
from app.modules.glossary.repository import ClientGlossaryRepository
from app.modules.glossary.schemas import (
    UNDECIPHERABLE,
    GlossaryEntryPlain,
    GlossarySnapshot,
)
from app.modules.usage_events.repository import UsageEventRepository
from app.modules.usage_events.service import UsageEventService
from app.modules.users.schemas import PaginationMeta

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.authz import CurrentUser
    from app.core.config import Settings
    from app.core.crypto import ClientCipher

logger = get_logger(__name__)


async def load_glossary_snapshot(
    db: AsyncSession,
    *,
    client_id: UUID,
    cipher: ClientCipher,
    user: CurrentUser | None = None,
) -> GlossarySnapshot:
    """Glossário completo de UM tenant, decifrado e em ordem determinística.

    Args:
        db: `AsyncSession` ativa (o caller controla a transação).
        client_id: tenant a ler. Vem da SESSÃO sendo qualificada (BACK 06.4) ou
            da linha do usuário (rota) — **nunca** de payload.
        cipher: `ClientCipher` DESTE cliente. O AAD amarra cada ciphertext ao
            par (cliente, linha): o cipher do cliente A não decifra a entrada do
            cliente B nem por engano.
        user: quando existe request, o `CurrentUser` para o filtro extra de
            tenant no `SELECT` (defense-in-depth). `None` no caminho do job.

    Returns:
        `GlossarySnapshot` — `is_empty=True` quando o cliente não tem glossário,
        que é o caso "sem regressão" da qualificação atual.
    """
    repo = ClientGlossaryRepository(db)
    rows = await repo.list_all_active(client_id=client_id, user=user)
    version = await repo.get_version(client_id=client_id)

    entries = tuple(_decrypt_entry(row, cipher=cipher, client_id=client_id) for row in rows)
    return GlossarySnapshot(client_id=client_id, version=version, entries=entries)


def build_entry(
    *,
    client_id: UUID,
    kind: GlossaryEntryKind,
    name: str,
    code: str | None,
    description: str | None,
    cipher: ClientCipher,
) -> ClientGlossaryEntry:
    """Monta uma entrada NOVA já cifrada. Único lugar que cifra o glossário.

    O `id` é gerado ANTES de cifrar porque entra no AAD
    (`client‖tabela‖coluna‖pk`) — é o que impede mover um ciphertext de uma
    linha para outra, ou de um cliente para outro, e ainda decifrar.
    Cada campo recebe IV próprio (CLAUDE.md §4.2 — IV nunca se repete).
    """
    entry_id = uuid4()
    name_ct, name_iv = cipher.encrypt(name, field_locator(AAD_GLOSSARY_NAME, entry_id))
    code_ct, code_iv = _encrypt_optional(cipher, code, AAD_GLOSSARY_CODE, entry_id)
    desc_ct, desc_iv = _encrypt_optional(cipher, description, AAD_GLOSSARY_DESCRIPTION, entry_id)
    return ClientGlossaryEntry(
        id=entry_id,
        client_id=client_id,
        kind=kind.value,
        name_encrypted=name_ct,
        name_iv=name_iv,
        code_encrypted=code_ct,
        code_iv=code_iv,
        description_encrypted=desc_ct,
        description_iv=desc_iv,
    )


def apply_entry_edit(
    entry: ClientGlossaryEntry,
    *,
    kind: GlossaryEntryKind,
    name: str,
    code: str | None,
    description: str | None,
    cipher: ClientCipher,
) -> ClientGlossaryEntry:
    """Recifra os campos textuais de uma entrada existente, in-place.

    Recifrar (em vez de comparar ciphertext e só mexer no que mudou) é o
    caminho correto: com IV novo por operação, dois ciphertexts do MESMO texto
    são diferentes, então "comparar para pular" não funciona — e o AAD já amarra
    a linha, então recifrar não move nada de lugar.
    """
    entry.kind = kind.value
    entry.name_encrypted, entry.name_iv = cipher.encrypt(
        name, field_locator(AAD_GLOSSARY_NAME, entry.id)
    )
    entry.code_encrypted, entry.code_iv = _encrypt_optional(
        cipher, code, AAD_GLOSSARY_CODE, entry.id
    )
    entry.description_encrypted, entry.description_iv = _encrypt_optional(
        cipher, description, AAD_GLOSSARY_DESCRIPTION, entry.id
    )
    return entry


def _encrypt_optional(
    cipher: ClientCipher,
    value: str | None,
    aad_pair: tuple[str, str],
    pk: UUID,
) -> tuple[str | None, str | None]:
    """Campo opcional: `None` continua `None` (sem cifrar string vazia)."""
    if value is None:
        return None, None
    ct, iv = cipher.encrypt(value, field_locator(aad_pair, pk))
    return ct, iv


def _decrypt_entry(
    row: ClientGlossaryEntry,
    *,
    cipher: ClientCipher,
    client_id: UUID,
) -> GlossaryEntryPlain:
    """Decifra os 3 campos textuais de uma linha, tolerando falha por campo."""
    failed = False

    name, name_failed = _decrypt_required(
        cipher, row.name_encrypted, row.name_iv, AAD_GLOSSARY_NAME, row.id, client_id, "name"
    )
    failed = failed or name_failed

    code, code_failed = _decrypt_optional(
        cipher, row.code_encrypted, row.code_iv, AAD_GLOSSARY_CODE, row.id, client_id, "code"
    )
    failed = failed or code_failed

    description, description_failed = _decrypt_optional(
        cipher,
        row.description_encrypted,
        row.description_iv,
        AAD_GLOSSARY_DESCRIPTION,
        row.id,
        client_id,
        "description",
    )
    failed = failed or description_failed

    return GlossaryEntryPlain(
        id=row.id,
        kind=GlossaryEntryKind(row.kind),
        code=code,
        name=name,
        description=description,
        decrypt_failed=failed,
    )


def _decrypt_required(
    cipher: ClientCipher,
    ct: str,
    iv: str,
    aad_pair: tuple[str, str],
    pk: UUID,
    client_id: UUID,
    field: str,
) -> tuple[str, bool]:
    try:
        return cipher.decrypt(ct, iv, field_locator(aad_pair, pk)), False
    except Exception:
        _log_failure(client_id=client_id, entry_id=pk, field=field)
        return UNDECIPHERABLE, True


def _decrypt_optional(
    cipher: ClientCipher,
    ct: str | None,
    iv: str | None,
    aad_pair: tuple[str, str],
    pk: UUID,
    client_id: UUID,
    field: str,
) -> tuple[str | None, bool]:
    if ct is None or iv is None:
        return None, False
    try:
        return cipher.decrypt(ct, iv, field_locator(aad_pair, pk)), False
    except Exception:
        _log_failure(client_id=client_id, entry_id=pk, field=field)
        return UNDECIPHERABLE, True


def _log_failure(*, client_id: UUID, entry_id: UUID, field: str) -> None:
    """Trilha oficial da falha de decifragem (mesma convenção de review/export).

    Só IDs e o nome do campo — nunca plaintext, ciphertext ou IV. `except: pass`
    é proibido: sem este warning, glossário indecifrável viraria bloco de prompt
    vazio sem ninguém perceber.
    """
    logger.warning(
        "glossary_decrypt_failed",
        client_id=str(client_id),
        entry_id=str(entry_id),
        field=field,
    )


# ----------------------------------------------------------------------
# CRUD do glossário (BACK 06.3)
# ----------------------------------------------------------------------


class GlossaryService:
    """Regra de negócio do CRUD do glossário de um tenant.

    Três invariantes que o service — e não a rota — garante:

    1. **Teto de entradas por cliente.** É o que impede o bloco de prompt da
       BACK 06.4 de crescer sem limite (guardrail S-2/R9). O teto vem de
       `MAX_ENTRIES_PER_CLIENT`, a mesma constante que a 06.4 assume.
    2. **Toda escrita bump a versão na MESMA transação** (`repo.bump_version`),
       inclusive a REMOÇÃO — é o delete que `MAX(updated_at)` não pegaria.
    3. **Toda escrita emite `glossario_editado`**, fail-soft: falha de
       instrumentação nunca desfaz a edição do usuário (SAVEPOINT no repository
       de `usage_events`).

    O RBAC de papel fica no guard da rota (`ManageGlossaryDep`, que consulta a
    `PERMISSION_MATRIX`) e o de tenant em `AccessibleClientDep` +
    `scoped_by_tenant` dentro do SELECT. Aqui não se compara `role` nem
    `client_id` na mão.
    """

    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._repo = ClientGlossaryRepository(db)
        self._events = UsageEventService(UsageEventRepository(db))
        self._settings = settings

    async def list_entries(
        self,
        *,
        client: Client,
        user: CurrentUser,
        page: int,
        page_size: int,
    ) -> tuple[list[GlossaryEntryPlain], int, PaginationMeta]:
        """Página decifrada + versão do glossário + metadados de paginação."""
        cipher = await load_client_cipher(client, settings=self._settings)
        rows, total = await self._repo.list_page(
            client_id=client.id, user=user, page=page, page_size=page_size
        )
        entries = [_decrypt_entry(row, cipher=cipher, client_id=client.id) for row in rows]
        version = await self._repo.get_version(client_id=client.id)
        total_pages = (total + page_size - 1) // page_size if page_size else 0
        pagination = PaginationMeta(
            page=page, page_size=page_size, total=total, total_pages=total_pages
        )
        return entries, version, pagination

    async def create_entry(
        self,
        *,
        client: Client,
        kind: GlossaryEntryKind,
        name: str,
        code: str | None,
        description: str | None,
    ) -> GlossaryEntryPlain:
        """Cria uma entrada, respeitando o teto do tenant."""
        current = await self._repo.count_active(client_id=client.id)
        if current >= MAX_ENTRIES_PER_CLIENT:
            raise GlossaryLimitExceededError(
                f"Cliente {client.id} já tem {current} entradas ativas "
                f"(teto {MAX_ENTRIES_PER_CLIENT}).",
            )

        cipher = await provision_client_cipher(client, settings=self._settings)
        entry = build_entry(
            client_id=client.id,
            kind=kind,
            name=name,
            code=code,
            description=description,
            cipher=cipher,
        )
        await self._repo.add(entry)
        await self._after_write(client_id=client.id)
        return _decrypt_entry(entry, cipher=cipher, client_id=client.id)

    async def update_entry(
        self,
        *,
        client: Client,
        user: CurrentUser,
        entry_id: UUID,
        kind: GlossaryEntryKind,
        name: str,
        code: str | None,
        description: str | None,
    ) -> GlossaryEntryPlain:
        """Edita uma entrada DO tenant. Entrada de outro tenant → 404."""
        entry = await self._require_entry(client=client, user=user, entry_id=entry_id)
        cipher = await provision_client_cipher(client, settings=self._settings)
        apply_entry_edit(
            entry,
            kind=kind,
            name=name,
            code=code,
            description=description,
            cipher=cipher,
        )
        await self._db.flush()
        await self._after_write(client_id=client.id)
        return _decrypt_entry(entry, cipher=cipher, client_id=client.id)

    async def delete_entry(
        self,
        *,
        client: Client,
        user: CurrentUser,
        entry_id: UUID,
    ) -> int:
        """Remoção LÓGICA. Devolve a versão nova do glossário do tenant."""
        entry = await self._require_entry(client=client, user=user, entry_id=entry_id)
        await self._repo.soft_delete(entry)
        return await self._after_write(client_id=client.id)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _require_entry(
        self, *, client: Client, user: CurrentUser, entry_id: UUID
    ) -> ClientGlossaryEntry:
        """Carrega a entrada com o tenant NO PRÓPRIO SELECT (anti-IDOR).

        404 (não 403) para entrada de outro tenant: a resposta é indistinguível
        de "não existe" e não confirma a existência do recurso alheio.
        """
        entry = await self._repo.get_active(entry_id=entry_id, client_id=client.id, user=user)
        if entry is None:
            raise NotFoundError(
                f"Entrada de glossário {entry_id} não encontrada no cliente {client.id}.",
                user_message="Entrada do glossário não encontrada.",
            )
        return entry

    async def _after_write(self, *, client_id: UUID) -> int:
        """Efeito colateral obrigatório de QUALQUER escrita: versão + evento.

        Um lugar só, chamado pelos três verbos — se ficasse espalhado, o dia em
        que alguém esquecer no `delete` o cache do prompt serviria glossário
        removido, e a suposição S-1 deixaria de ser medida.
        """
        version = await self._repo.bump_version(client_id=client_id)
        n_entries = await self._repo.count_active(client_id=client_id)
        await self._events.emit_glossario_editado(client_id=client_id, n_categorias=n_entries)
        return version
