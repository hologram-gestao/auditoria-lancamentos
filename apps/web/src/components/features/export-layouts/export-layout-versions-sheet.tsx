'use client';

/**
 * Gaveta das VERSÕES de um layout — Sprint 13 (FRONT 13.5 / R1). Só leitura.
 *
 * Versionar é o que deixa um arquivo já gerado explicável pela versão que o
 * gerou, então a gaveta mostra TODAS, da mais nova para a mais antiga, cada uma
 * com os parâmetros legíveis (colunas, separador, cabeçalho, codificação, quebra
 * de linha, data e valor). Sem editar: editor visual está fora do escopo.
 *
 * Presentacional quanto ao alvo: quem abre passa o `layoutId`; o detalhe vem do
 * hook (`GET /export-layouts/{id}`), com os estados de carregando e erro aqui.
 */

import { AuthorLabel } from '@/components/features/reconciliations/author-label';
import { Button } from '@/components/ui/button';
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet';
import { useExportLayout } from '@/hooks/use-export-layouts';
import { ApiError } from '@/lib/api/client';
import type { ExportLayoutVersionItem } from '@/lib/contracts';
import {
  layoutAmountExample,
  layoutCharLabel,
  layoutEncodingLabel,
  layoutFieldLabel,
  layoutLineEndingLabel,
  readLayoutDefinition,
} from '@/lib/export-layout-definition';
import { formatCreatedAt } from '@/lib/format';

interface ExportLayoutVersionsSheetProps {
  open: boolean;
  /**
   * O alvo NÃO é limpo ao fechar (quem abre só troca `open`): enquanto a gaveta
   * sai deslizando, o conteúdo continua sendo o layout que a pessoa olhava.
   */
  layoutId: string | null;
  onOpenChange: (open: boolean) => void;
}

export function ExportLayoutVersionsSheet({
  open,
  layoutId,
  onOpenChange,
}: ExportLayoutVersionsSheetProps) {
  const query = useExportLayout(layoutId);
  const layout = query.data;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="flex w-full flex-col p-0 sm:max-w-lg">
        <SheetHeader>
          <SheetTitle>{layout?.name ?? 'Layout de exportação'}</SheetTitle>
          <SheetDescription>
            {layout
              ? `${layout.targetSystem} · versão atual ${layout.latestVersion}. As versões anteriores ficam guardadas: cada arquivo gerado continua explicável pela versão que o gerou.`
              : 'Versões do layout, da mais nova para a mais antiga.'}
          </SheetDescription>
        </SheetHeader>

        <SheetBody className="space-y-4">
          {query.isLoading ? (
            <div className="space-y-3" aria-busy="true" aria-label="Carregando as versões">
              {Array.from({ length: 2 }).map((_, i) => (
                <div key={i} className="bg-muted h-40 w-full animate-pulse rounded-lg" />
              ))}
            </div>
          ) : query.isError ? (
            <div
              role="alert"
              className="bg-destructive-muted text-destructive ring-destructive/30 flex flex-col items-start gap-3 rounded-lg p-4 text-sm ring-1 ring-inset"
            >
              <span>
                {query.error instanceof ApiError
                  ? query.error.userMessage
                  : 'Não foi possível carregar as versões do layout.'}
              </span>
              <Button variant="outline" size="sm" onClick={() => void query.refetch()}>
                Tentar novamente
              </Button>
            </div>
          ) : (
            <ol className="space-y-4" aria-label="Versões do layout">
              {(layout?.versions ?? []).map((version) => (
                <li key={version.version}>
                  <VersionCard
                    version={version}
                    isLatest={version.version === layout?.latestVersion}
                  />
                </li>
              ))}
            </ol>
          )}
        </SheetBody>

        <SheetFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Fechar
          </Button>
          <span />
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

function VersionCard({
  version,
  isLatest,
}: {
  version: ExportLayoutVersionItem;
  isLatest: boolean;
}) {
  const definition = readLayoutDefinition(version.definition);
  const headingId = `layout-version-${version.version}`;

  return (
    <section aria-labelledby={headingId} className="space-y-3 rounded-lg border p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 id={headingId} className="text-sm font-semibold">
          Versão {version.version}
          {isLatest && (
            <span className="bg-success-muted text-success ml-2 rounded px-1.5 py-0.5 text-xs font-medium">
              atual
            </span>
          )}
        </h3>
        {/* `div`, não `p`: o tooltip do autor monta um `div` (DOM inválido dentro de `p`). */}
        <div className="text-muted-foreground text-xs">
          {formatCreatedAt(version.createdAt)} · <AuthorLabel author={version.author} />
        </div>
      </div>

      {definition === null ? (
        <p className="text-muted-foreground text-sm">
          Não foi possível ler os parâmetros desta versão.
        </p>
      ) : (
        <dl className="grid grid-cols-1 gap-x-4 gap-y-2 text-sm sm:grid-cols-[auto_1fr]">
          <dt className="text-muted-foreground">Colunas</dt>
          <dd>
            <ol className="list-inside list-decimal">
              {definition.columns.map((column, index) => (
                <li key={`${column.field}-${index}`}>{layoutFieldLabel(column.field)}</li>
              ))}
            </ol>
          </dd>
          <dt className="text-muted-foreground">Separador</dt>
          <dd>{layoutCharLabel(definition.separator)}</dd>
          <dt className="text-muted-foreground">Cabeçalho</dt>
          <dd>{definition.hasHeader ? 'Sim, na primeira linha' : 'Não tem'}</dd>
          <dt className="text-muted-foreground">Codificação</dt>
          <dd>{layoutEncodingLabel(definition.encoding)}</dd>
          <dt className="text-muted-foreground">Quebra de linha</dt>
          <dd>{layoutLineEndingLabel(definition.lineEnding)}</dd>
          <dt className="text-muted-foreground">Data</dt>
          <dd>{definition.dateFormat}</dd>
          <dt className="text-muted-foreground">Valor</dt>
          <dd>
            <span className="whitespace-nowrap font-mono">
              {layoutAmountExample(definition.amountFormat)}
            </span>
            <span className="text-muted-foreground block text-xs">
              Sempre absoluto (o sinal vira a partida). Milhar:{' '}
              {layoutCharLabel(definition.amountFormat.thousandsSeparator)}; decimal:{' '}
              {layoutCharLabel(definition.amountFormat.decimalSeparator)};{' '}
              {definition.amountFormat.decimalPlaces} casas.
            </span>
          </dd>
        </dl>
      )}
    </section>
  );
}
