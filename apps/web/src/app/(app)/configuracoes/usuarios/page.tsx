'use client';

/**
 * Tela de Gestão de Usuários — Doc §8.2 (admin-only).
 *
 * Estrutura:
 *   - Breadcrumb "Configurações > Usuários"
 *   - Busca debounced (300ms) + botão "Novo Usuário"
 *   - Tabela com Nome, E-mail, Perfil (badge), Status (badge), Cadastro, Ações
 *   - Paginação "Mostrando X-Y de Z" + setas
 *   - Modais: criar, editar, desativar (componentes em features/users/)
 *
 * Defesa em profundidade contra acesso de Manager:
 *   - Middleware Next libera todas rotas autenticadas (não decodifica JWT).
 *   - Esta página, sendo client component, redireciona para /clientes se o
 *     `user.role` no Zustand não for admin.
 *   - Backend retorna 403 em todas as rotas /api/v1/users (RBAC).
 */

import { format } from 'date-fns';
import { ptBR } from 'date-fns/locale';
import {
  ChevronLeft,
  ChevronRight,
  PowerOff,
  Power,
  Search,
  SquarePen,
  UserPlus,
} from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';
import { toast } from 'sonner';

import { CreateUserModal } from '@/components/features/users/create-user-modal';
import { DeactivateConfirm } from '@/components/features/users/deactivate-confirm';
import { EditUserModal } from '@/components/features/users/edit-user-modal';
import { UserRoleBadge, UserStatusBadge } from '@/components/features/users/user-badges';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import { useActivateUser, useUsersList } from '@/hooks/use-users';
import { ApiError } from '@/lib/api/client';
import type { User } from '@/lib/api/users';
import { cn } from '@/lib/utils';
import { useAuthStore } from '@/stores/auth';

const PAGE_SIZE = 20;

export default function UsersPage() {
  const router = useRouter();
  const currentUser = useAuthStore((s) => s.user);

  // Defesa em profundidade — manager logado é jogado de volta para /clientes.
  useEffect(() => {
    if (currentUser !== null && currentUser.role !== 'admin') {
      router.replace('/clientes');
    }
  }, [currentUser, router]);

  const [searchInput, setSearchInput] = useState('');
  const debouncedSearch = useDebouncedValue(searchInput, 300);
  const [page, setPage] = useState(1);

  // Reseta a paginação quando a busca muda (UX padrão).
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch]);

  const queryParams = useMemo(
    () => ({ page, pageSize: PAGE_SIZE, search: debouncedSearch || undefined }),
    [page, debouncedSearch],
  );
  const { data, isLoading, isFetching, isError, error } = useUsersList(queryParams);

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<User | null>(null);
  const [deactivating, setDeactivating] = useState<User | null>(null);

  const activateMutation = useActivateUser();

  async function handleActivate(user: User) {
    try {
      await activateMutation.mutateAsync(user.id);
      toast.success('Usuário reativado.');
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.userMessage : 'Não foi possível reativar o usuário.';
      toast.error(msg);
    }
  }

  if (currentUser === null || currentUser.role !== 'admin') {
    return null;
  }

  const total = data?.pagination.total ?? 0;
  const rows = data?.data ?? [];
  const totalPages = data?.pagination.totalPages ?? 0;
  const rangeStart = total === 0 ? 0 : (page - 1) * PAGE_SIZE + 1;
  const rangeEnd = Math.min(page * PAGE_SIZE, total);

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <p className="text-muted-foreground text-sm">Configurações &gt; Usuários</p>
        <h1 className="text-2xl font-semibold">Usuários</h1>
        <p className="text-muted-foreground text-sm">
          Crie, edite, ative e desative usuários internos da Hologram.
        </p>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative max-w-sm flex-1">
          <Search
            className="text-muted-foreground absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
            aria-hidden="true"
          />
          <Input
            value={searchInput}
            onChange={(e) => setSearchInput(e.target.value)}
            placeholder="Buscar por nome ou e-mail..."
            className="pl-9"
            aria-label="Buscar usuários"
          />
        </div>
        <Button onClick={() => setCreateOpen(true)}>
          <UserPlus className="h-4 w-4" aria-hidden="true" />
          Novo Usuário
        </Button>
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Nome</TableHead>
              <TableHead>E-mail</TableHead>
              <TableHead>Perfil</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Cadastrado em</TableHead>
              <TableHead className="w-28 text-right">Ações</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell colSpan={6} className="text-muted-foreground py-10 text-center text-sm">
                  Carregando usuários...
                </TableCell>
              </TableRow>
            ) : isError ? (
              <TableRow>
                <TableCell colSpan={6} className="text-destructive py-10 text-center text-sm">
                  {error instanceof ApiError
                    ? error.userMessage
                    : 'Não foi possível carregar a lista.'}
                </TableCell>
              </TableRow>
            ) : rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={6} className="text-muted-foreground py-10 text-center text-sm">
                  Nenhum usuário encontrado.
                </TableCell>
              </TableRow>
            ) : (
              rows.map((u) => {
                const isSelf = u.id === currentUser.id;
                return (
                  <TableRow key={u.id} className={cn(!u.active && 'opacity-60')}>
                    <TableCell className="font-medium">{u.name}</TableCell>
                    <TableCell className="text-muted-foreground">{u.email}</TableCell>
                    <TableCell>
                      <UserRoleBadge role={u.role} />
                    </TableCell>
                    <TableCell>
                      <UserStatusBadge active={u.active} />
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {format(new Date(u.created_at), "dd 'de' MMM 'de' yyyy", { locale: ptBR })}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => setEditing(u)}
                          aria-label={`Editar ${u.name}`}
                        >
                          <SquarePen className="h-4 w-4" aria-hidden="true" />
                        </Button>
                        {!isSelf &&
                          (u.active ? (
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => setDeactivating(u)}
                              aria-label={`Desativar ${u.name}`}
                            >
                              <PowerOff className="text-destructive h-4 w-4" aria-hidden="true" />
                            </Button>
                          ) : (
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => handleActivate(u)}
                              disabled={activateMutation.isPending}
                              aria-label={`Reativar ${u.name}`}
                            >
                              <Power className="h-4 w-4 text-emerald-600" aria-hidden="true" />
                            </Button>
                          ))}
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })
            )}
          </TableBody>
        </Table>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-muted-foreground text-sm" aria-live="polite">
          {total === 0 ? 'Nenhum resultado.' : `Mostrando ${rangeStart}–${rangeEnd} de ${total}`}
          {isFetching && total > 0 ? ' · atualizando...' : ''}
        </p>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page <= 1 || isLoading}
          >
            <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            Anterior
          </Button>
          <span className="text-muted-foreground text-sm">
            Página {page}
            {totalPages > 0 ? ` de ${totalPages}` : ''}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPage((p) => p + 1)}
            disabled={page >= totalPages || isLoading || totalPages === 0}
          >
            Próxima
            <ChevronRight className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      </div>

      <CreateUserModal open={createOpen} onOpenChange={setCreateOpen} />
      <EditUserModal
        open={editing !== null}
        onOpenChange={(o) => !o && setEditing(null)}
        user={editing}
        currentUserId={currentUser.id}
      />
      <DeactivateConfirm
        open={deactivating !== null}
        onOpenChange={(o) => !o && setDeactivating(null)}
        user={deactivating}
      />
    </div>
  );
}
