'use client';

/**
 * A prévia da importação do plano contábil (86e3qhhrm), dentro da gaveta.
 *
 * Mostra o que a planilha FARIA, como o servidor calculou (`POST …/import/preview`,
 * a mesma diferença da importação): contas novas, alteradas (com os campos que
 * mudam), sem mudança e as que NÃO vieram. A reimportação não inativa: a ausente
 * continua como estava e ganha o selo "não veio na última importação", e a prévia
 * diz quantas delas o de-para ou a conta do banco usam, que são as que podem
 * levar uma conta inexistente ao arquivo contábil.
 *
 * Nada é calculado aqui: contagens e listas vêm da resposta, e as listas param no
 * teto do servidor (`listLimit`), dito na tela quando a contagem passa dele.
 */

import { AlertTriangle, Info } from 'lucide-react';

import { ScrollRegion } from '@/components/ui/scroll-region';
import type {
  AccountingChartImportPreview,
  AccountingChartPreviewChangedAccount,
} from '@/lib/contracts';

const CHANGE_LABELS: Record<AccountingChartPreviewChangedAccount['changes'][number], string> = {
  nome: 'nome',
  tipo: 'tipo',
  classificacao: 'classificação',
};

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Quantas das ausentes estão em uso, com a concordância de cada caso. */
function absentInUseText(inUse: number, absent: number): string {
  if (absent === 1) return 'Ela está em uso no de-para ou como conta do banco.';
  return `${plural(inUse, 'delas está em uso', 'delas estão em uso')} no de-para ou como conta do banco.`;
}

/** "649 · Banco conta movimento" — o rótulo de uma conta nas listas da prévia. */
function accountLine(code: string, name: string): string {
  return `${code} · ${name}`;
}

export function AccountingChartImportPreviewPanel({
  preview,
}: {
  preview: AccountingChartImportPreview;
}) {
  const inUse = preview.contasAusentesEmUso;
  return (
    <section
      aria-labelledby="accounting-chart-preview-heading"
      data-testid="accounting-chart-import-preview"
      className="space-y-4"
    >
      <h3 id="accounting-chart-preview-heading" className="text-sm font-semibold">
        O que esta planilha muda no plano
      </h3>

      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <PreviewStat label="Novas" value={preview.contasNovas} />
        <PreviewStat label="Alteradas" value={preview.contasAlteradas} />
        <PreviewStat label="Sem mudança" value={preview.contasSemMudanca} />
        <PreviewStat label="Não vieram" value={preview.contasAusentes} />
      </dl>

      {!preview.temMudancas && (
        <p role="status" className="bg-muted text-foreground rounded-lg p-3 text-sm">
          Esta planilha não muda nada no plano contábil: todas as contas já estão como nela.
        </p>
      )}

      {preview.contasAusentes > 0 && (
        <div
          data-testid="accounting-chart-preview-absent-note"
          className="bg-warning-muted text-warning ring-warning/30 space-y-1 rounded-lg p-3 text-sm ring-1 ring-inset"
        >
          <p className="flex items-start gap-2 font-medium">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <span>
              {plural(preview.contasAusentes, 'conta não veio', 'contas não vieram')} nesta planilha
            </span>
          </p>
          <p>
            {preview.contasAusentes === 1
              ? 'Ela continua no plano como está, com o selo "não veio na última importação".'
              : 'Elas continuam no plano como estão, com o selo "não veio na última importação".'}{' '}
            Nenhuma conta é inativada nem apagada pela importação.
          </p>
          {inUse > 0 && (
            <p className="font-medium">
              {absentInUseText(inUse, preview.contasAusentes)} Se a conta não existir mais no
              sistema contábil, o arquivo contábil vai levar uma conta inexistente: confira antes de
              gerar.
            </p>
          )}
        </div>
      )}

      {preview.contasInativasNaPlanilha > 0 && (
        <p className="bg-info-muted text-info ring-info/30 flex items-start gap-2 rounded-lg p-3 text-sm ring-1 ring-inset">
          <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <span>
            {plural(
              preview.contasInativasNaPlanilha,
              'conta inativa veio na planilha e continua inativa',
              'contas inativas vieram na planilha e continuam inativas',
            )}
            . Para voltar a usar, reative pela edição da conta.
          </span>
        </p>
      )}

      {preview.novas.length > 0 && (
        <PreviewList
          title="Contas novas"
          total={preview.contasNovas}
          limit={preview.listLimit}
          items={preview.novas.map((account) => ({
            key: account.code,
            label: accountLine(account.code, account.name),
            detail: account.type === 'sintetica' ? 'sintética' : 'analítica',
          }))}
        />
      )}
      {preview.alteradas.length > 0 && (
        <PreviewList
          title="Contas alteradas"
          total={preview.contasAlteradas}
          limit={preview.listLimit}
          items={preview.alteradas.map((account) => ({
            key: account.accountId,
            label: accountLine(account.code, account.name),
            detail: `muda ${account.changes.map((change) => CHANGE_LABELS[change]).join(', ')}`,
          }))}
        />
      )}
      {preview.ausentes.length > 0 && (
        <PreviewList
          title="Contas que não vieram"
          total={preview.contasAusentes}
          limit={preview.listLimit}
          items={preview.ausentes.map((account) => ({
            key: account.accountId,
            label: accountLine(
              account.code,
              account.nameResolved ? account.name : 'nome indisponível',
            ),
            detail: [
              account.inUse ? 'em uso' : null,
              account.active ? null : 'inativa',
              account.absentSince ? 'já não vinha antes' : null,
            ]
              .filter(Boolean)
              .join(' · '),
            emphasis: account.inUse,
          }))}
        />
      )}
    </section>
  );
}

function PreviewStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-muted/50 rounded-md p-2">
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="text-lg font-semibold tabular-nums">{value}</dd>
    </div>
  );
}

interface PreviewListItem {
  key: string;
  label: string;
  detail: string;
  emphasis?: boolean;
}

function PreviewList({
  title,
  total,
  limit,
  items,
}: {
  title: string;
  total: number;
  limit: number;
  items: PreviewListItem[];
}) {
  return (
    <div className="space-y-1.5">
      <h4 className="text-sm font-medium">
        {title} <span className="text-muted-foreground font-normal tabular-nums">({total})</span>
      </h4>
      <ScrollRegion label={title} className="max-h-48 rounded-md border">
        <ul className="divide-y text-sm">
          {items.map((item) => (
            <li
              key={item.key}
              className="flex flex-wrap items-baseline justify-between gap-x-3 px-3 py-1.5"
            >
              <span className="min-w-0 break-words">{item.label}</span>
              {item.detail !== '' && (
                <span
                  className={
                    item.emphasis
                      ? 'text-warning text-xs font-medium'
                      : 'text-muted-foreground text-xs'
                  }
                >
                  {item.detail}
                </span>
              )}
            </li>
          ))}
        </ul>
      </ScrollRegion>
      {total > limit && (
        <p className="text-muted-foreground text-xs">
          Mostrando as primeiras {limit} de {total}.
        </p>
      )}
    </div>
  );
}
