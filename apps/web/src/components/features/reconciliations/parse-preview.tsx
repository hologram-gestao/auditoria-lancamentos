'use client';

/**
 * Tela de validação do parsing — `[FRONT 7.2]`, Doc §12.3.
 *
 * Renderizada inline pelo `NewReconciliationForm` quando o `view` muda para
 * `'preview'`. Não navega para outra rota: a posição na URL continua sendo
 * `/clientes/{id}/conciliacao/nova`, o que permite voltar ao formulário com
 * todos os values intactos (ver §pitfalls do briefing).
 *
 * Nada é persistido até o usuário clicar em "Confirmar e processar". Se ele
 * cancelar, o `ParsedStatement` simplesmente sai do estado React do form pai.
 *
 * Observações de acessibilidade:
 *   - `<table>` semântica (via shadcn `Table`) com `<caption>` lido por leitores
 *     de tela como descrição da tabela.
 *   - Cores não são o único canal de informação: o sinal aritmético (`-`/`+`)
 *     no número também distingue débito de crédito para usuários com daltonismo
 *     ou em monocromia.
 */

import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import type { ParsedStatement, ParsedTransaction } from '@/lib/api/reconciliations';
import { formatAccountType, formatBRDate, formatBRL } from '@/lib/format';
import { cn } from '@/lib/utils';

const PREVIEW_ROW_LIMIT = 5;

interface ParsePreviewProps {
  parsed: ParsedStatement;
  onCancel: () => void;
  onConfirm: () => void;
  /** Mantido para forward-compat com S10 — quando a confirmação virar mutation
   *  real, este flag desabilita os botões e troca o label. Em S9 fica `false`. */
  isConfirming: boolean;
}

export function ParsePreview({ parsed, onCancel, onConfirm, isConfirming }: ParsePreviewProps) {
  const previewRows = parsed.transactions.slice(0, PREVIEW_ROW_LIMIT);
  const totalCount = parsed.transactions.length;
  const hasMore = totalCount > PREVIEW_ROW_LIMIT;

  return (
    <section aria-labelledby="parse-preview-title" className="space-y-6">
      <header className="space-y-1">
        <h2 id="parse-preview-title" className="text-xl font-semibold">
          Confirme as movimentações extraídas
        </h2>
        <p className="text-muted-foreground text-sm">
          Verifique se os dados abaixo correspondem ao arquivo enviado antes de confirmar.
        </p>
      </header>

      <MetadataGrid parsed={parsed} totalCount={totalCount} />

      <div className="space-y-2">
        <h3 className="text-sm font-medium">
          Primeiras {Math.min(PREVIEW_ROW_LIMIT, totalCount)} transações
        </h3>
        <div className="rounded-md border">
          <Table>
            <TableCaption className="sr-only">
              Prévia das primeiras {PREVIEW_ROW_LIMIT} transações extraídas do arquivo.
            </TableCaption>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[120px]">Data</TableHead>
                <TableHead>Descrição</TableHead>
                <TableHead className="w-[160px] text-right">Valor</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {previewRows.map((tx, idx) => (
                <PreviewRow key={`${tx.date}-${idx}`} tx={tx} />
              ))}
            </TableBody>
          </Table>
        </div>
        {hasMore && (
          <p className="text-muted-foreground text-xs">
            Mostrando {PREVIEW_ROW_LIMIT} de {totalCount} transações.
          </p>
        )}
      </div>

      <div
        role="note"
        className="bg-muted/40 text-muted-foreground rounded-md border px-4 py-3 text-sm"
      >
        Confira os dados antes de continuar. Após confirmar, o processamento será disparado em
        background.
      </div>

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="outline" onClick={onCancel} disabled={isConfirming}>
          Cancelar
        </Button>
        <Button type="button" onClick={onConfirm} disabled={isConfirming}>
          Confirmar e processar
        </Button>
      </div>
    </section>
  );
}

interface MetadataGridProps {
  parsed: ParsedStatement;
  totalCount: number;
}

function MetadataGrid({ parsed, totalCount }: MetadataGridProps) {
  return (
    <dl className="bg-card grid grid-cols-1 gap-x-6 gap-y-3 rounded-lg border p-4 sm:grid-cols-2">
      <Field label="Banco identificado" value={parsed.bank_name} />
      <Field label="Tipo de conta" value={formatAccountType(parsed.account_type)} />
      <Field
        label="Período"
        value={`${formatBRDate(parsed.period_start)} a ${formatBRDate(parsed.period_end)}`}
      />
      <Field
        label="Total de transações"
        value={`${totalCount} ${totalCount === 1 ? 'movimentação' : 'movimentações'}`}
      />
      <Field label="Saldo inicial" value={formatBRL(parsed.opening_balance)} />
      <Field label="Saldo final" value={formatBRL(parsed.closing_balance)} />
    </dl>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="space-y-0.5">
      <dt className="text-muted-foreground text-xs font-medium uppercase tracking-wide">{label}</dt>
      <dd className="text-sm font-medium">{value}</dd>
    </div>
  );
}

function PreviewRow({ tx }: { tx: ParsedTransaction }) {
  const numeric = Number(tx.amount);
  const isCredit = Number.isFinite(numeric) && numeric > 0;
  const isDebit = Number.isFinite(numeric) && numeric < 0;
  return (
    <TableRow>
      <TableCell className="font-mono text-xs">{formatBRDate(tx.date)}</TableCell>
      <TableCell className="max-w-[480px] truncate" title={tx.description}>
        {tx.description}
      </TableCell>
      <TableCell
        className={cn(
          'text-right font-mono text-sm tabular-nums',
          isCredit && 'text-emerald-600 dark:text-emerald-400',
          isDebit && 'text-red-600 dark:text-red-400',
        )}
      >
        {formatBRL(tx.amount, { signed: true })}
      </TableCell>
    </TableRow>
  );
}
