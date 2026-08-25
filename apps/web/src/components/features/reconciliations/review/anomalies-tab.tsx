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
 *
 * **Sprint 7 (FRONT 07.6).** Em sessão de CARTÃO, a anomalia cuja linha do
 * arquivo ainda está `sem_omie` ganha "Lançar no Omie" e entra na seleção do
 * lote. A elegibilidade NÃO é deduzida do código da anomalia: `AnomalyItem` não
 * carrega `situation` nem `omie_lancamento_id`, e uma `missing_in_omie` segue
 * aberta mesmo depois de o operador ignorar a linha. Quem responde é a lista
 * real de linhas `sem_omie` (`useAllSemOmieEntries`), cruzada por id.
 */

import { useEffect, useMemo, useState } from 'react';

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
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useAllSemOmieEntries, useAnomalies } from '@/hooks/use-reconciliations';
import type { AnomalyItem, FileEntryItem } from '@/lib/api/reconciliations';
import { hasPermission } from '@/lib/authz';
import { formatBRDate, formatBRL } from '@/lib/format';
import { cn } from '@/lib/utils';
import { useAuthStore } from '@/stores/auth';

import { acceptsReviewVerdict, AnomalyVerdictControl } from './anomaly-verdict-control';
import { LancarLoteBar, LancarNoOmieButton, PostingCheckbox } from './lancar-no-omie-controls';
import { LancarNoOmieDrawer } from './lancar-no-omie-drawer';
import { getPostingBlock, type PostingBlockReason } from './omie-posting-eligibility';
import { ResolveAnomalyDialog } from './resolve-anomaly-dialog';
import { SeverityBadge } from './severity-badge';

interface AnomaliesTabProps {
  sessionId: string;
  /** Sessão de cartão — só ela lança no Omie (FRONT 07.6). */
  isCard: boolean;
}

type SeverityFilter = 'all' | 'critical' | 'moderate' | 'info';
type ResolvedFilter = 'all' | 'true' | 'false';
/** As opções vêm da `PaginationBar` (10/20/50/100) — nada de lista paralela. */
const DEFAULT_PAGE_SIZE = 20;
/** Severidade · Tipo · Linha · Origem · Status · Veredito · Ações. */
const BASE_COLUMN_COUNT = 7;

