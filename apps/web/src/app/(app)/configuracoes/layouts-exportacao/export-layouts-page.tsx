'use client';

/**
 * Tela de Layouts de exportação — Sprint 13 (FRONT 13.5 / R1 + R3 tela).
 *
 * O layout define o formato do arquivo que o sistema contábil importa (colunas,
 * separador, codificação...). É configuração da ORGANIZAÇÃO, versionada: a tela
 * lista os layouts, mostra as versões só leitura e cria o layout a partir do
 * modelo Domínio numa ação só. Sem editor visual (fora de escopo).
 *
 * Gating presentacional via `lib/authz` (`manage_export_layouts` — a permissão
 * DESTA tela, a mesma do item de menu); a barreira real é o 403 do backend. A
 * plataforma vê as organizações todas: ganha a coluna Organização, o filtro
 * (server-side, `?organizationId=`, com o recorte na URL) e escolhe a
 * organização ao criar.
 */

import { Plus } from 'lucide-react';
import { useMemo, useState } from 'react';

import { ExportLayoutFromTemplateDialog } from '@/components/features/export-layouts/export-layout-from-template-dialog';
import { ExportLayoutVersionsSheet } from '@/components/features/export-layouts/export-layout-versions-sheet';
import { ExportLayoutsTable } from '@/components/features/export-layouts/export-layouts-table';
import {
  ALL_ORGANIZATIONS,
  OrganizationFilterSelect,
  useOrganizationOptions,
} from '@/components/features/organizations/organization-select';
import { AccessDenied } from '@/components/shared/access-denied';
import { Button } from '@/components/ui/button';
import { useExportLayouts } from '@/hooks/use-export-layouts';
import { useUrlState } from '@/hooks/use-url-state';
import { ApiError } from '@/lib/api/client';
import { hasPermission, homePathFor, isPlatformScoped } from '@/lib/authz';
import { useAuthStore } from '@/stores/auth';

/** Parâmetro da URL com o recorte de organização (só a plataforma). */
const ORGANIZATION_PARAM = 'organizacao';

export default function ExportLayoutsPage() {
  const currentUser = useAuthStore((s) => s.user);
  const canSee = hasPermission(currentUser, 'manage_export_layouts');
  const isPlatform = isPlatformScoped(currentUser);
  const url = useUrlState();
  const organizationFilter = isPlatform ? url.get(ORGANIZATION_PARAM) : null;

  const query = useExportLayouts(organizationFilter, { enabled: canSee });
  // O contrato da lista traz só o `organizationId`: o nome, para a coluna da
  // plataforma, vem das mesmas opções que alimentam o filtro (cache comum).
  const { organizations } = useOrganizationOptions({ enabled: canSee && isPlatform });
  const organizationNames = useMemo(
    () => new Map(organizations.map((o) => [o.id, o.name])),
    [organizations],
  );

  const [createOpen, setCreateOpen] = useState(false);
  const [versionsOpen, setVersionsOpen] = useState(false);
  const [versionsLayoutId, setVersionsLayoutId] = useState<string | null>(null);

  if (currentUser === null) return null;

  if (!canSee) {
    return (
      <AccessDenied
        message="Os layouts de exportação são restritos ao administrador da organização."
        backHref={homePathFor(currentUser)}
        backLabel="Voltar para o início"
      />
    );
  }

  const rows = query.data ?? [];
  const errorMessage =
    query.error instanceof ApiError
      ? query.error.userMessage
      : 'Não foi possível carregar os layouts de exportação.';

  return (
    <div className="flex flex-col gap-6 md:h-full">
      <div className="space-y-1">
        <p className="text-muted-foreground text-sm">Configurações &gt; Layouts de exportação</p>
        <h1 className="text-2xl font-semibold">Layouts de exportação</h1>
        <p className="text-muted-foreground text-sm">
          O formato do arquivo que o sistema contábil importa: colunas, separador, codificação, data
          e valor. Cada alteração vira uma versão nova, e um arquivo já gerado continua explicável
          pela versão que o gerou.
        </p>
      </div>

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        {isPlatform ? (
          <OrganizationFilterSelect
            value={organizationFilter ?? ALL_ORGANIZATIONS}
            onValueChange={(value) =>
              url.setMany({ [ORGANIZATION_PARAM]: value === ALL_ORGANIZATIONS ? null : value })
            }
            ariaLabel="Filtrar por organização"
            className="w-full sm:w-56"
          />
        ) : (
          <span />
        )}
        <Button onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" aria-hidden="true" />
          Criar a partir do modelo Domínio
        </Button>
      </div>

      <div className="flex flex-col md:min-h-0 md:flex-1">
        <ExportLayoutsTable
          rows={rows}
          isLoading={query.isLoading}
          isFetching={query.isFetching}
          isError={query.isError}
          errorMessage={errorMessage}
          onRetry={() => void query.refetch()}
          showOrganization={isPlatform}
          organizationName={(id) => organizationNames.get(id) ?? '—'}
          onOpenVersions={(layout) => {
            setVersionsLayoutId(layout.id);
            setVersionsOpen(true);
          }}
          onCreate={() => setCreateOpen(true)}
          filtered={organizationFilter !== null}
        />
      </div>

      <p className="text-muted-foreground text-sm" aria-live="polite">
        {query.isLoading
          ? ''
          : rows.length === 0
            ? 'Nenhum layout.'
            : `${rows.length} layout${rows.length === 1 ? '' : 's'}.`}
      </p>

      <ExportLayoutFromTemplateDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        requireOrganization={isPlatform}
      />
      <ExportLayoutVersionsSheet
        open={versionsOpen}
        layoutId={versionsLayoutId}
        onOpenChange={setVersionsOpen}
      />
    </div>
  );
}
