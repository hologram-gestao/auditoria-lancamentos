'use client';

/**
 * Tela "Glossário" do cliente — Sprint 6 / R2 (FRONT 06.6).
 *
 * Lista o vocabulário contábil DAQUELE tenant (categorias, fornecedores típicos
 * e regras de auditoria). O backend filtra por `client_id` da rota, validado
 * contra o tenant do TOKEN; o front nunca manda `client_id` no body nem confia
 * num vindo da URL.
 *
 * Padrão de lista do design-system, igual a "Usuários do cliente":
 *   - paginação com **estado na URL** (view linkável, sobrevive a F5);
 *   - `PaginationBar` fixa no rodapé, mesmo com uma página só;
 *   - estados loading (skeleton proporcional) / vazio (CTA real) / erro
 *     ("Tentar novamente") tratados — nunca uma tabela muda.
 *
 * **Gating (matriz do R4 + BACK 06.3).** Ler é de todo papel com acesso ao
 * cliente — inclusive o operador, para quem o glossário é referência na
 * revisão. ESCREVER pede `manage_glossary` (gerente do cliente + equipe do
 * sistema). Por isso a rota NÃO é bloqueada por papel: o que some para o
 * operador são as ações de escrita (ocultas, não desabilitadas — botão
 * desabilitado ainda anuncia "existe algo aqui que você não pode fazer").
 * A autoridade continua sendo o backend; isto evita oferecer o que dá 403.
 *
 * **Sem filtro por tipo (decisão).** O endpoint não aceita filtro de `kind`
 * (só `page`/`pageSize` — conferido no router). Filtrar no cliente esconderia
 * entradas das outras páginas e faria o rodapé mentir o total. A coluna "Tipo"
 * com badge dá a leitura por tipo sem inventar um filtro que o servidor não
 * sabe honrar; quando o backend expuser o parâmetro, ele entra aqui.
 */

import { Plus, SquarePen, Trash2 } from 'lucide-react';
import { useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import { PaginationBar } from '@/components/ui/pagination-bar';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useGlossaryList } from '@/hooks/use-glossary';
import { readPositiveInt, useUrlState } from '@/hooks/use-url-state';
import { ApiError } from '@/lib/api/client';
import type { ListGlossaryParams } from '@/lib/api/glossary';
import { hasPermission } from '@/lib/authz';
import type { GlossaryEntry } from '@/lib/contracts';
import { useAuthStore } from '@/stores/auth';

import { GlossaryKindBadge, GlossaryUndecipherableBadge } from './glossary-badges';
import { GlossaryDeleteConfirm } from './glossary-delete-confirm';
import { GlossaryFormDrawer } from './glossary-form-drawer';

const PARAM = { page: 'page', pageSize: 'pageSize' } as const;
const DEFAULT_PAGE_SIZE = 20;

