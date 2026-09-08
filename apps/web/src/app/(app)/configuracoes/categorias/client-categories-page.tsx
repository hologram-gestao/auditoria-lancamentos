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
 * Gating presentacional via `lib/authz` (`canManageSystemUsers`, como as demais
 * configurações do sistema); a barreira real é o 403 do backend.
 */

import { Plus } from 'lucide-react';
import { useState } from 'react';

import { ClientCategoriesTable } from '@/components/features/client-categories/client-categories-table';
import { ClientCategoryDeleteConfirm } from '@/components/features/client-categories/client-category-delete-confirm';
import { ClientCategoryDialog } from '@/components/features/client-categories/client-category-dialog';
import { AccessDenied } from '@/components/shared/access-denied';
import { Button } from '@/components/ui/button';
import { useClientCategories } from '@/hooks/use-client-categories';
import { ApiError } from '@/lib/api/client';
import type { ClientCategoryItem } from '@/lib/api/client-categories';
import { canManageSystemUsers, homePathFor } from '@/lib/authz';
import { useAuthStore } from '@/stores/auth';

export default function ClientCategoriesPage() {
  const currentUser = useAuthStore((s) => s.user);
  const canSee = canManageSystemUsers(currentUser);

  const { data, isLoading, isError, error } = useClientCategories({ enabled: canSee });

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ClientCategoryItem | null>(null);
  const [deleting, setDeleting] = useState<ClientCategoryItem | null>(null);

  if (currentUser === null) return null;

  if (!canSee) {
    return (
      <AccessDenied
        message="As configurações do sistema são restritas ao administrador da Hologram."
        backHref={homePathFor(currentUser)}
        backLabel="Voltar para o início"
      />
    );
  }

  const rows = data ?? [];
  const errorMessage =
    error instanceof ApiError ? error.userMessage : 'Não foi possível carregar as categorias.';

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <p className="text-muted-foreground text-sm">Configurações &gt; Categorias de Cliente</p>
        <h1 className="text-2xl font-semibold">Categorias de Cliente</h1>
        <p className="text-muted-foreground text-sm">
          Agrupe os clientes por nicho ou segmento. A categoria aparece como chip na lista de
          clientes e serve de filtro; uma categoria em uso não pode ser excluída.
        </p>
      </div>

      <div className="flex justify-end">
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
