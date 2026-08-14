'use client';

/**
 * Aba 3 — Anomalias (FRONT 9.16, Doc §14.6).
 *
 * Filtros: severity (all/critical/moderate/info) e status (all/pendente/resolvida).
 * Ordenação: vem ordenada do back, cronológica pela data do lançamento
 * relacionado (mais antiga primeiro), com as anomalias sem linha relacionada
 * no fim. É a mesma ordem da aba 5 do Excel e das abas de Movimentações e
 * Divergências Omie. Não reordenar no cliente: a lista é paginada, e ordenar
 * a página já carregada faria a tela divergir da paginação.
 *
 * Ações:
 *   - Pendente: "Marcar como resolvida" → `<ResolveAnomalyDialog>` (nota ≥ 10 chars).
 *   - Sempre: "Registrar anomalia" abre o modal sem source pré-vinculada
 *     (caso edge mencionado no checklist — back aceita anomalia sem
 *     file_entry_id/omie_entry_id).
 *
 * "Linha relacionada" mostra date + descrição truncada quando vinculada
 * a file_entry; quando é omie_entry, "Omie #ID Data". Sem deep-link
 * (FRONT 9.16 nota — pode ser implementado em iteração futura).
 */

import { useEffect, useState } from 'react';

import { Button } from '@/components/ui/button';
import { PaginationBar } from '@/components/ui/pagination-bar';
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
import { useAnomalies } from '@/hooks/use-reconciliations';
import type { AnomalyItem } from '@/lib/api/reconciliations';
import { hasPermission } from '@/lib/authz';
import { formatBRDate, formatBRL } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useAuthStore } from '@/stores/auth';

import { acceptsReviewVerdict, AnomalyVerdictControl } from './anomaly-verdict-control';
import { ResolveAnomalyDialog } from './resolve-anomaly-dialog';
import { SeverityBadge } from './severity-badge';

interface AnomaliesTabProps {
  sessionId: string;
}

type SeverityFilter = 'all' | 'critical' | 'moderate' | 'info';
type ResolvedFilter = 'all' | 'true' | 'false';
/** As opções vêm da `PaginationBar` (10/20/50/100) — nada de lista paralela. */
const DEFAULT_PAGE_SIZE = 20;
/** Severidade · Tipo · Linha · Detectado por · Status · Veredito · Ações. */
const COLUMN_COUNT = 7;

export function AnomaliesTab({ sessionId }: AnomaliesTabProps) {
  // Matriz do R4 numa consulta só (`lib/authz`), no topo da aba — cada linha
  // recebe a resposta por prop. Nada de `role === '...'` dentro de célula.
  const currentUser = useAuthStore((s) => s.user);
  const canReview = hasPermission(currentUser, 'review_export');
  const [severity, setSeverity] = useState<SeverityFilter>('all');
  const [resolved, setResolved] = useState<ResolvedFilter>('all');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(DEFAULT_PAGE_SIZE);
  const [resolvingId, setResolvingId] = useState<string | null>(null);

  // Trocar o tamanho da página também volta para a 1: quem está na página 8 com
  // 10 por página e escolhe 100 cairia numa página que não existe mais.
  useEffect(() => {
    setPage(1);
  }, [severity, resolved, pageSize]);

  const listQuery = useAnomalies(sessionId, { page, pageSize, severity, resolved });
  const items = listQuery.data?.data ?? [];
  const pagination = listQuery.data?.pagination;
  const totalPages = pagination?.totalPages ?? 0;
  const total = pagination?.total ?? 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <label htmlFor="anomaly-severity" className="text-muted-foreground text-xs">
            Severidade
          </label>
          <Select value={severity} onValueChange={(v) => setSeverity(v as SeverityFilter)}>
            <SelectTrigger id="anomaly-severity" className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todas</SelectItem>
              <SelectItem value="critical">Críticas</SelectItem>
              <SelectItem value="moderate">Moderadas</SelectItem>
              <SelectItem value="info">Informativas</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-1">
          <label htmlFor="anomaly-resolved" className="text-muted-foreground text-xs">
            Status
          </label>
          <Select value={resolved} onValueChange={(v) => setResolved(v as ResolvedFilter)}>
            <SelectTrigger id="anomaly-resolved" className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">Todas</SelectItem>
              <SelectItem value="false">Pendentes</SelectItem>
              <SelectItem value="true">Resolvidas</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-32">Severidade</TableHead>
              <TableHead className="w-48">Tipo</TableHead>
              <TableHead>Linha relacionada</TableHead>
              <TableHead className="w-32">Detectado por</TableHead>
              <TableHead className="w-28">Status</TableHead>
              {/* Sprint 6 / R4: "o flag procedia?" — eixo diferente de Status. */}
              <TableHead className="w-56 text-right">O flag procedia?</TableHead>
              <TableHead className="w-44 text-right">Ações</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {listQuery.isLoading && (
              <>
                {Array.from({ length: 6 }).map((_, i) => (
                  <TableRow key={i}>
                    <TableCell colSpan={COLUMN_COUNT}>
                      <div className="bg-muted h-6 animate-pulse rounded" />
                    </TableCell>
                  </TableRow>
                ))}
              </>
            )}
            {!listQuery.isLoading && items.length === 0 && (
              <TableRow>
                <TableCell
                  colSpan={COLUMN_COUNT}
                  className="text-muted-foreground py-10 text-center text-sm"
                >
                  Nenhuma anomalia registrada.
                </TableCell>
              </TableRow>
            )}
            {!listQuery.isLoading &&
              items.map((anomaly) => (
                <AnomalyRow
                  key={anomaly.id}
                  sessionId={sessionId}
                  anomaly={anomaly}
                  canReview={canReview}
                  onResolve={() => setResolvingId(anomaly.id)}
                />
              ))}
          </TableBody>
        </Table>
      </div>

      <PaginationBar
        page={page}
        pageSize={pageSize}
        total={total}
        totalPages={totalPages}
        onPageChange={setPage}
        onPageSizeChange={setPageSize}
        disabled={listQuery.isLoading}
        itemLabel="anomalias"
      />

      {resolvingId !== null && (
        <ResolveAnomalyDialog
          sessionId={sessionId}
          anomalyId={resolvingId}
          open={resolvingId !== null}
          onOpenChange={(open) => {
            if (!open) setResolvingId(null);
          }}
        />
      )}
    </div>
  );
}

