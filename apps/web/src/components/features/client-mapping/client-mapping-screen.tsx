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
 * **A página rola, a tabela não** (86e3f55bd, o desenho da carteira na
 * 86e3eq9uy): a seção tem altura natural, quem rola é o `<main>` do shell e o
 * cabeçalho da tabela da aba Decisões gruda no topo dele (`stickyHeader="page"`,
 * de `xl` para cima). O recorte por situação vem dos contadores do topo da aba.
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
import { useAccountingChartList } from '@/hooks/use-client-accounting-chart';
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
import { triggerBrowserDownload } from '@/lib/download';
import { originIsFileBased } from '@/lib/origin-capabilities';
import { useAuthStore } from '@/stores/auth';

import { isAccountingDestination } from './accounting-destination';
import { MappingHowItWorks } from './mapping-how-it-works';
import { MappingImportSheet } from './mapping-import-sheet';
import {
  INHERITING_DESTINATION_TYPE,
  MappingListPanel,
  SITUATION_FILTERS,
} from './mapping-list-panel';
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
/** Com a página rolando (86e3f55bd), 20 era pouco; o teto de 100 do servidor não muda. */
export const DEFAULT_PAGE_SIZE = 50;

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

  const destinationParam = readEnum(
    get(PARAM.destination),
    destinations.map((item) => item.type),
  );
  // Sem destino na URL e com o demonstrativo SEM alvos (organização nova), o padrão
  // depende de o cliente ter plano contábil (86e3n70pn): só então se pergunta. A
  // sonda é a MESMA do aviso do `conta_contabil` (mesma chave, mesmo cache).
  const needsChartProbe =
    destinationParam === undefined &&
    destinations.some(
      (item) => item.type === INHERITING_DESTINATION_TYPE && item.targetsCount === 0,
    ) &&
    destinations.some((item) => isAccountingDestination(item.type));
  const chartProbe = useAccountingChartList(
    clientId,
    { page: 1, pageSize: 1 },
    { enabled: needsChartProbe },
  );
  // Enquanto a sonda não responde, a tela não abre em destino nenhum: abrir no
  // demonstrativo e pular para a conta contábil um instante depois trocaria a lista.
  const resolvingDefault = needsChartProbe && chartProbe.isLoading;
  const destinationType =
    destinationParam ??
    defaultDestinationType(destinations, {
      clientHasAccountingChart: (chartProbe.data?.pagination.total ?? 0) > 0,
    });
  const destination = resolvingDefault
    ? null
    : (destinations.find((item) => item.type === destinationType) ?? null);

  const view = readEnum(get(PARAM.view), VIEW_VALUES) ?? DEFAULT_VIEW;
  const page = readPositiveInt(get(PARAM.page), 1);
  const pageSize = readPositiveInt(get(PARAM.pageSize), DEFAULT_PAGE_SIZE);
  const situation = readEnum(get(PARAM.situation), SITUATION_FILTERS);
  const codeParam = get(PARAM.code) ?? '';

  // Busca: estado local + debounce → URL (cada tecla não vira request). Só
  // escreve quando o debounce ASSENTOU (`debounced === input`): limpar o campo
  // (etiqueta removida, "Limpar filtros") zera a URL na hora, e sem esta guarda o
  // termo antigo, ainda no debounce, voltava para a URL por 300ms.
  const [codeInput, setCodeInput] = useState(codeParam);
  const debouncedCode = useDebouncedValue(codeInput, 300);
  useEffect(() => {
    if (debouncedCode !== codeInput || debouncedCode === codeParam) return;
    setMany({ [PARAM.code]: debouncedCode || null, [PARAM.page]: null });
  }, [debouncedCode, codeInput, codeParam, setMany]);

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
  // Importar o plano e associar a conta do banco (S16): é OUTRA permissão, e os
  // avisos do destino `conta_contabil` só mandam agir quem a tem.
  const canManageChart = hasPermission(currentUser, 'manage_client_accounting_chart');
  // O catálogo de destinos e alvos da ORGANIZAÇÃO (86e3n70pn): só quem escreve nele é
  // mandado cadastrar alvos pelo aviso de catálogo vazio.
  const canManageCatalog = hasPermission(currentUser, 'manage_mapping_catalog');
  const canSync = hasPermission(currentUser, 'sync_client_movements');
  const canUpload = hasPermission(currentUser, 'upload_client_file');
  // S13: gerar/baixar o arquivo contábil e (para o texto do "sem layout")
  // administrar layouts — duas permissões próprias, nunca `review_export`.
  const canGenerateFile = hasPermission(currentUser, 'generate_accounting_file');
  const canManageLayouts = hasPermission(currentUser, 'manage_export_layouts');
  // S14: origem por ARQUIVO — a base vem do envio, não do sync (409
  // `ORIGEM_POR_ARQUIVO`). Decidido pela capacidade, no helper único.
  const fileOrigin = originIsFileBased(client?.connections ?? []);
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

  // S13: a recusa do arquivo nomeia a categoria a corrigir — leva à aba
  // Decisões filtrada por ela, onde a gaveta de decisão abre. O campo de busca é
  // atualizado junto, senão o debounce escreveria o termo antigo de volta.
  function reviewCategory(categoryCode: string) {
    setCodeInput(categoryCode);
    setMany({
      [PARAM.view]: null,
      [PARAM.code]: categoryCode,
      [PARAM.situation]: null,
      [PARAM.page]: null,
    });
  }

  function clearCode() {
    setCodeInput('');
    setMany({ [PARAM.code]: null, [PARAM.page]: null });
  }

  return (
    // `data-page-scroll`: esta tela é do padrão em que a PÁGINA rola (§7
    // Frontend). É o que faz o `ClientShell` soltar a altura fixa do padrão
    // FILL; sem ele a página termina colada na borda da janela (86e3gkd80).
    <section
      aria-labelledby="client-mapping-heading"
      data-page-scroll
      className="flex flex-col gap-4"
    >
      <div className="space-y-1">
        <h1 id="client-mapping-heading" className="text-xl font-semibold">
          De-para
        </h1>
        <p className="text-muted-foreground text-sm">
          Para onde cada categoria de origem vai em cada destino. A mesma categoria pode ter
          decisões diferentes em destinos diferentes.
        </p>
      </div>

      <MappingHowItWorks />

      {canManage && isClosed && (
        <p className="text-muted-foreground text-sm">
          Cliente encerrado: o de-para fica disponível só para leitura e exportação.
        </p>
      )}

      {destinationsQuery.isLoading ||
      resolvingDefault ||
      (platform && clientOrganizationId === null) ? (
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
          <Tabs
            value={view}
            onValueChange={(value) =>
              setMany({ [PARAM.view]: value === DEFAULT_VIEW ? null : value })
            }
            // Altura NATURAL (86e3f55bd): sem `min-h-0 flex-1` nos dois níveis. A
            // cadeia de altura existia para a tabela rolar na própria área; agora
            // quem rola é o `<main>`. O painel inativo continua escondido pelo
            // primitivo (`data-[state=inactive]:hidden`, validação da S12).
            className="flex flex-col gap-4"
          >
            {/* Uma linha só (86e3fr9qz, pedido do Lucas): as abas, o destino e, à
                direita, Exportar e Importar. Quebra em mais linhas quando não
                cabe, sem cortar nada. */}
            <div className="flex flex-wrap items-center gap-3">
              <TabsList className="self-start">
                <TabsTrigger value="decisoes">Decisões</TabsTrigger>
                <TabsTrigger value="previa">Prévia da competência</TabsTrigger>
              </TabsList>
              <div className="flex items-center gap-2">
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
                  <SelectTrigger id="mapping-destination" className="w-56 sm:w-64">
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
              <div className="flex flex-wrap items-center gap-2 xl:ml-auto">
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
            </div>

            <TabsContent value="decisoes" className="mt-0 flex flex-col">
              <MappingListPanel
                clientId={clientId}
                destination={destination}
                listQuery={listQuery}
                situation={situation}
                codeInput={codeInput}
                codeFilter={codeParam}
                onCodeInputChange={setCodeInput}
                onSituationChange={(value) =>
                  setMany({ [PARAM.situation]: value, [PARAM.page]: null })
                }
                onClearFilters={clearFilters}
                onClearCode={clearCode}
                onPageChange={(next) => setMany({ [PARAM.page]: String(next) })}
                onPageSizeChange={(next) =>
                  setMany({ [PARAM.pageSize]: String(next), [PARAM.page]: null })
                }
                page={page}
                pageSize={pageSize}
                hasFilters={hasFilters}
                serverCompetence={serverCompetence}
                canManage={canManage}
                canManageChart={canManageChart}
                canManageCatalog={canManageCatalog}
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
                canUpload={canUpload}
                canManage={canManage}
                canManageChart={canManageChart}
                isClosed={isClosed}
                originStatus={client?.origin_status ?? 'ativa'}
                fileOrigin={fileOrigin}
                canGenerateFile={canGenerateFile}
                canManageLayouts={canManageLayouts}
                layoutsOrganizationId={platform ? clientOrganizationId : null}
                onReviewCategory={reviewCategory}
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
 * Sem destino na URL, a tela abre no demonstrativo contábil quando ele existe no
 * catálogo (validação humana da S12): é o destino principal do PRD, o único que
 * herda e o que a Sprint 13 lê. Abrir no primeiro do catálogo ("Conta contábil")
 * levava a um destino vazio com o aviso amarelo. O primeiro é só o fallback.
 *
 * A exceção (86e3n70pn): demonstrativo SEM alvos e cliente COM plano contábil
 * importado abre na conta contábil. É o escritório novo que já importou o plano do
 * cliente e ainda não cadastrou o catálogo: no demonstrativo ele só leria o aviso de
 * catálogo vazio, enquanto na conta contábil já pode decidir.
 */
export function defaultDestinationType(
  destinations: readonly MappingDestination[],
  { clientHasAccountingChart = false }: { clientHasAccountingChart?: boolean } = {},
): string {
  const inheriting = destinations.find((item) => item.type === INHERITING_DESTINATION_TYPE);
  const accounting = destinations.find((item) => isAccountingDestination(item.type));
  if (inheriting?.targetsCount === 0 && accounting !== undefined && clientHasAccountingChart) {
    return accounting.type;
  }
  return inheriting?.type ?? destinations[0]?.type ?? '';
}
