'use client';

/**
 * Aba "Prévia da competência" do de-para (Sprint 12 — FRONT 12.7 / R0 · R3 · R5).
 *
 * Três blocos, nesta ordem, porque cada um depende do anterior:
 *
 *   1. **Estado da base de movimentos** da competência (sincronizada em, falhou
 *      em, nunca) e a ação "Sincronizar competência" — só para quem tem
 *      `sync_client_movements`. A prévia NÃO sincroniza sozinha (R0).
 *   2. **A prévia**: as QUATRO situações com valor (formatBRL) e quantidade, a
 *      cobertura e a contra-métrica (% `nao_mapear`), e as categorias sem
 *      decisão por valor. Tudo calculado no SERVIDOR sobre a competência
 *      inteira — esta tela não soma nada. Base nunca sincronizada não mostra
 *      "0% de cobertura": mostra a instrução de sincronizar (o 409
 *      `BASE_NAO_SINCRONIZADA` do R5 existe para isso).
 *   3. **Materializar** (`manage_client_mapping`) com confirmação — e uma
 *      confirmação EXTRA, explícita, quando houver valor sem decisão — e as
 *      versões materializadas da competência, só-leitura.
 */

import { AlertTriangle, CheckCircle2, Layers, Loader2, RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';

import { OriginStateBlock } from '@/components/shared/origin-state-notice';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch } from '@/components/ui/switch';
import {
  Table,
  TableBody,
  TableCard,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  useMappingPreview,
  useMaterializeMapping,
  useMovementsSyncState,
  useSyncMovements,
} from '@/hooks/use-client-mapping';
import { ApiError } from '@/lib/api/client';
import { isCompetence } from '@/lib/competence';
import type {
  MappingDestination,
  MappingPreview,
  MappingSituationTotal,
  MovementsSyncState,
  OriginStatus,
} from '@/lib/contracts';
import { formatBRL, formatCreatedAt, formatPercent, formatReferenceMonth } from '@/lib/format';
import { isOriginError, originErrorCode } from '@/lib/origin-state';

interface MappingPreviewPanelProps {
  clientId: string;
  destination: MappingDestination;
  competence: string;
  onCompetenceChange: (competence: string) => void;
  canSync: boolean;
  canManage: boolean;
  isClosed: boolean;
  originStatus: OriginStatus;
}

