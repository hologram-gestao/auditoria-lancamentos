'use client';

/**
 * Tela "Usuários" do cliente — Sprint 5 / R5 (FRONT 05.6).
 *
 * Lista os usuários DAQUELE tenant (o backend filtra por `client_id`; o front
 * nunca manda `client_id` no body nem confia num vindo da URL), com gaveta de
 * criação/edição e ativar/desativar por `AlertDialog`.
 *
 * Padrão de lista do design-system, igual à Lista de Conciliações:
 *   - busca e paginação com **estado na URL** (view linkável, sobrevive a F5);
 *   - `PaginationBar` fixa no rodapé, mesmo com uma página só;
 *   - estados loading (skeleton proporcional) / vazio (CTA real) / erro
 *     ("Tentar novamente") tratados — nunca uma tabela muda.
 *
 * **Gating.** Quem administra usuários do tenant é o gerente do cliente e o
 * admin do sistema (matriz do R4, consultada por `hasPermission` — a única
 * função de permissão do front). Operador do cliente e gerente do sistema não
 * veem o item de menu; se forçarem a URL, caem no `AccessDenied` em vez de num
 * 403 cru. A autoridade continua sendo o backend.
 */

import { Plus, Power, PowerOff, Search, SquarePen } from 'lucide-react';
import { useMemo, useState } from 'react';

import { AccessDenied } from '@/components/shared/access-denied';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { PaginationBar } from '@/components/ui/pagination-bar';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useClientUsersList } from '@/hooks/use-client-users';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import { readPositiveInt, useUrlState } from '@/hooks/use-url-state';
import { ApiError } from '@/lib/api/client';
import type { ListClientUsersParams } from '@/lib/api/client-users';
import { hasPermission } from '@/lib/authz';
import type { ClientUserResponse } from '@/lib/contracts';
import { useAuthStore } from '@/stores/auth';

import { ClientUserRoleBadge, ClientUserStatusBadge } from './client-user-badges';
import { ClientUserFormDrawer } from './client-user-form-drawer';
import { ClientUserToggleConfirm } from './client-user-toggle-confirm';

const PARAM = { search: 'busca', page: 'page', pageSize: 'pageSize' } as const;
const DEFAULT_PAGE_SIZE = 20;
const COLUMN_COUNT = 5;

