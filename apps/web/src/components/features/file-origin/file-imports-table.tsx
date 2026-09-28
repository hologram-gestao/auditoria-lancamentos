'use client';

/**
 * Lista "Arquivos processados" da aba Origem por arquivo (Sprint 14 — FRONT
 * 14.6 / R3): competência, linhas que viraram movimento, quando e quem — o
 * autor já MASCARADO pelo servidor (usuário de tenant lê "Equipe {org}").
 *
 * Tabela de altura natural: esta aba tem três seções e quem rola é o `<main>`
 * do shell (não é a tela em que a tabela enche a janela). Estado vazio em
 * `<TableEmpty>` DEPOIS do `<Table>`, dentro do mesmo `<TableCard>` — nunca
 * numa célula `colSpan`, que em 390px fica com a metade direita fora da tela.
 */

import { AuthorLabel } from '@/components/features/reconciliations/author-label';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCard,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { useFileImports } from '@/hooks/use-client-file-origin';
import { ApiError } from '@/lib/api/client';
import { formatCreatedAt, formatReferenceMonth } from '@/lib/format';

/** Âncora da seção — a recusa `ARQUIVO_JA_PROCESSADO` aponta para cá. */
export const FILE_IMPORTS_ANCHOR = 'arquivos-processados';

export function FileImportsTable({ clientId }: { clientId: string }) {
  const query = useFileImports(clientId, null);
  const rows = query.data ?? [];

  return (
    <section
      id={FILE_IMPORTS_ANCHOR}
      aria-labelledby="file-imports-heading"
      className="space-y-3"
      data-testid="file-imports"
    >
      <div className="space-y-1">
        <h3 id="file-imports-heading" className="text-base font-semibold">
          Arquivos processados
        </h3>
        <p className="text-muted-foreground text-sm">
          Cada envio que entrou inteiro. Um arquivo corrigido na mesma competência substitui as
          linhas do anterior; o mesmo arquivo de novo é recusado.
        </p>
      </div>

      {query.isError ? (
        <div
          role="alert"
          className="bg-destructive-muted text-destructive ring-destructive/30 flex flex-col items-start gap-3 rounded-lg p-4 text-sm ring-1 ring-inset sm:flex-row sm:items-center sm:justify-between"
        >
          <span>
            {query.error instanceof ApiError
              ? query.error.userMessage
              : 'Não foi possível carregar os arquivos processados.'}
          </span>
          <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
            Tentar novamente
          </Button>
        </div>
      ) : (
        <TableCard aria-busy={query.isFetching}>
          <Table scrollRegionLabel="Arquivos processados deste cliente (rolável)">
            <TableHeader>
              <TableRow>
                <TableHead className="whitespace-nowrap">Competência</TableHead>
                <TableHead className="whitespace-nowrap text-right">Linhas</TableHead>
                <TableHead className="whitespace-nowrap">Processado em</TableHead>
                <TableHead className="whitespace-nowrap">Por</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {query.isLoading ? (
                <SkeletonRows />
              ) : (
                rows.map((item) => (
                  <TableRow key={item.id}>
                    <TableCell className="whitespace-nowrap font-medium">
                      {formatReferenceMonth(item.competence)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-right tabular-nums">
                      {item.rows}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      {formatCreatedAt(item.processedAt)}
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      <AuthorLabel author={item.author} />
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
          {!query.isLoading && rows.length === 0 && (
            <TableEmpty className="text-center">
              <p className="text-sm font-medium">Nenhum arquivo processado ainda</p>
              <p className="text-muted-foreground text-sm">
                O primeiro envio que entrar inteiro aparece aqui, com a competência e quem enviou.
              </p>
            </TableEmpty>
          )}
        </TableCard>
      )}
    </section>
  );
}

function SkeletonRows() {
  return (
    <>
      {Array.from({ length: 2 }).map((_, index) => (
        <TableRow key={index} aria-hidden="true">
          {Array.from({ length: 4 }).map((__, cell) => (
            <TableCell key={cell}>
              <div className="bg-muted h-4 w-full animate-pulse rounded" />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  );
}