export function MappingPreviewPanel({
  clientId,
  destination,
  competence,
  onCompetenceChange,
  canSync,
  canManage,
  isClosed,
  originStatus,
}: MappingPreviewPanelProps) {
  const validCompetence = isCompetence(competence) ? competence : '';
  const stateQuery = useMovementsSyncState(clientId, validCompetence);
  const neverSynced = stateQuery.data?.neverSynced === true;
  // Nunca pedir a prévia de uma base que nunca foi sincronizada: o servidor
  // responderia 409 e a resposta certa (a instrução) já está no estado da base.
  const previewQuery = useMappingPreview(clientId, destination.type, validCompetence, {
    enabled: stateQuery.data !== undefined && !neverSynced,
  });
  const syncMutation = useSyncMovements(clientId);
  const [originError, setOriginError] = useState<unknown>(null);

  // Taxonomia de origem da S9 como ESTADO (nunca toast genérico): o código vem
  // do `origin_status` antes do clique e do erro real depois dele.
  const originCode =
    originErrorCode(originError) ?? (originStatus === 'ativa' ? null : 'SEM_CONEXAO');
  const showSyncAction = canSync && !isClosed && originCode === null;

  async function handleSync() {
    if (!validCompetence) return;
    setOriginError(null);
    try {
      const result = await syncMovements();
      toast.success(
        `Competência sincronizada: ${result.movimentos} ${result.movimentos === 1 ? 'movimento' : 'movimentos'} de ${result.contas} ${result.contas === 1 ? 'conta' : 'contas'}.`,
      );
    } catch (err) {
      if (isOriginError(err)) {
        setOriginError(err);
        return;
      }
      toast.error(
        err instanceof ApiError ? err.userMessage : 'Não foi possível sincronizar a competência.',
      );
    }
  }

  function syncMovements() {
    return syncMutation.mutateAsync(validCompetence);
  }

  const syncButton = (
    <Button type="button" onClick={() => void handleSync()} disabled={syncMutation.isPending}>
      {syncMutation.isPending ? (
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      ) : (
        <RefreshCw className="h-4 w-4" aria-hidden="true" />
      )}
      {syncMutation.isPending ? 'Sincronizando…' : 'Sincronizar competência'}
    </Button>
  );

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="space-y-1.5 sm:w-56">
          <Label htmlFor="mapping-preview-competence">Competência</Label>
          <Input
            id="mapping-preview-competence"
            type="month"
            lang="pt-BR"
            value={competence}
            onChange={(e) => {
              if (isCompetence(e.target.value)) onCompetenceChange(e.target.value);
            }}
          />
        </div>
      </div>

      <section
        aria-labelledby="mapping-base-state-heading"
        className="bg-card space-y-3 rounded-lg border p-4"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <h3 id="mapping-base-state-heading" className="text-sm font-semibold">
              Base de movimentos de {formatReferenceMonth(validCompetence)}
            </h3>
            <BaseStateText query={stateQuery} />
          </div>
          {showSyncAction && syncButton}
        </div>
        {!isClosed && originCode !== null && canSync && (
          <OriginStateBlock code={originCode} clientId={clientId} variant="inline" />
        )}
        {canSync && isClosed && (
          <p className="text-muted-foreground text-sm">
            Cliente encerrado: a sincronização está indisponível.
          </p>
        )}
      </section>

      {neverSynced ? (
        <NeverSyncedInstruction canSync={canSync && !isClosed} competence={validCompetence} />
      ) : previewQuery.isLoading || stateQuery.isLoading ? (
        <PreviewSkeleton />
      ) : previewQuery.isError ? (
        <PreviewErrorState
          error={previewQuery.error}
          canSync={canSync && !isClosed}
          competence={validCompetence}
          onGoTo={onCompetenceChange}
          onRetry={() => void previewQuery.refetch()}
        />
      ) : previewQuery.data ? (
        <PreviewContent
          clientId={clientId}
          destination={destination}
          preview={previewQuery.data}
          canMaterialize={canManage && !isClosed}
          onStale={() => void previewQuery.refetch()}
        />
      ) : null}
    </div>
  );
}

function BaseStateText({
  query,
}: {
  query: { data?: MovementsSyncState; isLoading: boolean; isError: boolean };
}) {
  if (query.isLoading) {
    return <div className="bg-muted h-4 w-56 animate-pulse rounded" aria-hidden="true" />;
  }
  if (query.isError || !query.data) {
    return (
      <p role="alert" className="text-destructive text-sm">
        Não foi possível ler o estado da base desta competência.
      </p>
    );
  }
  const state = query.data;
  return (
    <div className="space-y-1 text-sm" data-testid="mapping-base-state">
      {state.neverSynced ? (
        <p className="text-muted-foreground">Nunca sincronizada.</p>
      ) : (
        <p className="text-muted-foreground">
          Sincronizada em {state.syncedAt ? formatCreatedAt(state.syncedAt) : '—'}.
        </p>
      )}
      {state.syncFailedAt != null && (
        <p className="text-warning flex items-center gap-1.5">
          <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden="true" />A última tentativa falhou
          em {formatCreatedAt(state.syncFailedAt)}
          {state.syncedAt ? '; a prévia usa a última base íntegra.' : '.'}
        </p>
      )}
    </div>
  );
}

function NeverSyncedInstruction({ canSync, competence }: { canSync: boolean; competence: string }) {
  return (
    <div
      role="status"
      className="bg-info-muted text-info ring-info/30 space-y-1 rounded-lg p-4 text-sm ring-1 ring-inset"
    >
      <p className="font-medium">
        {formatReferenceMonth(competence)} ainda não tem base de movimentos
      </p>
      <p>
        {canSync
          ? 'Sincronize a competência (botão acima) para trazer os movimentos da origem. A prévia do de-para é calculada sobre eles.'
          : 'A prévia é calculada sobre os movimentos da competência. Peça a alguém da equipe com acesso de sincronização para sincronizá-la.'}
      </p>
    </div>
  );
}

