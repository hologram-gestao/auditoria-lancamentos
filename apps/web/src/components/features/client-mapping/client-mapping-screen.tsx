'use client';

/**
 * Tela "De-para" do cliente — Sprint 12 / R6 (FRONT 12.7).
 *
 * Onde o contador parceiro (papel `manager`) classifica a carteira dele: cada
 * categoria de origem do cliente → um alvo do catálogo do DESTINO escolhido,
 * ou "Não mapear". A mesma categoria tem decisões diferentes em destinos
 * diferentes — é a tese da sprint —, por isso o destino é o primeiro controle
 * da tela.
 *
 * **Duas abas, estado na URL** (`view`): "Decisões" (a lista, com edição) e
 * "Prévia da competência" (estado da base, cobertura, materialização). Destino,
 * filtros, página e competência também moram na URL: a view é linkável e
 * sobrevive ao F5.
 *
 * **Gating — UI e rota pela MESMA permissão (R6).** Ler é de quem alcança o
 * cliente (o operador inclusive). Toda escrita — editar, lote, iniciar,
 * importar, materializar — pede `manage_client_mapping`; sincronizar a
 * competência pede `sync_client_movements`. Sem a permissão a ação some (nunca
 * desabilitada); a autoridade continua sendo o servidor. Nenhum `role ===`
 * aqui: tudo passa por `lib/authz.ts`.
 *
 * **O que esta tela NÃO faz:** não soma (cobertura e as quatro situações vêm do
 * servidor), não busca por nome (nome de categoria não é persistido, §4.5) e
 * não inventa destino: os destinos são o catálogo da organização DO CLIENTE.
 */

import { Download, Loader2, Upload } from 'lucide-react';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Label } from '@/components/ui/label';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import {
  useClientMappingList,
  useExportClientMapping,
  useMappingDestinations,
} from '@/hooks/use-client-mapping';
import { useClientDetail } from '@/hooks/use-clients';
import { useDebouncedValue } from '@/hooks/use-debounced-value';
import { readEnum, readPositiveInt, useUrlState } from '@/hooks/use-url-state';
import { ApiError, NetworkError } from '@/lib/api/client';
import type { ListClientMappingParams } from '@/lib/api/client-mapping';
import { hasPermission, isPlatformScoped } from '@/lib/authz';
import { isCompetence, localCurrentCompetence } from '@/lib/competence';
import type { MappingDestination } from '@/lib/contracts';
import { useAuthStore } from '@/stores/auth';

import { MappingImportSheet } from './mapping-import-sheet';
import { MappingListPanel, SITUATION_FILTERS } from './mapping-list-panel';
import { MappingPreviewPanel } from './mapping-preview-panel';

const PARAM = {
  destination: 'destination',
  view: 'view',
  page: 'page',
  pageSize: 'pageSize',
  situation: 'situation',
  code: 'code',
  competence: 'competence',
} as const;

const DEFAULT_VIEW = 'decisoes';
const VIEW_VALUES = ['decisoes', 'previa'] as const;
const DEFAULT_PAGE_SIZE = 20;

