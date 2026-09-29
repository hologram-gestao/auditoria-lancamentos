'use client';

/**
 * A prévia no destino `conta_contabil` (Sprint 16 — FRONT 16.6 / R3 · R4).
 *
 * Três peças, todas lidas do SERVIDOR (esta tela não soma nada):
 *   - **completude de partida agregada** (BACK 16.4): Σ|valor| com partida
 *     completa (conta do plano + conta do banco + histórico) ÷ Σ|valor| com
 *     alvo, com os dois valores para a conta ser conferível. `pct` nulo = sem
 *     linha com conta — mostrado como "—" e dito, nunca "0%";
 *   - **contas de origem sem conta do banco**, em destaque, com o caminho para
 *     Plano contábil › Conta do banco (é o que faria a materialização recusar);
 *   - **por categoria com alvo**: conta (código e nome), histórico (truncado com
 *     dica acessível) e o que a deixa INCOMPLETA — sem histórico, conta do banco
 *     pendente, decisão legada do catálogo.
 */

import { AlertTriangle, CheckCircle2 } from 'lucide-react';

import {
  Table,
  TableBody,
  TableCard,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type {
  AccountingCategoryPreview,
  MappingPreview,
  PartidaCompleteness,
} from '@/lib/contracts';
import { formatBRL, formatPercent } from '@/lib/format';
import { cn } from '@/lib/utils';

import {
  LegacyRedoBadge,
  PendingSourceAccountsNotice,
  TruncatedHistory,
} from './accounting-destination';

const baseBadge =
  'inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset';

export function AccountingPreviewSection({
  clientId,
  preview,
  canManageChart,
  isClosed,
}: {
  clientId: string;
  preview: MappingPreview;
  /** `manage_client_accounting_chart` — gateia o "Associar" das pendentes. */
  canManageChart: boolean;
  isClosed: boolean;
}) {
  const categories = preview.accountingCategories ?? [];
  const pending = preview.pendingSourceAccounts ?? [];
  return (
    <section
      aria-labelledby="mapping-partida-heading"
      className="bg-card space-y-4 rounded-lg border p-4"
      data-testid="mapping-accounting-preview"
    >
      <div className="space-y-1">
        <h3 id="mapping-partida-heading" className="text-sm font-semibold">
          Partida contábil
        </h3>
        <p className="text-muted-foreground text-sm">
          Cada linha com conta vira uma partida: a conta da categoria de um lado, a conta do banco
          da conta de origem do outro (débito ou crédito pelo sinal) e o histórico padrão.
        </p>
      </div>

      {preview.partidaCompleteness != null && (
        <PartidaCompletenessBlock completeness={preview.partidaCompleteness} />
      )}

      {pending.length > 0 && (
        <PendingSourceAccountsNotice
          clientId={clientId}
          accounts={pending}
          canManageChart={canManageChart}
          isClosed={isClosed}
        />
      )}

      {categories.length === 0 ? (
        <p className="text-muted-foreground text-sm">
          Nenhuma categoria com conta nesta competência.
        </p>
      ) : (
        <TableCard className="max-h-96">
          <Table fill scrollRegionLabel="Partida por categoria (rolável)">
            <TableHeader>
              <TableRow>
                <TableHead>Categoria</TableHead>
                <TableHead>Conta</TableHead>
                <TableHead>Histórico</TableHead>
                <TableHead className="whitespace-nowrap text-right">Valor</TableHead>
                <TableHead>Partida</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {categories.map((category) => (
                <CategoryRow
                  key={`${category.sourceType}:${category.categoryCode}`}
                  category={category}
                />
              ))}
            </TableBody>
          </Table>
        </TableCard>
      )}
    </section>
  );
}

/** A métrica da sprint, com numerador e denominador à vista. */
export function PartidaCompletenessBlock({ completeness }: { completeness: PartidaCompleteness }) {
  const noTarget = completeness.pct == null;
  return (
    <dl className="grid gap-3 sm:grid-cols-2" data-testid="partida-completeness">
      <div className="space-y-0.5">
        <dt className="text-muted-foreground text-xs">Partida completa (por valor)</dt>
        <dd className="text-2xl font-semibold tabular-nums">
          {noTarget ? '—' : formatPercent(completeness.pct)}
        </dd>
        <dd className="text-muted-foreground text-xs">
          {noTarget
            ? 'Nenhuma linha com conta nesta competência: não há partida para medir.'
            : 'Conta do plano, conta do banco e histórico presentes, sobre o valor com conta.'}
        </dd>
      </div>
      <div className="space-y-0.5">
        <dt className="text-muted-foreground text-xs">Valor com partida completa</dt>
        <dd className="whitespace-nowrap text-lg font-semibold tabular-nums">
          {formatBRL(completeness.completeAmount)}
        </dd>
        <dd className="text-muted-foreground whitespace-nowrap text-xs tabular-nums">
          de {formatBRL(completeness.targetAmount)} com conta
        </dd>
      </div>
    </dl>
  );
}

function CategoryRow({ category }: { category: AccountingCategoryPreview }) {
  const bankPending = category.pendingSourceAccounts.length > 0;
  const complete =
    !category.requiresRedo && !category.historyMissing && !bankPending && category.count > 0
      ? category.completeCount === category.count
      : false;
  return (
    <TableRow>
      {/* 86e3fxqqh: nome embaixo do código, pela MESMA resolução (e o MESMO texto de
          fallback) da coluna Categoria na lista de decisões (mapping-list-panel.tsx). */}
      <TableCell className="min-w-40 whitespace-normal">
        <span className="block font-medium tabular-nums">{category.categoryCode}</span>
        {category.categoryNameResolved && category.categoryName ? (
          <span className="text-muted-foreground block text-xs">{category.categoryName}</span>
        ) : (
          <span className="text-muted-foreground block text-xs">Nome indisponível agora</span>
        )}
      </TableCell>
      <TableCell className="min-w-40 whitespace-normal">
        {category.requiresRedo ? (
          <span className="text-muted-foreground text-sm">Decisão do catálogo (legado)</span>
        ) : category.accountingAccountCode ? (
          <>
            <span className="block tabular-nums">{category.accountingAccountCode}</span>
            {category.accountingAccountName && (
              <span className="text-muted-foreground block text-xs">
                {category.accountingAccountName}
              </span>
            )}
          </>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </TableCell>
      <TableCell className="max-w-56">
        <TruncatedHistory history={category.history} />
      </TableCell>
      <TableCell className="whitespace-nowrap text-right tabular-nums">
        {formatBRL(category.amount)}
        <span className="text-muted-foreground block text-xs">
          {category.count} {category.count === 1 ? 'movimento' : 'movimentos'}
        </span>
      </TableCell>
      <TableCell className="min-w-44 whitespace-normal">
        <div className="flex flex-wrap items-center gap-1.5">
          {complete ? (
            <span className={cn(baseBadge, 'bg-success-muted text-success ring-success/30')}>
              <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
              Completa
            </span>
          ) : (
            <>
              <span className="sr-only">Incompleta:</span>
              {category.requiresRedo && <LegacyRedoBadge />}
              {category.historyMissing && !category.requiresRedo && (
                <span className={cn(baseBadge, 'bg-warning-muted text-warning ring-warning/30')}>
                  <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                  Falta histórico
                </span>
              )}
              {bankPending && (
                <span className={cn(baseBadge, 'bg-warning-muted text-warning ring-warning/30')}>
                  <AlertTriangle className="h-3 w-3" aria-hidden="true" />
                  Conta do banco pendente
                </span>
              )}
            </>
          )}
        </div>
        {!complete && category.count > 0 && (
          <span className="text-muted-foreground mt-1 block text-xs tabular-nums">
            {category.completeCount} de {category.count} completas
          </span>
        )}
      </TableCell>
    </TableRow>
  );
}