function PreviewErrorState({
  error,
  canSync,
  competence,
  onGoTo,
  onRetry,
}: {
  error: unknown;
  canSync: boolean;
  competence: string;
  onGoTo: (competence: string) => void;
  onRetry: () => void;
}) {
  if (error instanceof ApiError) {
    if (error.code === 'BASE_NAO_SINCRONIZADA') {
      return <NeverSyncedInstruction canSync={canSync} competence={competence} />;
    }
    if (error.code === 'SEM_MOVIMENTOS') {
      return (
        <div role="status" className="bg-muted space-y-1 rounded-lg p-4 text-sm">
          <p className="font-medium">Sem movimentos em {formatReferenceMonth(competence)}</p>
          <p className="text-muted-foreground">
            A competência foi sincronizada e não tem movimento — não há o que traduzir.
          </p>
        </div>
      );
    }
    if (error.code === 'ANTERIOR_A_PRIMEIRA_VIGENCIA') {
      const earliest = error.details.earliestCompetence;
      return (
        <div role="status" className="bg-muted space-y-3 rounded-lg p-4 text-sm">
          <p>{error.userMessage}</p>
          {isCompetence(earliest) && (
            <Button type="button" variant="outline" size="sm" onClick={() => onGoTo(earliest)}>
              Ver {formatReferenceMonth(earliest)}
            </Button>
          )}
        </div>
      );
    }
  }
  return (
    <div
      role="alert"
      className="border-destructive/30 bg-destructive/5 text-destructive space-y-3 rounded-lg border p-6"
    >
      <p className="text-sm font-medium">Não foi possível calcular a prévia</p>
      <p className="text-sm">
        {error instanceof ApiError ? error.userMessage : 'Ocorreu um erro inesperado.'}
      </p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Tentar novamente
      </Button>
    </div>
  );
}

const SITUATION_CARDS: {
  key: keyof MappingPreview['situations'];
  label: string;
  hint: string;
}[] = [
  { key: 'alvo', label: 'Com alvo', hint: 'Chegam a uma conta do destino.' },
  { key: 'naoMapear', label: 'Não mapear', hint: 'Decididas: ficam fora do destino.' },
  { key: 'semDecisao', label: 'Sem decisão', hint: 'Pendentes: ninguém decidiu ainda.' },
  {
    key: 'semCategoria',
    label: 'Sem categoria de origem',
    hint: 'Vieram da origem sem categoria; ficam fora da cobertura.',
  },
];