export function ClientMappingScreen({ clientId }: { clientId: string }) {
  const currentUser = useAuthStore((s) => s.user);
  const { get, setMany } = useUrlState();
  // O shell já carregou este detalhe — vem do cache, sem segundo request.
  const clientDetail = useClientDetail(clientId);
  const client = clientDetail.data;

  // Os destinos são o catálogo da organização DO CLIENTE. A plataforma enxerga
  // todas as organizações, então é a única que precisa dizer qual (staff e
  // usuário de cliente recebem 403 se mandarem o parâmetro — e já recebem a
  // própria org sem ele).
  const platform = isPlatformScoped(currentUser);
  const clientOrganizationId = client?.organization.id ?? null;
  const destinationsQuery = useMappingDestinations(platform ? clientOrganizationId : null, {
    enabled: currentUser !== null && (!platform || clientOrganizationId !== null),
  });
  const destinations: MappingDestination[] = (destinationsQuery.data ?? []).filter(
    (item) =>
      item.active &&
      (clientOrganizationId === null || item.organizationId === clientOrganizationId),
  );

  const destinationType =
    readEnum(
      get(PARAM.destination),
      destinations.map((item) => item.type),
    ) ??
    destinations[0]?.type ??
    '';
  const destination = destinations.find((item) => item.type === destinationType) ?? null;

  const view = readEnum(get(PARAM.view), VIEW_VALUES) ?? DEFAULT_VIEW;
  const page = readPositiveInt(get(PARAM.page), 1);
  const pageSize = readPositiveInt(get(PARAM.pageSize), DEFAULT_PAGE_SIZE);
  const situation = readEnum(get(PARAM.situation), SITUATION_FILTERS);
  const codeParam = get(PARAM.code) ?? '';

  // Busca: estado local + debounce → URL (cada tecla não vira request).
  const [codeInput, setCodeInput] = useState(codeParam);
  const debouncedCode = useDebouncedValue(codeInput, 300);
  useEffect(() => {
    if (debouncedCode === codeParam) return;
    setMany({ [PARAM.code]: debouncedCode || null, [PARAM.page]: null });
  }, [debouncedCode, codeParam, setMany]);

  const listParams: ListClientMappingParams = {
    page,
    pageSize,
    situation: situation ?? null,
    code: codeParam || null,
  };
  const listQuery = useClientMappingList(clientId, destinationType, listParams, {
    enabled: destination !== null,
  });

  // A competência CORRENTE é a do servidor (a lista a devolve); o relógio do
  // navegador é só o fallback enquanto ela não chegou.
  const serverCompetence = listQuery.data?.competence ?? localCurrentCompetence();
  const competenceParam = get(PARAM.competence);
  const competence = isCompetence(competenceParam) ? competenceParam : serverCompetence;

  const exportMutation = useExportClientMapping(clientId, destinationType);
  const [importOpen, setImportOpen] = useState(false);

  if (currentUser === null) return null;

  const isClosed = client?.closed_at != null;
  const canManage = hasPermission(currentUser, 'manage_client_mapping');
  const canSync = hasPermission(currentUser, 'sync_client_movements');
  const hasFilters = situation !== undefined || codeParam !== '';

  async function handleExport() {
    try {
      const { blob, filename } = await exportMutation.mutateAsync();
      triggerBrowserDownload(blob, filename ?? `de-para-${destinationType}.xlsx`);
      toast.success('Planilha do de-para exportada.');
    } catch (err) {
      toast.error(
        err instanceof ApiError || err instanceof NetworkError
          ? err.userMessage
          : 'Não foi possível exportar o de-para.',
      );
    }
  }

  function clearFilters() {
    setCodeInput('');
    setMany({ [PARAM.situation]: null, [PARAM.code]: null, [PARAM.page]: null });
  }

  return (
    <section aria-labelledby="client-mapping-heading" className="flex h-full flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h2 id="client-mapping-heading" className="text-lg font-semibold">
            De-para
          </h2>
          <p className="text-muted-foreground text-sm">
            Para onde cada categoria de origem vai em cada destino. A mesma categoria pode ter
            decisões diferentes em destinos diferentes.
          </p>
        </div>
        {destination !== null && (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => void handleExport()}
              disabled={exportMutation.isPending}
            >
              {exportMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              ) : (
                <Download className="h-4 w-4" aria-hidden="true" />
              )}
              {exportMutation.isPending ? 'Exportando…' : 'Exportar'}
            </Button>
            {canManage && !isClosed && (
              <Button type="button" variant="secondary" onClick={() => setImportOpen(true)}>
                <Upload className="h-4 w-4" aria-hidden="true" />
                Importar
              </Button>
            )}
          </div>
        )}
      </div>

      {canManage && isClosed && (
        <p className="text-muted-foreground text-sm">
          Cliente encerrado: o de-para fica disponível só para leitura e exportação.
        </p>
      )}

      {destinationsQuery.isLoading || (platform && clientOrganizationId === null) ? (
        <div className="bg-muted h-10 w-full animate-pulse rounded-md sm:w-72" aria-hidden="true" />
      ) : destinationsQuery.isError ? (
        <div
          role="alert"
          className="border-destructive/30 bg-destructive/5 text-destructive space-y-3 rounded-lg border p-6"
        >
          <p className="text-sm font-medium">Não foi possível carregar os destinos</p>
          <p className="text-sm">
            {destinationsQuery.error instanceof ApiError
              ? destinationsQuery.error.userMessage
              : 'Ocorreu um erro inesperado.'}
          </p>
          <Button variant="outline" size="sm" onClick={() => void destinationsQuery.refetch()}>
            Tentar novamente
          </Button>
        </div>
      ) : destination === null ? (
        <div role="status" className="bg-muted space-y-1 rounded-lg p-6 text-sm">
          <p className="font-medium">Nenhum destino ativo na organização deste cliente</p>
          <p className="text-muted-foreground">
            Os destinos do de-para (demonstrativo, conta contábil, natureza fiscal, fluxo de caixa…)
            são configurados pela administração da organização.
          </p>
        </div>
      ) : (
        <>
          <div className="space-y-1.5 sm:w-80">
            <Label htmlFor="mapping-destination">Destino</Label>
            <Select
              value={destination.type}
              onValueChange={(value) =>
                setMany({
                  [PARAM.destination]: value,
                  [PARAM.page]: null,
                })
              }
            >
              <SelectTrigger id="mapping-destination" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {destinations.map((item) => (
                  <SelectItem key={item.id} value={item.type}>
                    {item.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <Tabs
            value={view}
            onValueChange={(value) =>
              setMany({ [PARAM.view]: value === DEFAULT_VIEW ? null : value })
            }
            // `min-h-0` nos dois níveis (Tabs e TabsContent): sem ele a cadeia de
            // altura para aqui e a tabela deixa de rolar na própria área (86e2uca1d).
            className="flex min-h-0 flex-1 flex-col gap-4"
          >
            <TabsList className="self-start">
              <TabsTrigger value="decisoes">Decisões</TabsTrigger>
              <TabsTrigger value="previa">Prévia da competência</TabsTrigger>
            </TabsList>

            <TabsContent value="decisoes" className="mt-0 flex min-h-0 flex-1 flex-col">
              <MappingListPanel
                clientId={clientId}
                destination={destination}
                listQuery={listQuery}
                situation={situation}
                codeInput={codeInput}
                onCodeInputChange={setCodeInput}
                onSituationChange={(value) =>
                  setMany({ [PARAM.situation]: value, [PARAM.page]: null })
                }
                onClearFilters={clearFilters}
                onPageChange={(next) => setMany({ [PARAM.page]: String(next) })}
                onPageSizeChange={(next) =>
                  setMany({ [PARAM.pageSize]: String(next), [PARAM.page]: null })
                }
                page={page}
                pageSize={pageSize}
                hasFilters={hasFilters}
                serverCompetence={serverCompetence}
                canManage={canManage}
                isClosed={isClosed}
              />
            </TabsContent>

            <TabsContent value="previa" className="mt-0">
              <MappingPreviewPanel
                clientId={clientId}
                destination={destination}
                competence={competence}
                onCompetenceChange={(next) =>
                  setMany({ [PARAM.competence]: next === serverCompetence ? null : next })
                }
                canSync={canSync}
                canManage={canManage}
                isClosed={isClosed}
                originStatus={client?.origin_status ?? 'ativa'}
              />
            </TabsContent>
          </Tabs>

          {canManage && !isClosed && (
            <MappingImportSheet
              open={importOpen}
              onOpenChange={setImportOpen}
              clientId={clientId}
              destination={destination}
              serverCompetence={serverCompetence}
            />
          )}
        </>
      )}
    </section>
  );
}

/**
 * Link temporário + `click()` — o padrão do download de blob da casa
 * (`export-report-button.tsx`). O revoke libera a memória do objeto.
 */
function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
