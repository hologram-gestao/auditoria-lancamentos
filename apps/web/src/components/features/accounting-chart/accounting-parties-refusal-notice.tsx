'use client';

/**
 * As recusas da importação das relações de clientes e fornecedores do Domínio
 * (86e3qhhrn), cada uma com o motivo ESPECÍFICO. Nunca um toast genérico.
 *
 * Ramificado por `code` (`lib/accounting-parties-errors.ts`), no molde do
 * `AccountingChartRefusalNotice` (ADR-047-FE):
 *   - `LINHAS_INVALIDAS` → tabela linha x conta x motivo em português (a conta é o
 *     código reduzido, o que a pessoa procura no plano para corrigir);
 *   - `RELATORIO_DE_OUTRA_EMPRESA`, `CABECALHO_DIVERGENTE`, `FORMATO_NAO_SUPORTADO` e
 *     `ARQUIVO_INVALIDO` → o `userMessage` tipado do servidor, que já é a instrução.
 * Código que não é nenhum desses devolve `null` (o caller já foi ao toast).
 *
 * Sem conteúdo de célula: documento, nome e razão social nunca saem do servidor.
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
  partyLineReasonLabel,
  readAccountingPartiesRefusal,
  type AccountingPartiesRefusal,
} from '@/lib/accounting-parties-errors';

const TITLES: Record<AccountingPartiesRefusal['code'], string> = {
  FORMATO_NAO_SUPORTADO: 'Formato de arquivo não suportado',
  ARQUIVO_INVALIDO: 'Não foi possível ler o relatório',
  CABECALHO_DIVERGENTE: 'O arquivo não é a relação de clientes nem a de fornecedores',
  LINHAS_INVALIDAS: 'O relatório tem linhas que impedem a importação — nenhum vínculo foi gravado',
  RELATORIO_DE_OUTRA_EMPRESA: 'Este relatório é de outra empresa',
};

export function AccountingPartiesRefusalNotice({
  error,
}: {
  error: unknown;
}): React.ReactElement | null {
  const refusal = readAccountingPartiesRefusal(error);
  if (refusal === null) return null;
  return (
    <div
      role="alert"
      data-refusal-code={refusal.code}
      data-testid="accounting-parties-refusal"
      className="bg-destructive-muted text-destructive ring-destructive/30 space-y-3 rounded-lg p-4 text-sm ring-1 ring-inset"
    >
      <p className="flex items-start gap-2 font-medium">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
        <span>{TITLES[refusal.code]}</span>
      </p>
      {refusal.code === 'LINHAS_INVALIDAS' ? (
        <div className="space-y-3">
          <p>{refusal.userMessage}</p>
          <TableCard className="bg-background text-foreground max-h-72">
            <Table fill scrollRegionLabel="Linhas que impedem a importação (rolável)">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-20 whitespace-nowrap">Linha</TableHead>
                  <TableHead className="w-24 whitespace-nowrap">Conta</TableHead>
                  <TableHead>Motivo</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {refusal.lines.map((item, index) => (
                  <TableRow key={`${item.line}:${index}`}>
                    <TableCell className="tabular-nums">{item.line}</TableCell>
                    <TableCell className="tabular-nums">{item.accountCode ?? '—'}</TableCell>
                    <TableCell className="whitespace-normal">
                      {partyLineReasonLabel(item.reason)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableCard>
          <p className="text-xs" data-testid="accounting-parties-invalid-lines-count">
            {refusal.total > refusal.lines.length
              ? `Mostrando ${refusal.lines.length} de ${refusal.total} linhas.`
              : `${refusal.total} ${refusal.total === 1 ? 'linha' : 'linhas'}.`}{' '}
            Os números são os das linhas do relatório, como aparecem no Excel.
          </p>
        </div>
      ) : (
        <p>{refusal.userMessage}</p>
      )}
    </div>
  );
}