export function AnomaliesTab({ sessionId, isCard }: AnomaliesTabProps) {
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
  // Memoizado porque a elegibilidade do lançamento (abaixo) deriva daqui: um
  // array novo a cada render recalcularia os `useMemo` sem nada ter mudado.
  const items = useMemo(() => listQuery.data?.data ?? [], [listQuery.data]);
  const pagination = listQuery.data?.pagination;
  const totalPages = pagination?.totalPages ?? 0;
  const total = pagination?.total ?? 0;

  // ---------------------------------------------------------------------
  // Sprint 7 / FRONT 07.6 — lançamento a partir da anomalia
  // ---------------------------------------------------------------------

  const semOmieQuery = useAllSemOmieEntries(sessionId, { enabled: isCard });
  const semOmieById = useMemo(() => {
    const map = new Map<string, FileEntryItem>();
    semOmieQuery.data?.forEach((entry) => map.set(entry.id, entry));
    return map;
  }, [semOmieQuery.data]);

  /**
   * Linha lançável por trás de cada anomalia da página. `null` quando a
   * anomalia não tem linha de arquivo, quando a linha saiu de `sem_omie` (já
   * conciliada/ignorada/lançada) ou quando a sessão não é de cartão.
   */
  const entryByAnomaly = useMemo(() => {
    const map = new Map<string, FileEntryItem>();
    if (!isCard) return map;
    items.forEach((anomaly) => {
      const relatedId = anomaly.related_file_entry?.id;
      if (relatedId === undefined) return;
      const entry = semOmieById.get(relatedId);
      if (entry !== undefined) map.set(anomaly.id, entry);
    });
    return map;
  }, [items, semOmieById, isCard]);

  const [selectedAnomalyIds, setSelectedAnomalyIds] = useState<string[]>([]);
  const [postedEntryIds, setPostedEntryIds] = useState<string[]>([]);
  const [launchTargets, setLaunchTargets] = useState<FileEntryItem[] | null>(null);

  const selectableAnomalyIds = useMemo(
    () => items.filter((a) => entryByAnomaly.has(a.id)).map((a) => a.id),
    [items, entryByAnomaly],
  );

  // Mesma poda da aba de Movimentações: seleção é da PÁGINA e do que continua
  // elegível — trocar de filtro/página não pode levar id pendurado para o lote.
  useEffect(() => {
    setSelectedAnomalyIds((prev) => {
      const next = prev.filter((id) => selectableAnomalyIds.includes(id));
      return next.length === prev.length ? prev : next;
    });
  }, [selectableAnomalyIds]);

  /**
   * DUAS anomalias podem apontar para a MESMA linha do arquivo (ex.: valor
   * divergente + sem correspondente). O backend recusa o lote inteiro com 422
   * quando um `file_entry_id` se repete, então a deduplicação acontece aqui,
   * na montagem — não no servidor.
   */
  const selectedEntries = useMemo(() => {
    const byEntryId = new Map<string, FileEntryItem>();
    selectedAnomalyIds.forEach((anomalyId) => {
      const entry = entryByAnomaly.get(anomalyId);
      if (entry !== undefined) byEntryId.set(entry.id, entry);
    });
    return [...byEntryId.values()];
  }, [selectedAnomalyIds, entryByAnomaly]);

  const allSelectableSelected =
    selectableAnomalyIds.length > 0 && selectedAnomalyIds.length === selectableAnomalyIds.length;
  const columnCount = isCard ? BASE_COLUMN_COUNT + 1 : BASE_COLUMN_COUNT;

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

      {isCard && selectedAnomalyIds.length > 0 && (
        <LancarLoteBar
          selectedCount={selectedEntries.length}
          onLaunch={() => setLaunchTargets(selectedEntries)}
          onClear={() => setSelectedAnomalyIds([])}
        />
      )}

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              {isCard && (
                <TableHead className="w-10">
                  <PostingCheckbox
                    checked={allSelectableSelected}
                    indeterminate={selectedAnomalyIds.length > 0}
                    disabled={selectableAnomalyIds.length === 0}
                    label="Selecionar todas as compras desta página que podem ser lançadas no Omie"
                    onChange={(checked) =>
                      setSelectedAnomalyIds(checked ? selectableAnomalyIds : [])
                    }
                  />
                </TableHead>
              )}
              {/* 86e2xmug9 — economia de largura: no auto-layout do browser os
                  `w-*` são preferência, não lei. Quem manda é o conteúdo: por
                  isso TIPO (o conteúdo denso — nome + context clampado) é a
                  ÚNICA coluna sem largura, com `min-w-56` de piso, e a LINHA
                  RELACIONADA é limitada por um bloco interno `max-w-56` na
                  célula — cap no `th` sozinho não segura a célula esticada. */}
              <TableHead className="w-24">Severidade</TableHead>
              <TableHead className="min-w-48">Tipo</TableHead>
              <TableHead className="w-56">Linha relacionada</TableHead>
              <TableHead className="w-24">Origem</TableHead>
              <TableHead className="w-24">Status</TableHead>
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
                    <TableCell colSpan={columnCount}>
                      <div className="bg-muted h-6 animate-pulse rounded" />
                    </TableCell>
                  </TableRow>
                ))}
              </>
            )}
            {!listQuery.isLoading && items.length === 0 && (
              <TableRow>
                <TableCell
                  colSpan={columnCount}
                  className="text-muted-foreground py-10 text-center text-sm"
                >
                  {/* 86e2u513j — mesma distinção da aba Movimentações: com
                      filtro ativo, "Nenhuma anomalia registrada" afirmaria
                      sobre a conciliação INTEIRA o que só vale para o recorte
                      — quem filtra por "Críticas" numa sessão só com
                      moderadas concluiria que está tudo limpo. */}
                  {severity !== 'all' || resolved !== 'all' ? (
                    <div className="space-y-2">
                      <p>Nenhuma anomalia encontrada com os filtros selecionados.</p>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => {
                          setSeverity('all');
                          setResolved('all');
                        }}
                      >
                        Limpar filtros
                      </Button>
                    </div>
                  ) : (
                    'Nenhuma anomalia registrada.'
                  )}
                </TableCell>
              </TableRow>
            )}
            {!listQuery.isLoading &&
              items.map((anomaly) => {
                const entry = entryByAnomaly.get(anomaly.id);
                return (
                  <AnomalyRow
                    key={anomaly.id}
                    sessionId={sessionId}
                    anomaly={anomaly}
                    canReview={canReview}
                    isCard={isCard}
                    postingBlock={resolvePostingBlock(anomaly, entry, {
                      isCard,
                      posted: postedEntryIds,
                    })}
                    selected={selectedAnomalyIds.includes(anomaly.id)}
                    onSelectedChange={(checked) =>
                      setSelectedAnomalyIds((prev) =>
                        checked
                          ? [...new Set([...prev, anomaly.id])]
                          : prev.filter((id) => id !== anomaly.id),
                      )
                    }
                    onLancar={() => {
                      if (entry !== undefined) setLaunchTargets([entry]);
                    }}
                    onResolve={() => setResolvingId(anomaly.id)}
                  />
                );
              })}
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

      {launchTargets !== null && (
        <LancarNoOmieDrawer
          sessionId={sessionId}
          entries={launchTargets}
          open
          onOpenChange={(open) => {
            if (!open) setLaunchTargets(null);
          }}
          onPosted={(results) => {
            const ids = Object.keys(results);
            setPostedEntryIds((prev) => [...new Set([...prev, ...ids])]);
            setSelectedAnomalyIds((prev) =>
              prev.filter((anomalyId) => {
                const entry = entryByAnomaly.get(anomalyId);
                return entry === undefined || !ids.includes(entry.id);
              }),
            );
          }}
        />
      )}

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