function PreviewContent({
  clientId,
  destination,
  preview,
  canMaterialize,
  onStale,
}: {
  clientId: string;
  destination: MappingDestination;
  preview: MappingPreview;
  canMaterialize: boolean;
  onStale: () => void;
}) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const undecided = preview.undecidedCategories;

  return (
    <div className="flex flex-col gap-4" data-testid="mapping-preview">
      <section
        aria-labelledby="mapping-coverage-heading"
        className="bg-card space-y-4 rounded-lg border p-4"
      >
        <div className="flex flex-wrap items-start justify-between gap-3">
          <h3 id="mapping-coverage-heading" className="text-sm font-semibold">
            Prévia de {formatReferenceMonth(preview.competence)} em {destination.name}
          </h3>
          {canMaterialize && (
            <Button type="button" onClick={() => setConfirmOpen(true)}>
              <Layers className="h-4 w-4" aria-hidden="true" />
              Materializar
            </Button>
          )}
        </div>

        <dl className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-0.5">
            <dt className="text-muted-foreground text-xs">Cobertura (por valor)</dt>
            <dd className="text-2xl font-semibold tabular-nums">
              {formatPercent(preview.coveragePct)}
            </dd>
            <dd className="text-muted-foreground text-xs">
              Valor com decisão sobre o valor com categoria.
            </dd>
          </div>
          <div className="space-y-0.5">
            <dt className="text-muted-foreground text-xs">Decidido como &quot;Não mapear&quot;</dt>
            <dd className="text-2xl font-semibold tabular-nums">
              {formatPercent(preview.naoMapearPct)}
            </dd>
            <dd className="text-muted-foreground text-xs">
              Contra-métrica: cobertura alta com muito &quot;Não mapear&quot; é destino vazio.
            </dd>
          </div>
        </dl>

        <dl className="grid grid-cols-1 gap-3 border-t pt-4 sm:grid-cols-2 xl:grid-cols-4">
          {SITUATION_CARDS.map((card) => (
            <SituationStat
              key={card.key}
              label={card.label}
              hint={card.hint}
              total={preview.situations[card.key]}
            />
          ))}
        </dl>
      </section>

      <section aria-labelledby="mapping-undecided-heading" className="space-y-2">
        <h3 id="mapping-undecided-heading" className="text-sm font-semibold">
          Categorias sem decisão
        </h3>
        {undecided.length === 0 ? (
          <p className="text-muted-foreground flex items-center gap-1.5 text-sm">
            <CheckCircle2 className="text-success h-4 w-4" aria-hidden="true" />
            Todas as categorias com movimento nesta competência têm decisão.
          </p>
        ) : (
          <TableCard className="max-h-80">
            <Table fill scrollRegionLabel="Lista de categorias sem decisão (rolável)">
              <TableHeader>
                <TableRow>
                  <TableHead>Categoria</TableHead>
                  <TableHead className="whitespace-nowrap">Valor</TableHead>
                  <TableHead>Movimentos</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {undecided.map((item) => (
                  <TableRow key={`${item.sourceType}:${item.categoryCode}`}>
                    <TableCell className="font-medium tabular-nums">{item.categoryCode}</TableCell>
                    <TableCell className="whitespace-nowrap tabular-nums">
                      {formatBRL(item.amount)}
                    </TableCell>
                    <TableCell className="tabular-nums">{item.count}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableCard>
        )}
      </section>

      <MaterializedVersions latestVersion={preview.latestVersion} />

      {canMaterialize && (
        <MaterializeDialog
          open={confirmOpen}
          onOpenChange={setConfirmOpen}
          clientId={clientId}
          destination={destination}
          preview={preview}
          onStale={onStale}
        />
      )}
    </div>
  );
}

function SituationStat({
  label,
  hint,
  total,
}: {
  label: string;
  hint: string;
  total: MappingSituationTotal;
}) {
  return (
    <div className="space-y-0.5">
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="whitespace-nowrap text-lg font-semibold tabular-nums">
        {formatBRL(total.amount)}
      </dd>
      <dd className="text-muted-foreground text-xs">
        {total.count} {total.count === 1 ? 'movimento' : 'movimentos'} · {hint}
      </dd>
    </div>
  );
}

/**
 * As versões materializadas da competência, só-leitura. O contrato desta
 * sprint devolve só a ÚLTIMA versão (`latestVersion`) — as versões são
 * sequenciais e imutáveis (N+1 a cada materialização, nunca sobrescritas), então
 * a lista 1..N é derivada dela, sem data (não há rota que liste o detalhe).
 */
function MaterializedVersions({ latestVersion }: { latestVersion: number }) {
  const versions = Array.from({ length: latestVersion }, (_, index) => latestVersion - index);
  return (
    <section aria-labelledby="mapping-versions-heading" className="space-y-2">
      <h3 id="mapping-versions-heading" className="text-sm font-semibold">
        Versões materializadas
      </h3>
      {versions.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          Nenhuma versão materializada nesta competência.
        </p>
      ) : (
        <>
          <ul
            className="flex flex-wrap gap-2"
            aria-label="Versões materializadas desta competência"
          >
            {versions.map((version) => (
              <li
                key={version}
                className="bg-muted text-muted-foreground ring-border rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset"
              >
                Versão {version}
                {version === latestVersion ? ' (mais recente)' : ''}
              </li>
            ))}
          </ul>
          <p className="text-muted-foreground text-xs">
            Versões são imutáveis: materializar de novo cria a próxima, nunca altera uma anterior.
          </p>
        </>
      )}
    </section>
  );
}

/**
 * Materializar (R5). AlertDialog porque é irreversível — a versão nasce
 * imutável. Com valor SEM decisão, a confirmação é EXPLÍCITA e extra: a pessoa
 * marca que aceita a cobertura parcial, e é essa marca que vai ao servidor
 * (`confirmPartialCoverage`), que a registra na própria materialização.
 */
function MaterializeDialog({
  open,
  onOpenChange,
  clientId,
  destination,
  preview,
  onStale,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  clientId: string;
  destination: MappingDestination;
  preview: MappingPreview;
  onStale: () => void;
}) {
  const materializeMutation = useMaterializeMapping(clientId, destination.type);
  const [acceptPartial, setAcceptPartial] = useState(false);
  const undecided = preview.situations.semDecisao;
  const hasUndecided = Number(undecided.amount) !== 0 || undecided.count > 0;
  const nextVersion = preview.latestVersion + 1;
  const isPending = materializeMutation.isPending;

  function handleOpenChange(next: boolean) {
    if (isPending) return;
    if (!next) setAcceptPartial(false);
    onOpenChange(next);
  }

  async function handleConfirm() {
    try {
      const result = await materializeMutation.mutateAsync({
        competence: preview.competence,
        previewToken: preview.previewToken,
        confirmPartialCoverage: hasUndecided && acceptPartial,
      });
      toast.success(
        `Versão ${result.version} de ${formatReferenceMonth(preview.competence)} materializada.`,
      );
      setAcceptPartial(false);
      onOpenChange(false);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'PREVIA_DESATUALIZADA') {
        toast.error(err.userMessage);
        setAcceptPartial(false);
        onOpenChange(false);
        onStale();
        return;
      }
      toast.error(err instanceof ApiError ? err.userMessage : 'Não foi possível materializar.');
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>
            Materializar a versão {nextVersion} de {formatReferenceMonth(preview.competence)}?
          </AlertDialogTitle>
          <AlertDialogDescription>
            O resultado desta prévia em {destination.name} vira a versão {nextVersion}, imutável — é
            ela que a geração do arquivo contábil vai ler. Materializar de novo depois cria outra
            versão; esta não muda.
          </AlertDialogDescription>
        </AlertDialogHeader>

        {hasUndecided && (
          <div className="bg-warning-muted text-warning ring-warning/30 space-y-3 rounded-lg p-3 text-sm ring-1 ring-inset">
            <p className="flex items-start gap-1.5 font-medium">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              {formatBRL(undecided.amount)} em {undecided.count}{' '}
              {undecided.count === 1 ? 'movimento' : 'movimentos'} sem decisão ficarão fora desta
              versão.
            </p>
            <div className="flex items-center gap-2">
              <Switch
                id="mapping-accept-partial"
                checked={acceptPartial}
                onCheckedChange={setAcceptPartial}
                disabled={isPending}
              />
              <Label htmlFor="mapping-accept-partial" className="cursor-pointer text-sm">
                Materializar mesmo com cobertura parcial
              </Label>
            </div>
          </div>
        )}

        <AlertDialogFooter className="gap-2 sm:justify-between">
          <AlertDialogCancel disabled={isPending}>Cancelar</AlertDialogCancel>
          <AlertDialogAction
            variant="default"
            onClick={() => void handleConfirm()}
            disabled={isPending || (hasUndecided && !acceptPartial)}
          >
            {isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            Materializar versão {nextVersion}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function PreviewSkeleton() {
  return (
    <div className="space-y-3 rounded-lg border p-4" aria-hidden="true">
      <div className="bg-muted h-4 w-48 animate-pulse rounded" />
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="bg-muted h-10 animate-pulse rounded" />
        <div className="bg-muted h-10 animate-pulse rounded" />
      </div>
      <div className="grid gap-3 sm:grid-cols-4">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={index} className="bg-muted h-12 animate-pulse rounded" />
        ))}
      </div>
    </div>
  );
}
