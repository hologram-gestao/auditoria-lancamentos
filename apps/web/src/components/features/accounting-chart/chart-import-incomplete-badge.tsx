'use client';

/**
 * O selo "Plano não está 100%" da tela do plano contábil (86e3ydjze, épico 86e3ydjy9).
 *
 * Desde 03/11/2026 uma importação pode entrar SEM as linhas com problema, por
 * confirmação explícita na prévia (perda nunca silenciosa). Enquanto a ÚLTIMA importação
 * do cliente tiver linhas de fora, este selo vermelho fica ao lado do título: passando o
 * mouse (ou com o foco do teclado) o tooltip diz quando e quantas; clicando, um diálogo
 * lista linha × motivo. Importação seguinte sem linha de fora faz o selo sumir (a última
 * importação é a fonte, lida do servidor em `ultimaImportacao`).
 *
 * Cor só por token OPACO (`destructive-muted`, nunca `bg-destructive/N`, front-gate §4);
 * tooltip pelo componente da casa, nunca `title` nativo. O nome acessível COMEÇA pelo
 * texto visível (WCAG 2.5.3).
 */

import { AlertTriangle } from 'lucide-react';
import { useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Table,
  TableBody,
  TableCard,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { accountingLineReasonLabel } from '@/lib/accounting-chart-errors';
import type { AccountingChartLastImport } from '@/lib/contracts';
import { formatCreatedAt } from '@/lib/format';

export const INCOMPLETE_BADGE_TEXT = 'Plano não está 100%';

function linesText(n: number): string {
  return n === 1 ? '1 linha ficou de fora' : `${n} linhas ficaram de fora`;
}

export function ChartImportIncompleteBadge({
  lastImport,
}: {
  lastImport: AccountingChartLastImport | null | undefined;
}) {
  const [open, setOpen] = useState(false);
  const lines = lastImport?.linhasExcluidas ?? [];
  if (!lastImport || lines.length === 0) return null;

  const when = formatCreatedAt(lastImport.importadaEm);
  const explanation = `O plano importado em ${when} não está 100%: ${linesText(lines.length)}.`;

  return (
    <>
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              data-testid="accounting-chart-incomplete-badge"
              aria-label={`${INCOMPLETE_BADGE_TEXT}: ${explanation} Ver as linhas.`}
              onClick={() => setOpen(true)}
              className="bg-destructive-muted text-destructive ring-destructive/40 hover:ring-destructive focus-visible:ring-ring inline-flex cursor-pointer items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold ring-1 ring-inset focus-visible:outline-none focus-visible:ring-2"
            >
              <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
              {INCOMPLETE_BADGE_TEXT}
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-xs text-xs leading-snug">
            {explanation} Clique para ver quais.
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>O plano importado não está 100%</DialogTitle>
            <DialogDescription>
              {explanation} Elas não entraram no plano contábil. Corrija o arquivo e importe de novo
              para tirar este aviso.
            </DialogDescription>
          </DialogHeader>
          <TableCard className="max-h-72">
            <Table fill scrollRegionLabel="Linhas que ficaram de fora (rolável)">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-20 whitespace-nowrap">Linha</TableHead>
                  <TableHead>Motivo</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {lines.map((item) => (
                  <TableRow key={item.linha}>
                    <TableCell className="tabular-nums">{item.linha}</TableCell>
                    <TableCell className="whitespace-normal">
                      {accountingLineReasonLabel(item.motivo)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableCard>
          <p className="text-muted-foreground text-xs">
            Os números são os das linhas da planilha, como aparecem no Excel.
          </p>
          <DialogFooter>
            <Button type="button" onClick={() => setOpen(false)}>
              Fechar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