/**
 * Motivo do bloqueio da ação numa linha de anomalia.
 *
 * A anomalia não conhece o estado da linha — quem conhece é a lista de
 * `sem_omie`. "Não achei a linha ali" significa que ela deixou de ser lançável
 * (conciliada, ignorada ou já lançada); `posted` distingue o caso que acabou de
 * acontecer nesta tela, em que o motivo exato é conhecido.
 */
function resolvePostingBlock(
  anomaly: AnomalyItem,
  entry: FileEntryItem | undefined,
  options: { isCard: boolean; posted: string[] },
): PostingBlockReason | null {
  if (!options.isCard) return 'sessao_nao_e_cartao';
  if (entry !== undefined) return getPostingBlock(entry, { isCard: true });
  const relatedId = anomaly.related_file_entry?.id;
  if (relatedId !== undefined && options.posted.includes(relatedId)) return 'ja_lancada';
  return 'nao_e_sem_omie';
}

interface AnomalyRowProps {
  sessionId: string;
  anomaly: AnomalyItem;
  canReview: boolean;
  isCard: boolean;
  postingBlock: PostingBlockReason | null;
  selected: boolean;
  onSelectedChange: (checked: boolean) => void;
  onLancar: () => void;
  onResolve: () => void;
}

function AnomalyRow({
  sessionId,
  anomaly,
  canReview,
  isCard,
  postingBlock,
  selected,
  onSelectedChange,
  onLancar,
  onResolve,
}: AnomalyRowProps) {
  const relatedLabel = buildRelatedLabel(anomaly);
  const detectedByLabel = anomaly.detected_by === 'ai' ? 'Sistema' : 'Manual';
  /** Sem linha de arquivo não há o que lançar — a coluna fica vazia. */
  const showPosting = isCard && anomaly.related_file_entry !== null;
  return (
    <TableRow>
      {isCard && (
        <TableCell>
          {showPosting && (
            <PostingCheckbox
              checked={selected}
              disabled={postingBlock !== null}
              label={`Selecionar a compra relacionada: ${relatedLabel}`}
              onChange={onSelectedChange}
            />
          )}
        </TableCell>
      )}
      <TableCell>
        <SeverityBadge severity={anomaly.anomaly_type.severity} />
      </TableCell>
      <TableCell className="text-sm">
        <div className="flex flex-col">
          <span className="font-medium">{anomaly.anomaly_type.name}</span>
          {anomaly.context !== null && anomaly.context.trim() !== '' && (
            <TextoClampado texto={anomaly.context} className="text-muted-foreground text-xs" />
          )}
          {anomaly.resolution_note !== null && anomaly.resolution_note.trim() !== '' && (
            <TextoClampado
              texto={`Resolução: ${anomaly.resolution_note}`}
              className="text-success mt-0.5 text-xs italic"
            />
          )}
        </div>
      </TableCell>
      <TableCell className="text-muted-foreground text-sm">
        {/* O cap REAL da coluna: no auto-layout a célula estica até o conteúdo,
            então é o bloco interno que limita — o rótulo já sai truncado em
            50 chars do `buildRelatedLabel`. O `min-w-40` é o piso: sem ele o
            Tipo flexível espremia esta coluna a ~110px e o rótulo virava uma
            escada de 6 linhas (pego por print na 86e2xmug9). */}
        <span className="block min-w-40 max-w-56">{relatedLabel}</span>
      </TableCell>
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
        <div className="flex flex-wrap items-center justify-end gap-2">
          {showPosting && <LancarNoOmieButton block={postingBlock} onClick={onLancar} />}
          {!anomaly.resolved ? (
            <Button size="sm" variant="outline" onClick={onResolve}>
              Marcar como resolvida
            </Button>
          ) : (
            !showPosting && <span className="text-muted-foreground text-xs">—</span>
          )}
        </div>
      </TableCell>
    </TableRow>
  );
}

/**
 * Texto longo da célula Tipo com `line-clamp-2` — é o clamp que devolve o
 * ritmo vertical à tabela (86e2xmug9: cada `context` de tamanho diferente
 * fazia uma altura de linha diferente). O conteúdo INTEIRO continua
 * alcançável pelo padrão do `BadgeComDica` (fix a09a7c3): Tooltip do design
 * system, `role="img"` (ARIA proíbe `aria-label` em span de role genérico) com
 * o `aria-label` carregando o texto COMPLETO — anunciado sem tooltip aberto —
 * e `tabIndex={0}` com anel de foco (o Radix abre a dica no foco). Nunca
 * `title` nativo: não aparece em toque, não alcança teclado, leitor ignora.
 */
function TextoClampado({ texto, className }: { texto: string; className: string }) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            role="img"
            tabIndex={0}
            aria-label={texto}
            className={cn(
              'focus-visible:ring-ring line-clamp-2 rounded focus-visible:outline-none focus-visible:ring-2',
              className,
            )}
          >
            {texto}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-sm whitespace-pre-line text-xs leading-snug">
          {texto}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function StatusPill({ resolved }: { resolved: boolean }) {
  return (
    <span
      className={cn(
        'inline-flex rounded-full px-2 py-0.5 text-xs font-medium',
        resolved ? 'bg-success-muted text-success' : 'bg-warning-muted text-warning',
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