export function GlossaryScreen({ clientId }: { clientId: string }) {
  const currentUser = useAuthStore((s) => s.user);
  const canManage = hasPermission(currentUser, 'manage_glossary');

  const url = useUrlState();
  const page = readPositiveInt(url.get(PARAM.page), 1);
  const pageSize = readPositiveInt(url.get(PARAM.pageSize), DEFAULT_PAGE_SIZE);

  const queryParams = useMemo<ListGlossaryParams>(() => ({ page, pageSize }), [page, pageSize]);

  const { data, isLoading, isFetching, isError, error, refetch } = useGlossaryList(
    clientId,
    queryParams,
  );

  const [formEntry, setFormEntry] = useState<GlossaryEntry | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [deleting, setDeleting] = useState<GlossaryEntry | null>(null);

  if (currentUser === null) return null;

  const rows = data?.data.entries ?? [];
  const pagination = data?.pagination;
  // A coluna "Ações" só existe para quem escreve — senão o cabeçalho anunciaria
  // uma coluna permanentemente vazia para o operador.
  const columnCount = canManage ? 4 : 3;

  function openCreate() {
    setFormEntry(null);
    setFormOpen(true);
  }

  function openEdit(entry: GlossaryEntry) {
    setFormEntry(entry);
    setFormOpen(true);
  }

  return (
    <section aria-labelledby="glossary-heading" className="flex h-full flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 id="glossary-heading" className="text-lg font-semibold">
            Glossário
          </h2>
          <p className="text-muted-foreground text-sm">
            {canManage
              ? 'O vocabulário contábil deste cliente. A análise de classificação usa estas entradas como referência.'
              : 'O vocabulário contábil deste cliente, mantido pelo gerente. Use como referência na revisão.'}
          </p>
        </div>
        {canManage && (
          <Button type="button" onClick={openCreate}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            Nova entrada
          </Button>
        )}
      </div>

      <div className="min-h-0 flex-1" aria-busy={isFetching}>
        {isError ? (
          <ErrorState
            message={
              error instanceof ApiError
                ? error.userMessage
                : 'Não foi possível carregar o glossário.'
            }
            onRetry={() => void refetch()}
          />
        ) : (
          // Sem `overflow-x-auto` aqui: o próprio `<Table>` já embrulha num
          // `div.overflow-auto` FOCÁVEL (`ui/table.tsx`). Um segundo container
          // rolável por fora espreme as colunas em 390px em vez de rolar
          // (ADR-007-FE).
          <div className="rounded-lg border">
            <Table scrollRegionLabel="Glossário do cliente (rolável horizontalmente)">
              <TableHeader>
                <TableRow>
                  <TableHead className="whitespace-nowrap">Tipo</TableHead>
                  <TableHead className="whitespace-nowrap">Nome</TableHead>
                  <TableHead>Descrição de uso</TableHead>
                  {canManage && (
                    <TableHead className="w-28 whitespace-nowrap text-right">Ações</TableHead>
                  )}
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  <TableSkeletonRows columnCount={columnCount} />
                ) : rows.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={columnCount} className="py-12">
                      <EmptyState canManage={canManage} onCreateClick={openCreate} />
                    </TableCell>
                  </TableRow>
                ) : (
                  rows.map((entry) => (
                    <TableRow key={entry.id}>
                      <TableCell className="whitespace-nowrap">
                        <GlossaryKindBadge kind={entry.kind} />
                      </TableCell>
                      <TableCell className="font-medium">
                        <div className="flex flex-wrap items-center gap-2">
                          <span>{entry.name}</span>
                          {entry.decryptFailed && <GlossaryUndecipherableBadge />}
                        </div>
                        {entry.code ? (
                          <span className="text-muted-foreground block text-xs">
                            Código: {entry.code}
                          </span>
                        ) : null}
                      </TableCell>
                      <TableCell className="text-muted-foreground min-w-64 text-sm">
                        {entry.description ?? '—'}
                      </TableCell>
                      {canManage && (
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => openEdit(entry)}
                              aria-label={`Editar ${entry.name}`}
                            >
                              <SquarePen className="h-4 w-4" aria-hidden="true" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => setDeleting(entry)}
                              aria-label={`Remover ${entry.name}`}
                            >
                              <Trash2 className="text-destructive h-4 w-4" aria-hidden="true" />
                            </Button>
                          </div>
                        </TableCell>
                      )}
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </div>
        )}
      </div>

      {!isError && (
        <PaginationBar
          page={pagination?.page ?? page}
          pageSize={pagination?.pageSize ?? pageSize}
          total={pagination?.total ?? 0}
          totalPages={pagination?.totalPages ?? 0}
          onPageChange={(next) => url.setMany({ [PARAM.page]: String(next) })}
          onPageSizeChange={(next) =>
            url.setMany({ [PARAM.pageSize]: String(next), [PARAM.page]: null })
          }
          disabled={isLoading}
          itemLabel="entradas"
        />
      )}

      {/* Gaveta e confirmação só existem para quem escreve — não basta esconder
          o botão se o componente continua montado e alcançável por teclado. */}
      {canManage && (
        <>
          {/* Remount por `key`: abrir "editar" depois de "criar" (ou trocar de
              entrada) precisa nascer com o formulário limpo. */}
          <GlossaryFormDrawer
            key={formEntry?.id ?? 'new'}
            open={formOpen}
            onOpenChange={setFormOpen}
            clientId={clientId}
            entry={formEntry}
          />

          <GlossaryDeleteConfirm
            open={deleting !== null}
            onOpenChange={(open) => !open && setDeleting(null)}
            clientId={clientId}
            entry={deleting}
          />
        </>
      )}
    </section>
  );
}

function TableSkeletonRows({ columnCount }: { columnCount: number }) {
  return (
    <>
      {Array.from({ length: 4 }).map((_, index) => (
        <TableRow key={index} aria-hidden="true">
          {Array.from({ length: columnCount }).map((__, cell) => (
            <TableCell key={cell}>
              <div className="bg-muted h-4 w-full animate-pulse rounded" />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}

function EmptyState({
  canManage,
  onCreateClick,
}: {
  canManage: boolean;
  onCreateClick: () => void;
}) {
  if (!canManage) {
    return (
      <div className="space-y-1 text-center">
        <p className="text-sm font-medium">Este cliente ainda não tem glossário</p>
        <p className="text-muted-foreground text-sm">
          Quando o gerente cadastrar categorias, fornecedores e regras, elas aparecem aqui.
        </p>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center gap-3 text-center">
      <div className="space-y-1">
        <p className="text-sm font-medium">Nenhuma entrada no glossário deste cliente</p>
        <p className="text-muted-foreground text-sm">
          Cadastre a primeira categoria, fornecedor típico ou regra de auditoria.
        </p>
      </div>
      <Button type="button" onClick={onCreateClick}>
        <Plus className="h-4 w-4" aria-hidden="true" />
        Nova entrada
      </Button>
    </div>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div
      role="alert"
      className="border-destructive/30 bg-destructive/5 text-destructive space-y-3 rounded-lg border p-6"
    >
      <p className="text-sm font-medium">Não foi possível carregar o glossário</p>
      <p className="text-sm">{message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Tentar novamente
      </Button>
    </div>
  );
}
