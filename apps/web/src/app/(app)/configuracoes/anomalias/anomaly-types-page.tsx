'use client';

/**
 * Tela admin de Tipos de Anomalia — S15 FRONT 11.2 (Fase 1).
 *
 * Defesa em profundidade:
 *   - Middleware Next libera todas rotas autenticadas (não decodifica JWT).
 *   - Esta página (client component) redireciona manager para `/clientes`.
 *   - Backend bloqueia mutações com 403.
 *
 * Ordenação default (severidade crítica → moderada → info, depois name asc)
 * vem do backend (`anomaly_types/repository.py`); aqui não reordenamos no
 * cliente. Busca é client-side por enquanto — o catálogo é pequeno (8–20
 * tipos no MVP); se passar de ~100, migrar para `?q=` no GET.
 *
 * Fase 2 ("Novo Tipo") fica atrás de `NEXT_PUBLIC_ANOMALY_TYPE_CREATE_ENABLED`.
 * Liberar quando o catálogo do seed for validado em produção.
 */

import { Plus, Search } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';

import { AnomalyTypeCreateDialog } from '@/components/features/anomaly-types/anomaly-type-create-dialog';
import { AnomalyTypeDeleteConfirm } from '@/components/features/anomaly-types/anomaly-type-delete-confirm';
import { AnomalyTypeEditDialog } from '@/components/features/anomaly-types/anomaly-type-edit-dialog';
import { AnomalyTypeToggleConfirm } from '@/components/features/anomaly-types/anomaly-type-toggle-confirm';
import { AnomalyTypesTable } from '@/components/features/anomaly-types/anomaly-types-table';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Switch } from '@/components/ui/switch';
import { useAnomalyTypesList } from '@/hooks/use-anomaly-types';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import type { AnomalyType } from '@/lib/api/anomaly-types';
import { ApiError } from '@/lib/api/client';
import { useAuthStore } from '@/stores/auth';

const PAGE_SIZE = 100;

const CREATE_ENABLED = process.env['NEXT_PUBLIC_ANOMALY_TYPE_CREATE_ENABLED'] === 'true';

function normalize(value: string): string {
  // Remove acentos (combining diacritical marks U+0300–U+036F).
  return value.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();
}

export default function AnomalyTypesPage() {
  const router = useRouter();
  const currentUser = useAuthStore((s) => s.user);

  useEffect(() => {
    if (currentUser !== null && currentUser.role !== 'admin') {
      router.replace('/clientes');
    }
  }, [currentUser, router]);

  const [searchInput, setSearchInput] = useState('');
  const debouncedSearch = useDebouncedValue(searchInput, 300);
  const [includeInactive, setIncludeInactive] = useState(false);

  const queryParams = useMemo(
    () => ({ page: 1, pageSize: PAGE_SIZE, includeInactive }),
    [includeInactive],
  );
  const { data, isLoading, isError, error } = useAnomalyTypesList(queryParams, {
    enabled: currentUser?.role === 'admin',
  });

  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<AnomalyType | null>(null);
  const [toggling, setToggling] = useState<AnomalyType | null>(null);
  const [deleting, setDeleting] = useState<AnomalyType | null>(null);

  const filteredRows = useMemo(() => {
    const all = data?.data ?? [];
    const q = normalize(debouncedSearch);
    if (!q) return all;
    return all.filter((t) => normalize(t.name).includes(q) || normalize(t.code).includes(q));
  }, [data, debouncedSearch]);

  if (currentUser === null || currentUser.role !== 'admin') {
    return null;
  }

  const errorMessage =
    error instanceof ApiError ? error.userMessage : 'Não foi possível carregar a lista.';

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <p className="text-muted-foreground text-sm">Configurações &gt; Tipos de Anomalia</p>
        <h1 className="text-2xl font-semibold">Tipos de Anomalia</h1>
        <p className="text-muted-foreground text-sm">
          Gerencie o catálogo de tipos detectados durante a conciliação. Desativar impede que novas
          anomalias do tipo sejam criadas; anomalias existentes permanecem visíveis.
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
            placeholder="Buscar por nome ou código..."
            className="pl-9"
            aria-label="Buscar tipos de anomalia"
          />
        </div>
        <div className="flex items-center gap-4">
          <label className="flex items-center gap-2 text-sm">
            <Switch
              checked={includeInactive}
              onCheckedChange={setIncludeInactive}
              aria-label="Incluir inativos"
            />
            <span className="text-muted-foreground">Incluir inativos</span>
          </label>
          {/* Fase 2 — Liberar quando catálogo do seed for validado em prod (S15 Fase 2). */}
          <Button onClick={() => setCreateOpen(true)} disabled={!CREATE_ENABLED}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            Novo Tipo
          </Button>
        </div>
      </div>

      <AnomalyTypesTable
        rows={filteredRows}
        isLoading={isLoading}
        isError={isError}
        errorMessage={errorMessage}
        onEdit={setEditing}
        onToggle={setToggling}
        onDelete={setDeleting}
      />

      <p className="text-muted-foreground text-sm" aria-live="polite">
        {filteredRows.length === 0
          ? 'Nenhum resultado.'
          : `${filteredRows.length} tipo${filteredRows.length === 1 ? '' : 's'}.`}
      </p>

      <AnomalyTypeCreateDialog open={createOpen} onOpenChange={setCreateOpen} />
      <AnomalyTypeEditDialog
        open={editing !== null}
        onOpenChange={(o) => !o && setEditing(null)}
        anomalyType={editing}
      />
      <AnomalyTypeToggleConfirm
        open={toggling !== null}
        onOpenChange={(o) => !o && setToggling(null)}
        anomalyType={toggling}
      />
      <AnomalyTypeDeleteConfirm
        open={deleting !== null}
        onOpenChange={(o) => !o && setDeleting(null)}
        anomalyType={deleting}
      />
    </div>
  );
}
