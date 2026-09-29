'use client';

/**
 * As recusas da importação do plano contábil, cada uma com o motivo
 * ESPECÍFICO (Sprint 16 — FRONT 16.5 / R1). Nunca um toast genérico.
 *
 * Ramificado por `code` (`lib/accounting-chart-errors.ts`), no padrão do
 * `FileRefusalNotice` da S14 (ADR-047-FE):
 *   - `CABECALHO_DIVERGENTE` → colunas que FALTAM, que SOBRAM e que se REPETEM,
 *     nomeadas, e as encontradas — contra o modelo documentado logo acima;
 *   - `LINHAS_INVALIDAS` → tabela linha × motivo em português e "mostrando K de
 *     N" quando o servidor recortou;
 *   - `FORMATO_NAO_SUPORTADO` / `ARQUIVO_INVALIDO` → o `userMessage` tipado do
 *     servidor, com a instrução de "planilha sem nenhuma conta" quando for o caso.
 * Código que não é nenhum desses devolve `null` (o caller já foi ao toast).
 *
 * Sem conteúdo de célula: o servidor manda nomes de coluna (estrutura), número
 * de linha e motivo fechado — e é só isso que aparece.
 */

import { AlertTriangle } from 'lucide-react';

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
  accountingLineReasonLabel,
  readAccountingChartRefusal,
  type AccountingChartRefusal,
} from '@/lib/accounting-chart-errors';

const TITLES: Record<AccountingChartRefusal['code'], string> = {
  FORMATO_NAO_SUPORTADO: 'Formato de arquivo não suportado',
  ARQUIVO_INVALIDO: 'Não foi possível ler a planilha',
  CABECALHO_DIVERGENTE: 'O cabeçalho da planilha não segue o modelo',
  LINHAS_INVALIDAS: 'A planilha tem linhas inválidas — nada foi importado',
};

export function AccountingChartRefusalNotice({
  error,
}: {
  error: unknown;
}): React.ReactElement | null {
  const refusal = readAccountingChartRefusal(error);
  if (refusal === null) return null;
  return (
    <div
      role="alert"
      data-refusal-code={refusal.code}
      className="bg-destructive-muted text-destructive ring-destructive/30 space-y-3 rounded-lg p-4 text-sm ring-1 ring-inset"
    >
      <p className="flex items-start gap-2 font-medium">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <span>{TITLES[refusal.code]}</span>
      </p>
      <RefusalBody refusal={refusal} />
    </div>
  );
}

function RefusalBody({ refusal }: { refusal: AccountingChartRefusal }) {
  switch (refusal.code) {
    case 'CABECALHO_DIVERGENTE':
      return (
        <div className="space-y-3">
          <p>{refusal.userMessage}</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <ColumnList
              heading="Colunas obrigatórias que faltam"
              columns={refusal.missingColumns}
              emphasized
            />
            <ColumnList
              heading="Colunas fora do modelo"
              columns={refusal.unexpectedColumns}
              emphasized
            />
            <ColumnList heading="Colunas repetidas" columns={refusal.repeatedColumns} emphasized />
            <ColumnList heading="Colunas encontradas na planilha" columns={refusal.foundColumns} />
          </div>
        </div>
      );
    case 'LINHAS_INVALIDAS':
      return (
        <div className="space-y-3">
          <p>{refusal.userMessage}</p>
          <TableCard className="bg-background text-foreground max-h-72">
            <Table fill scrollRegionLabel="Linhas inválidas da planilha (rolável)">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-20 whitespace-nowrap">Linha</TableHead>
                  <TableHead>Motivo</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {refusal.lines.map((item, index) => (
                  <TableRow key={`${item.line}:${index}`}>
                    <TableCell className="tabular-nums">{item.line}</TableCell>
                    <TableCell className="whitespace-normal">
                      {accountingLineReasonLabel(item.reason)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableCard>
          <p className="text-xs" data-testid="accounting-invalid-lines-count">
            {refusal.total > refusal.lines.length
              ? `Mostrando ${refusal.lines.length} de ${refusal.total} linhas inválidas.`
              : `${refusal.total} ${refusal.total === 1 ? 'linha inválida' : 'linhas inválidas'}.`}{' '}
            A linha 1 é o cabeçalho.
          </p>
        </div>
      );
    case 'ARQUIVO_INVALIDO':
      return (
        <p>
          {refusal.noAccounts
            ? 'A planilha abriu, mas não tem nenhuma conta abaixo do cabeçalho. Confira se exportou o plano inteiro.'
            : refusal.userMessage}
        </p>
      );
    case 'FORMATO_NAO_SUPORTADO':
      // A mensagem TIPADA do servidor é a instrução (o que enviar).
      return <p>{refusal.userMessage}</p>;
  }
}

function ColumnList({
  heading,
  columns,
  emphasized = false,
}: {
  heading: string;
  columns: string[];
  emphasized?: boolean;
}) {
  // Lista vazia não aparece: "Colunas repetidas: —" numa recusa por coluna
  // faltante só atrapalha a leitura do que de fato está errado.
  if (columns.length === 0) return null;
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium">{heading}</p>
      <ul aria-label={heading} className="flex flex-wrap gap-1.5">
        {/* O backend devolve as colunas CRUAS: na recusa por coluna repetida o
            nome se repete, então a chave leva a posição. */}
        {columns.map((column, index) => (
          <li
            key={`${index}:${column}`}
            className={
              emphasized
                ? 'bg-background text-destructive ring-destructive/30 rounded px-2 py-0.5 font-mono text-xs font-semibold ring-1 ring-inset'
                : 'bg-background text-foreground rounded px-2 py-0.5 font-mono text-xs'
            }
          >
            {column}
          </li>
        ))}
      </ul>
    </div>
  );
}
