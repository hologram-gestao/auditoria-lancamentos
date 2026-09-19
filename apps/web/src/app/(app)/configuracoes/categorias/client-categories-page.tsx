'use client';

/**
 * Tela admin de Categorias de Cliente — 86e34jd8m (épico 86e34jchv).
 *
 * Catálogo FIXO por decisão: texto livre viraria "Fintech"/"fintech"/"Fin tech"
 * e mataria a análise por nicho (lucratividade por segmento) que motivou o
 * pedido. Quem edita é o admin do sistema (configuração, §4.9); a equipe toda
 * lê — o filtro da lista de clientes e o formulário do cliente consomem o
 * mesmo catálogo.
 *
 * Gating presentacional via `lib/authz` (`manage_client_categories` — a
 * permissão DESTA tela); a barreira real é o 403 do backend.
 */

import { Plus } from 'lucide-react';
import { useMemo, useState } from 'react';

import { ClientCategoriesTable } from '@/components/features/client-categories/client-categories-table';
import { ClientCategoryDeleteConfirm } from '@/components/features/client-categories/client-category-delete-confirm';
import { ClientCategoryDialog } from '@/components/features/client-categories/client-category-dialog';
import {
  ALL_ORGANIZATIONS,
  OrganizationFilterSelect,
} from '@/components/features/organizations/organization-select';
import { AccessDenied } from '@/components/shared/access-denied';
import { Button } from '@/components/ui/button';
import { useClientCategories } from '@/hooks/use-client-categories';
import { ApiError } from '@/lib/api/client';
import type { ClientCategoryItem } from '@/lib/api/client-categories';
import { hasPermission, homePathFor, isPlatformScoped } from '@/lib/authz';
import { useAuthStore } from '@/stores/auth';

export default function ClientCategoriesPage() {
  const currentUser = useAuthStore((s) => s.user);
  // Guard com a permissão DESTA tela (86e36ecwa): usar a de usuários deixaria
  // o menu e a rota discordarem no dia em que a matriz mudar uma e não a outra.
  const canSee = hasPermission(currentUser, 'manage_client_categories');
  // A plataforma lê o catálogo de TODAS as organizações (86e36ecqz), então
  // precisa ver de quem é cada linha e escolher onde a nova nasce.
  const isPlatform = isPlatformScoped(currentUser);

  const { data, isLoading, isError, error } = useClientCategories({ enabled: canSee });

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ClientCategoryItem | null>(null);
  const [deleting, setDeleting] = useState<ClientCategoryItem | null>(null);
  const [organizationFilter, setOrganizationFilter] = useState<string>(ALL_ORGANIZATIONS);

  // ANTES dos early returns: hook depois de `return` roda em ordens diferentes
  // entre renders e o React quebra. Filtro de EXIBIÇÃO sobre o catálogo que já
  // veio inteiro (a rota não é paginada e a plataforma recebe o de todas as
  // organizações): quem decide o que ela ALCANÇA é o servidor; isto só recorta
  // o que ela está olhando, mesmo papel do filtro das outras duas listas.
  const rows = useMemo(() => {
    const all = data ?? [];
    return organizationFilter === ALL_ORGANIZATIONS
      ? all
      : all.filter((c) => c.organization_id === organizationFilter);
  }, [data, organizationFilter]);

  if (currentUser === null) return null;

  if (!canSee) {
    return (
      <AccessDenied
        message="O catálogo de categorias é restrito ao administrador da organização."
        backHref={homePathFor(currentUser)}
        backLabel="Voltar para o início"
      />
    );
  }

  const errorMessage =
    error instanceof ApiError ? error.userMessage : 'Não foi possível carregar as categorias.';

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <p className="text-muted-foreground text-sm">Configurações &gt; Categorias de Cliente</p>
        <h1 className="text-2xl font-semibold">Categorias de Cliente</h1>
        <p className="text-muted-foreground text-sm">
          Agrupe os clientes por nicho ou segmento. O catálogo é por organização: a categoria
          aparece como chip na lista de clientes e serve de filtro, e uma categoria em uso não pode
          ser excluída.
        </p>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        {/* Filtro por organização (86e36ed1d) — só a plataforma, que lê o
            catálogo de TODAS. Mesmo lugar e mesmo rótulo das outras listas. */}
        {isPlatform ? (
          <OrganizationFilterSelect
            value={organizationFilter}
            onValueChange={setOrganizationFilter}
            ariaLabel="Filtrar por organização"
            className="w-full sm:w-56"
          />
        ) : (
          <span />
        )}
        {/* A tela inteira já é `manage_client_categories`: quem chega aqui pode
            criar — a plataforma escolhendo a organização no diálogo
            (86e36ed1d), o admin na própria. */}
        <Button
          onClick={() => {
            setEditing(null);
            setDialogOpen(true);
          }}
        >
          <Plus className="h-4 w-4" aria-hidden="true" />
          Nova categoria
        </Button>
      </div>

      <ClientCategoriesTable
        rows={rows}
        isLoading={isLoading}
        isError={isError}
        errorMessage={errorMessage}
        showOrganization={isPlatform}
        emptyMessage={
          organizationFilter === ALL_ORGANIZATIONS
            ? "Nenhuma categoria cadastrada. Crie a primeira em 'Nova categoria'."
            : 'Nenhuma categoria nesta organização.'
        }
        onEdit={(c) => {
          setEditing(c);
          setDialogOpen(true);
        }}
        onDelete={setDeleting}
      />

      <p className="text-muted-foreground text-sm" aria-live="polite">
        {rows.length === 0
          ? 'Nenhuma categoria.'
          : `${rows.length} categoria${rows.length === 1 ? '' : 's'}.`}
      </p>

      <ClientCategoryDialog
        open={dialogOpen}
        onOpenChange={(o) => {
          setDialogOpen(o);
          if (!o) setEditing(null);
        }}
        category={editing}
      />
      <ClientCategoryDeleteConfirm
        open={deleting !== null}
        onOpenChange={(o) => !o && setDeleting(null)}
        category={deleting}
      />
    </div>
  );
}