export function ClientUsersScreen({ clientId }: { clientId: string }) {
  const currentUser = useAuthStore((s) => s.user);
  const canManage = hasPermission(currentUser, 'manage_client_users');

  const url = useUrlState();
  const searchParam = url.get(PARAM.search) ?? '';
  const page = readPositiveInt(url.get(PARAM.page), 1);
  const pageSize = readPositiveInt(url.get(PARAM.pageSize), DEFAULT_PAGE_SIZE);

  // O input é local (digitar não pode fazer um `replace` por tecla); a URL só
  // recebe o valor DEBOUNCED, que também é o que vai para o backend.
  const [searchInput, setSearchInput] = useState(searchParam);
  const debouncedSearch = useDebouncedValue(searchInput, 300);

  const queryParams = useMemo<ListClientUsersParams>(() => {
    const params: ListClientUsersParams = { page, pageSize };
    const term = debouncedSearch.trim();
    if (term) params.search = term;
    return params;
  }, [page, pageSize, debouncedSearch]);

  const { data, isLoading, isFetching, isError, error, refetch } = useClientUsersList(
    clientId,
    queryParams,
    { enabled: canManage },
  );

  const [formUser, setFormUser] = useState<ClientUserResponse | null>(null);
  const [formOpen, setFormOpen] = useState(false);
  const [toggling, setToggling] = useState<ClientUserResponse | null>(null);

  if (currentUser === null) return null;

  if (!canManage) {
    return (
      <AccessDenied
        title="Você não tem acesso a esta página"
        message="A gestão de usuários deste cliente é restrita ao gerente do cliente. Fale com ele se precisar de acesso."
        backHref={`/clientes/${clientId}`}
        backLabel="Voltar para as conciliações"
      />
    );
  }

  const rows = data?.data ?? [];
  const pagination = data?.pagination;
  const hasSearch = debouncedSearch.trim().length > 0;

  function handleSearchChange(value: string) {
    setSearchInput(value);
    // Buscar volta para a página 1 no mesmo `replace`: sem isso, quem está na
    // página 3 e busca algo com 1 resultado cai numa página vazia.
    url.setMany({ [PARAM.search]: value || null, [PARAM.page]: null });
  }

  function openCreate() {
    setFormUser(null);
    setFormOpen(true);
  }

  function openEdit(user: ClientUserResponse) {
    setFormUser(user);
    setFormOpen(true);
  }

  return (
    <section aria-labelledby="client-users-heading" className="flex h-full flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 id="client-users-heading" className="text-lg font-semibold">
            Usuários
          </h2>
          <p className="text-muted-foreground text-sm">
            Pessoas deste cliente que acessam o sistema. Elas enxergam apenas este cliente.
          </p>
        </div>
        <Button type="button" onClick={openCreate}>
          <Plus className="h-4 w-4" aria-hidden="true" />
          Novo usuário
        </Button>
      </div>

      <div className="relative max-w-sm">
        <Search
          className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
          aria-hidden="true"
        />
        <Input
          value={searchInput}
          onChange={(e) => handleSearchChange(e.target.value)}
          placeholder="Buscar por nome ou e-mail..."
          className="pl-9"
          aria-label="Buscar usuários deste cliente"
        />
      </div>

      <div className="min-h-0 flex-1" aria-busy={isFetching}>
        {isError ? (
          <ErrorState
            message={
              error instanceof ApiError
                ? error.userMessage
                : 'Não foi possível carregar os usuários.'
            }
            onRetry={() => void refetch()}
          />
        ) : (
          // Sem `overflow-x-auto` aqui: o próprio `<Table>` já embrulha num
          // `div.overflow-auto` FOCÁVEL (`ui/table.tsx`). Um segundo container
          // rolável por fora fazia a tabela espremer as colunas em 390px em vez
          // de rolar — o e-mail quebrava uma letra por linha e a coluna "Ações"
          // ficava fora da tela.
          <div className="rounded-lg border">
            <Table scrollRegionLabel="Usuários do cliente (rolável horizontalmente)">
              <TableHeader>
                <TableRow>
                  <TableHead className="whitespace-nowrap">Nome</TableHead>
                  <TableHead className="whitespace-nowrap">E-mail</TableHead>
                  <TableHead className="whitespace-nowrap">Papel</TableHead>
                  <TableHead className="whitespace-nowrap">Status</TableHead>
                  <TableHead className="w-28 whitespace-nowrap text-right">Ações</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading ? (
                  <TableSkeletonRows />
                ) : rows.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={COLUMN_COUNT} className="py-12">
                      <EmptyState hasSearch={hasSearch} onCreateClick={openCreate} />
                    </TableCell>
                  </TableRow>
                ) : (
                  rows.map((user) => (
                    // Sem `opacity-60` na linha inativa: o axe em browser real
                    // mediu 2.57:1 no e-mail e 2.4–2.8:1 nas badges por causa
                    // dela (3 violações `serious` de uma vez). Quem sinaliza o
                    // estado é a badge "Inativo" — esmaecer a linha inteira
                    // troca informação por decoração ilegível.
                    <TableRow key={user.id}>
                      <TableCell className="whitespace-nowrap font-medium">{user.name}</TableCell>
                      <TableCell className="text-muted-foreground whitespace-nowrap">
                        {user.email}
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        <ClientUserRoleBadge role={user.role} />
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        <ClientUserStatusBadge active={user.active} />
                      </TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => openEdit(user)}
                            aria-label={`Editar ${user.name}`}
                          >
                            <SquarePen className="h-4 w-4" aria-hidden="true" />
                          </Button>
                          {/* Ninguém se desativa (o backend devolve 403); a
                              ação some para não oferecer o que será negado. */}
                          {user.id !== currentUser.id && (
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => setToggling(user)}
                              aria-label={
                                user.active ? `Desativar ${user.name}` : `Reativar ${user.name}`
                              }
                            >
                              {user.active ? (
                                <PowerOff className="text-destructive h-4 w-4" aria-hidden="true" />
                              ) : (
                                <Power className="text-success h-4 w-4" aria-hidden="true" />
                              )}
                            </Button>
                          )}
                        </div>
                      </TableCell>
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
          itemLabel="usuários"
        />
      )}

      {/* Remount por `key`: abrir "editar" depois de "criar" (ou trocar de
          usuário) precisa nascer com o formulário limpo, sem herdar o estado
          da abertura anterior. */}
      <ClientUserFormDrawer
        key={formUser?.id ?? 'new'}
        open={formOpen}
        onOpenChange={setFormOpen}
        clientId={clientId}
        user={formUser}
      />

      <ClientUserToggleConfirm
        open={toggling !== null}
        onOpenChange={(open) => !open && setToggling(null)}
        clientId={clientId}
        user={toggling}
        nextActive={toggling ? !toggling.active : false}
      />
    </section>
  );
}

function TableSkeletonRows() {
  return (
    <>
      {Array.from({ length: 4 }).map((_, index) => (
        <TableRow key={index} aria-hidden="true">
          {Array.from({ length: COLUMN_COUNT }).map((__, cell) => (
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
  hasSearch,
  onCreateClick,
}: {
  hasSearch: boolean;
  onCreateClick: () => void;
}) {
  if (hasSearch) {
    return (
      <div className="space-y-1 text-center">
        <p className="text-sm font-medium">Nenhum usuário encontrado</p>
        <p className="text-muted-foreground text-sm">Tente outro nome ou e-mail.</p>
      </div>
    );
  }
  return (
    <div className="flex flex-col items-center gap-3 text-center">
      <div className="space-y-1">
        <p className="text-sm font-medium">Nenhum usuário cadastrado neste cliente</p>
        <p className="text-muted-foreground text-sm">
          Crie o primeiro acesso para alguém da equipe do cliente.
        </p>
      </div>
      <Button type="button" onClick={onCreateClick}>
        <Plus className="h-4 w-4" aria-hidden="true" />
        Novo usuário
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
      <p className="text-sm font-medium">Não foi possível carregar os usuários</p>
      <p className="text-sm">{message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Tentar novamente
      </Button>
    </div>
  );
}