interface AnomalyRowProps {
  sessionId: string;
  anomaly: AnomalyItem;
  canReview: boolean;
  onResolve: () => void;
}

function AnomalyRow({ sessionId, anomaly, canReview, onResolve }: AnomalyRowProps) {
  const relatedLabel = buildRelatedLabel(anomaly);
  const detectedByLabel = anomaly.detected_by === 'ai' ? 'Sistema' : 'Manual';
  return (
    <TableRow>
      <TableCell>
        <SeverityBadge severity={anomaly.anomaly_type.severity} />
      </TableCell>
      <TableCell className="text-sm">
        <div className="flex flex-col">
          <span className="font-medium">{anomaly.anomaly_type.name}</span>
          {anomaly.context !== null && anomaly.context.trim() !== '' && (
            <span className="text-muted-foreground text-xs">{anomaly.context}</span>
          )}
          {anomaly.resolution_note !== null && anomaly.resolution_note.trim() !== '' && (
            <span className="mt-0.5 text-xs italic text-emerald-700 dark:text-emerald-300">
              Resolução: {anomaly.resolution_note}
            </span>
          )}
        </div>
      </TableCell>
      <TableCell className="text-muted-foreground text-sm">{relatedLabel}</TableCell>
      <TableCell className="text-muted-foreground text-sm">{detectedByLabel}</TableCell>
      <TableCell>
        <StatusPill resolved={anomaly.resolved} />
      </TableCell>
      <TableCell className="text-right">
        {/* Só flag da Camada 1 aceita veredito — nos demais tipos o servidor
            devolve 400, então nem se oferece a ação (a coluna fica com o
            travessão em vez de um botão que erra). */}
        {acceptsReviewVerdict(anomaly) ? (
          <AnomalyVerdictControl sessionId={sessionId} anomaly={anomaly} canReview={canReview} />
        ) : (
          <span className="text-muted-foreground text-xs">—</span>
        )}
      </TableCell>
      <TableCell className="text-right">
        {!anomaly.resolved ? (
          <Button size="sm" variant="outline" onClick={onResolve}>
            Marcar como resolvida
          </Button>
        ) : (
          <span className="text-muted-foreground text-xs">—</span>
        )}
      </TableCell>
    </TableRow>
  );
}

function StatusPill({ resolved }: { resolved: boolean }) {
  return (
    <span
      className={cn(
        'inline-flex rounded-full px-2 py-0.5 text-xs font-medium',
        resolved
          ? 'bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-200'
          : 'bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-200',
      )}
    >
      {resolved ? 'Resolvida' : 'Pendente'}
    </span>
  );
}

function buildRelatedLabel(anomaly: AnomalyItem): string {
  if (anomaly.related_file_entry !== null) {
    const fe = anomaly.related_file_entry;
    const desc = fe.description.length > 50 ? `${fe.description.slice(0, 47)}…` : fe.description;
    return `${formatBRDate(fe.transaction_date).slice(0, 5)} · ${desc} (${formatBRL(fe.amount, { signed: true })})`;
  }
  if (anomaly.related_omie_entry !== null) {
    const oe = anomaly.related_omie_entry;
    return `${formatBRDate(oe.transaction_date).slice(0, 5)} · Omie #${oe.omie_lancamento_id}`;
  }
  return '—';
}
