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

import { AlertTriangle } from 'lucide-react';

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
import type { ChecksumResult, ParsedStatement, ParsedTransaction } from '@/lib/api/reconciliations';
import { formatAccountType, formatBRDate, formatBRL } from '@/lib/format';
import { cn } from '@/lib/utils';

const PREVIEW_ROW_LIMIT = 5;

interface ParsePreviewProps {
  parsed: ParsedStatement;
  /**
   * BACK 02.3 — checksum de saldos. Quando `ok=false`, a confirmação é
   * BLOQUEADA e o motivo é exibido: é a defesa contra parse incompleto
   * (linhas perdidas fazem a identidade de saldos não fechar).
   * `null` = prévia sem checksum (ex.: chamada legada) → não bloqueia.
   */
  checksum: ChecksumResult | null;
  /** FRONT 1.4: conta de cartão → título/legenda específicos de fatura. */
  isCard: boolean;
  /** Nome da conta selecionada — usado no título da prévia de fatura. */
  accountName: string;
  onCancel: () => void;
  onConfirm: () => void;
  /** `true` enquanto a mutation `POST /api/v1/reconciliations` está em voo
   *  (S10). Desabilita os botões pra evitar duplo submit; ao virar `true`
   *  o `Cancelar` também desabilita pra impedir reset do `parsed` no meio. */
  isConfirming: boolean;
}

export function ParsePreview({
  parsed,
  checksum,
  isCard,
  accountName,
  onCancel,
  onConfirm,
  isConfirming,
}: ParsePreviewProps) {
  const previewRows = parsed.transactions.slice(0, PREVIEW_ROW_LIMIT);
  const totalCount = parsed.transactions.length;
  const hasMore = totalCount > PREVIEW_ROW_LIMIT;
  // Só bloqueia quando o checksum é aplicável E não fechou. Conta aplicação
  // (`applicable=false`) nunca bloqueia: rendimento/IOF/IR entram no saldo sem
  // virar movimentação, então a identidade não fecha nem num parse perfeito.
  const checksumBlocks = checksum !== null && checksum.applicable && !checksum.ok;

  return (
    <section aria-labelledby="parse-preview-title" className="space-y-6">
      <header className="space-y-1">
        <h2 id="parse-preview-title" className="text-xl font-semibold">
          {isCard ? `Prévia da fatura — ${accountName}` : 'Confirme as movimentações extraídas'}
        </h2>
        <p className="text-muted-foreground text-sm">
          Verifique se os dados abaixo correspondem ao arquivo enviado antes de confirmar.
        </p>
        {isCard && (
          <p className="text-muted-foreground text-xs">
            Valores negativos = compras · Valores positivos = estornos ou créditos.
          </p>
        )}
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

      {checksumBlocks && checksum?.reason && <ChecksumBlockAlert reason={checksum.reason} />}

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button type="button" variant="outline" onClick={onCancel} disabled={isConfirming}>
          {checksumBlocks ? 'Selecionar outro arquivo' : 'Cancelar'}
        </Button>
        <Button
          type="button"
          onClick={onConfirm}
          disabled={isConfirming || checksumBlocks}
          title={
            checksumBlocks
              ? 'Os saldos do arquivo não fecham — revise o extrato antes de conciliar.'
              : undefined
          }
        >
          Confirmar e processar
        </Button>
      </div>
    </section>
  );
}

/**
 * BACK 02.3 — bloqueio por checksum. Mesmo padrão visual do
 * `DuplicateBlockAlert` do formulário: sem rota de "continuar mesmo assim",
 * porque conciliar um extrato que não fecha propaga o erro para o Omie.
 * A `reason` vem pronta do backend (PT-BR, com os valores e a diferença).
 */
function ChecksumBlockAlert({ reason }: { reason: string }) {
  return (
    <div
      role="alert"
      className="bg-destructive/5 border-destructive/30 text-destructive flex items-start gap-3 rounded-lg border p-4 text-sm"
    >
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden="true" />
      <div className="space-y-1">
        <p className="font-semibold">Os saldos não fecham</p>
        <p className="leading-snug">{reason}</p>
      </div>
    </div>
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
