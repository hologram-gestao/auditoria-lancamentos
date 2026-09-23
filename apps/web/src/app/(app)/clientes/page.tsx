'use client';

/**
 * Tela de Clientes — Doc §9.1.
 *
 * RBAC visual (matriz do R4, via `lib/authz` — nunca `role === '...'` local):
 *   - Admin: vê todas as colunas (incluindo gerente responsável) e cria/edita.
 *   - Manager do sistema: vê apenas a própria carteira; sem coluna de gerente.
 *   - Usuário DE tenant (Sprint 5): **não tem lista global**. Ele é levado para
 *     o próprio cliente — um `AccessDenied` aqui seria um beco sem saída logo
 *     no destino padrão pós-login (`middleware.ts` manda todo mundo a
 *     `/clientes`).
 *
 * O backend já filtra por carteira no GET /clients (manager nunca recebe
 * dados de outro manager) — esta tela apenas oculta visualmente a coluna
 * para reduzir poluição visual.
 *
 * Categorias (86e34jd8m): chip por linha e filtro SERVER-SIDE (`category_id`) —
 * nunca no navegador sobre a página parcial (mesma lição das somas do Resumo).
 *
 * Favoritos (86e34jd5a): coração na primeira coluna, POR USUÁRIO. O backend já
 * devolve os favoritos de quem pede no topo — a tela não reordena nada, e o
 * favorito da página 3 sobe para a 1 porque a ordem é do SELECT.
 *
 * Carteira compartilhada (86e390m4c): a coluna "Gerente Responsável" continua
 * mostrando UM nome (o responsável); um "+N" discreto ao lado sinaliza que há
 * mais gente com acesso, sem carregar nomes em cada linha.
 *
 * Click handler na linha leva pra /clientes/{id} (detalhe — S7). Os botões
 * de ação dentro da linha usam stopPropagation pra não disparar a navegação.
 */

import { format } from 'date-fns';
import { ptBR } from 'date-fns/locale';
import { ChevronLeft, ChevronRight, Eye, Plus, Search, SquarePen } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';

import { CategoryBadge } from '@/components/features/client-categories/category-badge';
import { ClientStatusBadge } from '@/components/features/clients/client-status-badge';
import { OriginStatusBadge } from '@/components/features/clients/connections/connection-badges';
import { CreateClientModal } from '@/components/features/clients/create-client-modal';
import { EditClientModal } from '@/components/features/clients/edit-client-modal';
import { FavoriteToggle } from '@/components/features/clients/favorite-toggle';
import { ManagerAccessHint } from '@/components/features/clients/manager-access-hint';
import {
  ALL_ORGANIZATIONS,
  OrganizationFilterSelect,
} from '@/components/features/organizations/organization-select';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useClientCategories } from '@/hooks/use-client-categories';
import { useClientsList } from '@/hooks/use-clients';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import { ApiError } from '@/lib/api/client';
import type { Client } from '@/lib/api/clients';
import { hasPermission, homePathFor, isClientScoped, isPlatformScoped } from '@/lib/authz';
import { cn } from '@/lib/utils';
import { useAuthStore } from '@/stores/auth';

const PAGE_SIZE_OPTIONS = [10, 20, 50] as const;
type PageSize = (typeof PAGE_SIZE_OPTIONS)[number];

export default function ClientesPage() {
  const router = useRouter();
  const currentUser = useAuthStore((s) => s.user);

  // Usuário de tenant não passa por esta lista: vai direto para o próprio
  // cliente. Presentacional — quem nega de fato é o backend.
  const clientScoped = isClientScoped(currentUser);
  useEffect(() => {
    if (clientScoped && currentUser !== null) {
      router.replace(homePathFor(currentUser));
    }
  }, [clientScoped, currentUser, router]);

  const [searchInput, setSearchInput] = useState('');
  const debouncedSearch = useDebouncedValue(searchInput, 300);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState<PageSize>(20);
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<Client | null>(null);
  // 'all' = sem filtro (o Select do Radix não aceita '' como valor).
  const [categoryFilter, setCategoryFilter] = useState<string>('all');
  // Filtro por organização (86e36ed1d) — só a plataforma o vê; para o staff o
  // backend já restringe a lista à organização da LINHA dele.
  const [organizationFilter, setOrganizationFilter] = useState<string>(ALL_ORGANIZATIONS);
  const categoriesQuery = useClientCategories({ enabled: !clientScoped });

  // O catálogo é POR organização (86e36ecqz), e a plataforma recebe o de TODAS.
  // Sem recortar, ela poderia escolher "Varejo da Hologram" com a organização
  // Prospecta filtrada e receber uma lista vazia — um par impossível, com a
  // mensagem culpando a categoria. Recorte de EXIBIÇÃO sobre dado que já veio
  // inteiro, não filtro de isolamento: quem decide o que ela alcança é o
  // servidor.
  const visibleCategories = useMemo(() => {
    const all = categoriesQuery.data ?? [];
    if (!isPlatformScoped(currentUser) || organizationFilter === ALL_ORGANIZATIONS) return all;
    return all.filter((c) => c.organization_id === organizationFilter);
  }, [categoriesQuery.data, currentUser, organizationFilter]);

  // Reseta a paginação quando a busca, os filtros ou o pageSize mudam.
  useEffect(() => {
    setPage(1);
  }, [debouncedSearch, pageSize, categoryFilter, organizationFilter]);

  const queryParams = useMemo(
    () => ({
      page,
      pageSize,
      search: debouncedSearch || undefined,
      categoryId: categoryFilter === 'all' ? undefined : categoryFilter,
      organizationId: organizationFilter === ALL_ORGANIZATIONS ? undefined : organizationFilter,
    }),
    [page, pageSize, debouncedSearch, categoryFilter, organizationFilter],
  );
  const { data, isLoading, isFetching, isError, error } = useClientsList(queryParams);

  if (currentUser === null || clientScoped) {
    // O layout pai já redireciona quando não há usuário; para o usuário de
    // tenant o `useEffect` acima já mandou para a casa dele — não renderiza a
    // lista global nem por um frame.
    return null;
  }

  // "Gerente responsável" e criar/editar cliente são §9 — admin do sistema.
  const isAdmin = hasPermission(currentUser, 'edit_client');
  // A coluna/filtro de organização é da PLATAFORMA: para o staff toda linha da
  // lista é da mesma organização, e a coluna só repetiria o mesmo nome.
  const isPlatform = isPlatformScoped(currentUser);
  const total = data?.pagination.total ?? 0;
  const rows = data?.data ?? [];
  const totalPages = data?.pagination.totalPages ?? 0;
  const colCount = 7 + (isAdmin ? 1 : 0) + (isPlatform ? 1 : 0);
  const hasSearch = debouncedSearch.length > 0;
  const hasCategoryFilter = categoryFilter !== 'all';
  const hasOrganizationFilter = organizationFilter !== ALL_ORGANIZATIONS;

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold">Clientes</h1>
        <p className="text-muted-foreground text-sm">
          {isPlatform
            ? 'Todos os clientes, de todas as organizações.'
            : isAdmin
              ? 'Gerencie todos os clientes BPO da sua organização.'
              : 'Clientes da sua carteira.'}
        </p>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-1 flex-col gap-3 sm:flex-row sm:items-center">
          <div className="relative max-w-sm flex-1">
            <Search
              className="text-muted-foreground absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
              aria-hidden="true"
            />
            <Input
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Buscar por nome..."
              className="pl-9"
              aria-label="Buscar clientes"
            />
          </div>
          {/* Filtro por categoria (86e34jd8m) — server-side, via `category_id`. */}
          <Select value={categoryFilter} onValueChange={setCategoryFilter}>
            <SelectTrigger className="w-full sm:w-56" aria-label="Filtrar por categoria">
              <SelectValue placeholder="Todas as categorias" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todas as categorias</SelectItem>
              {visibleCategories.map((c) => (
                <SelectItem key={c.id} value={c.id}>
                  {/* Sem organização escolhida, a plataforma vê duas "Varejo"
                      de donos diferentes — o nome sozinho não distingue. */}
                  {isPlatform && organizationFilter === ALL_ORGANIZATIONS
                    ? `${c.name} · ${c.organization_name}`
                    : c.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {/* Filtro por organização (86e36ed1d) — server-side, via
              `?organizationId=`. Só a plataforma: o staff que mandasse outra
              organização receberia 403 (`resolve_organization_filter`). */}
          {isPlatform && (
            <OrganizationFilterSelect
              value={organizationFilter}
              onValueChange={(value) => {
                setOrganizationFilter(value);
                // No MESMO handler (lição da 86e36ecwa com o pageSize): a
                // categoria escolhida pode ser de outra organização, e o par
                // devolveria lista vazia acusando a categoria.
                setCategoryFilter('all');
              }}
              ariaLabel="Filtrar por organização"
              className="w-full sm:w-56"
            />
          )}
        </div>
        {hasPermission(currentUser, 'create_client') && (
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            Novo Cliente
          </Button>
        )}
      </div>

      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">
                <span className="sr-only">Favorito</span>
              </TableHead>
              <TableHead>Nome</TableHead>
              {isPlatform && <TableHead>Organização</TableHead>}
              <TableHead>Categoria</TableHead>
              {isAdmin && <TableHead>Gerente Responsável</TableHead>}
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Conciliações</TableHead>
              <TableHead>Cadastrado em</TableHead>
              <TableHead className="w-28 text-right">Ações</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading ? (
              <SkeletonRows colCount={colCount} />
            ) : isError ? (
              <TableRow>
                <TableCell
                  colSpan={colCount}
                  className="text-destructive py-10 text-center text-sm"
                >
                  {error instanceof ApiError
                    ? error.userMessage
                    : 'Não foi possível carregar a lista.'}
                </TableCell>
              </TableRow>
            ) : rows.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={colCount}
                  className="text-muted-foreground py-10 text-center text-sm"
                >
                  {hasSearch
                    ? `Nenhum cliente encontrado para "${debouncedSearch}".`
                    : hasCategoryFilter
                      ? 'Nenhum cliente nesta categoria.'
                      : hasOrganizationFilter
                        ? 'Nenhum cliente nesta organização.'
                        : "Nenhum cliente cadastrado. Crie o primeiro cliente clicando em 'Novo Cliente'."}
                </TableCell>
              </TableRow>
            ) : (
              rows.map((c) => (
                <TableRow
                  key={c.id}
                  className={cn('cursor-pointer', !c.active && 'opacity-60')}
                  onClick={() => router.push(`/clientes/${c.id}`)}
                >
                  <TableCell className="w-10 pr-0">
                    <FavoriteToggle
                      clientId={c.id}
                      clientName={c.name}
                      isFavorite={c.is_favorite}
                    />
                  </TableCell>
                  <TableCell className="font-medium">
                    <div className="flex flex-wrap items-center gap-2">
                      <span>{c.name}</span>
                      {/* S9 (R7): quem está sem origem aparece na LISTA — é o
                          escritório parceiro olhando a carteira inteira e
                          vendo onde falta conectar. Selo por token semântico,
                          nunca `opacity` na linha (ADR-007-FE). */}
                      {c.origin_status === 'sem_origem' && (
                        <OriginStatusBadge status={c.origin_status} />
                      )}
                    </div>
                  </TableCell>
                  {isPlatform && (
                    <TableCell className="text-muted-foreground whitespace-nowrap">
                      {c.organization.name}
                    </TableCell>
                  )}
                  <TableCell>
                    {c.category ? (
                      <CategoryBadge name={c.category.name} tone={c.category.tone} />
                    ) : (
                      <span className="text-muted-foreground">—</span>
                    )}
                  </TableCell>
                  {isAdmin && (
                    <TableCell className="text-muted-foreground whitespace-nowrap">
                      {c.responsible_manager?.name ?? '—'}
                      <ManagerAccessHint managerCount={c.manager_count} />
                    </TableCell>
                  )}
                  <TableCell>
                    <ClientStatusBadge active={c.active} closedAt={c.closed_at} />
                  </TableCell>
                  <TableCell className="text-muted-foreground text-right tabular-nums">
                    {c.reconciliation_count}
                  </TableCell>
                  <TableCell className="text-muted-foreground text-sm">
                    {format(new Date(c.created_at), "dd 'de' MMM 'de' yyyy", { locale: ptBR })}
                  </TableCell>
                  <TableCell className="text-right">
                    <div
                      className="flex items-center justify-end gap-1"
                      onClick={(e) => e.stopPropagation()}
                      role="presentation"
                    >
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() => router.push(`/clientes/${c.id}`)}
                        aria-label={`Ver detalhe de ${c.name}`}
                      >
                        <Eye className="h-4 w-4" aria-hidden="true" />
                      </Button>
                      {/* Editar cliente é §9 — só admin (PERMISSION_MATRIX
                          `edit_client`). O backend nega o manager com 403
                          "Papel manager não tem a permissão edit_client", então
                          mostrar o botão aqui abriria um modal que só falha. */}
                      {isAdmin && (
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => setEditing(c)}
                          aria-label={`Editar ${c.name}`}
                        >
                          <SquarePen className="h-4 w-4" aria-hidden="true" />
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

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <p className="text-muted-foreground text-sm" aria-live="polite">
            {total === 0 ? 'Nenhum resultado.' : `${total} cliente${total === 1 ? '' : 's'}`}
            {isFetching && total > 0 ? ' · atualizando...' : ''}
          </p>
          <Select
            value={String(pageSize)}
            onValueChange={(v) => setPageSize(Number(v) as PageSize)}
          >
            <SelectTrigger className="h-8 w-[88px]" aria-label="Resultados por página">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {PAGE_SIZE_OPTIONS.map((opt) => (
                <SelectItem key={opt} value={String(opt)}>
                  {opt} / pág.
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
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
            {totalPages > 0 ? `Página ${page} de ${totalPages}` : `Página ${page}`}
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

      <CreateClientModal open={createOpen} onOpenChange={setCreateOpen} />
      <EditClientModal
        open={editing !== null}
        onOpenChange={(o) => !o && setEditing(null)}
        client={editing}
      />
    </div>
  );
}

function SkeletonRows({ colCount }: { colCount: number }) {
  // 4 linhas é suficiente pra dar a impressão de "carregando" sem ocupar muito.
  return (
    <>
      {Array.from({ length: 4 }).map((_, i) => (
        <TableRow key={i}>
          {Array.from({ length: colCount }).map((__, j) => (
            <TableCell key={j} className="py-4">
              <div className="bg-muted h-3 w-full max-w-[180px] animate-pulse rounded" />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}
