'use client';

/**
 * As recusas do arquivo, cada uma com o motivo ESPECÍFICO e a ação de
 * correção (Sprint 14 — FRONT 14.6 / R2 · R5). Nunca um toast genérico.
 *
 * Um componente, ramificado por `code` (`lib/file-origin-errors.ts`):
 *   - `CABECALHO_DIVERGENTE` → as colunas AUSENTES nomeadas, as encontradas, e
 *     "Revisar mapeamento" (só para quem configura);
 *   - `LINHAS_INVALIDAS` → tabela linha × motivo (rótulos PT-BR do vocabulário
 *     fechado) e "mostrando K de N" quando o servidor recortou;
 *   - `TOTAL_DIVERGENTE` → os dois totais lado a lado, em `formatBRL`;
 *   - `FORMATO_NAO_SUPORTADO` / `ARQUIVO_INVALIDO` → o `userMessage` tipado do
 *     servidor (é ele que diz o que enviar);
 *   - `ARQUIVO_JA_PROCESSADO` → aponta a lista de processados;
 *   - `SEM_MAPEAMENTO` / `SINAL_NAO_DECLARADO` → leva ao editor de mapeamento;
 *   - os 409 da taxonomia de origem (S9) → o `OriginStateBlock` de sempre.
 * Código que não é nenhum desses devolve `null` — o caller cai no toast.
 *
 * Fundo destrutivo é `destructive-muted`, nunca `bg-destructive/N`
 * (86e3dxund): sobre `-muted` o texto é o sólido. `role="alert"`: é uma
 * recusa, não uma etapa de configuração. Nenhum conteúdo de célula: o servidor
 * manda nomes de coluna (estrutura), números de linha e motivos fechados — e é
 * só isso que aparece.
 */

import { AlertTriangle, ListChecks, Settings2 } from 'lucide-react';

import { OriginStateNotice } from '@/components/shared/origin-state-notice';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCard,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { lineReasonLabel, readFileRefusal, type FileRefusal } from '@/lib/file-origin-errors';
import { formatBRL } from '@/lib/format';

/** `accept` do input de arquivo — CSV e XLSX; PDF é recusado pelo servidor com motivo. */
export const FILE_ACCEPT =
  '.csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';

export function isCsvFileName(name: string): boolean {
  return name.toLowerCase().endsWith('.csv');
}

interface FileRefusalNoticeProps {
  /** O `error` cru da mutation. */
  error: unknown;
  clientId: string;
  /** Abre o editor de mapeamento — só passa quem tem `manage_input_mapping`. */
  onReviewMapping?: () => void;
  /** `href` da lista de processados (âncora na mesma tela). */
  importsHref?: string;
}

export function FileRefusalNotice({
  error,
  clientId,
  onReviewMapping,
  importsHref,
}: FileRefusalNoticeProps): React.ReactElement | null {
  const refusal = readFileRefusal(error);
  if (refusal === null) {
    // Os três 409 da origem (S9) — conexão `arquivo` ausente/inativa.
    return <OriginStateNotice error={error} clientId={clientId} />;
  }
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
      <RefusalBody refusal={refusal} onReviewMapping={onReviewMapping} importsHref={importsHref} />
    </div>
  );
}

const TITLES: Record<FileRefusal['code'], string> = {
  SEM_MAPEAMENTO: 'Este cliente ainda não tem mapeamento de colunas',
  SINAL_NAO_DECLARADO: 'O mapeamento não declara como o arquivo indica débito e crédito',
  FORMATO_NAO_SUPORTADO: 'Formato de arquivo não suportado',
  ARQUIVO_INVALIDO: 'Não foi possível ler o arquivo',
  ARQUIVO_JA_PROCESSADO: 'Este arquivo já foi processado nesta competência',
  CABECALHO_DIVERGENTE: 'O cabeçalho do arquivo não bate com o mapeamento',
  LINHAS_INVALIDAS: 'O arquivo tem linhas inválidas — nada foi processado',
  TOTAL_DIVERGENTE: 'O total informado não confere com o arquivo — nada foi processado',
};

function RefusalBody({
  refusal,
  onReviewMapping,
  importsHref,
}: {
  refusal: FileRefusal;
  onReviewMapping?: () => void;
  importsHref?: string;
}) {
  switch (refusal.code) {
    case 'CABECALHO_DIVERGENTE':
      return (
        <div className="space-y-3">
          <p>{refusal.userMessage}</p>
          <ColumnList
            heading="Colunas do mapeamento ausentes no arquivo"
            columns={refusal.missingColumns}
            emphasized
          />
          {refusal.foundColumnCount > 0 && (
            <p className="text-xs" data-testid="file-header-counts">
              {`O arquivo tem ${refusal.foundColumnCount} ${
                refusal.foundColumnCount === 1 ? 'coluna' : 'colunas'
              }. Confira se a linha 1 é o cabeçalho e não o primeiro lançamento.`}
            </p>
          )}
          {onReviewMapping && <ReviewMappingButton onClick={onReviewMapping} />}
        </div>
      );
    case 'LINHAS_INVALIDAS':
      return (
        <div className="space-y-3">
          <p>{refusal.userMessage}</p>
          <TableCard className="bg-background text-foreground max-h-72">
            <Table fill scrollRegionLabel="Linhas inválidas do arquivo (rolável)">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-24 whitespace-nowrap">Linha</TableHead>
                  <TableHead>Motivo</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {refusal.lines.map((item, index) => (
                  <TableRow key={`${item.line}:${index}`}>
                    <TableCell className="tabular-nums">{item.line}</TableCell>
                    <TableCell>{lineReasonLabel(item.reason)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableCard>
          <p className="text-xs" data-testid="invalid-lines-count">
            {refusal.total > refusal.lines.length
              ? `Mostrando ${refusal.lines.length} de ${refusal.total} linhas inválidas.`
              : `${refusal.total} ${refusal.total === 1 ? 'linha inválida' : 'linhas inválidas'}.`}
          </p>
        </div>
      );
    case 'TOTAL_DIVERGENTE':
      return (
        <div className="space-y-3">
          <p>{refusal.userMessage}</p>
          <dl className="grid grid-cols-2 gap-3" data-testid="total-comparison">
            <div className="bg-background text-foreground rounded-md p-3">
              <dt className="text-muted-foreground text-xs">Total informado</dt>
              <dd className="whitespace-nowrap text-lg font-semibold tabular-nums">
                {refusal.declaredTotal === null ? '—' : formatBRL(refusal.declaredTotal)}
              </dd>
            </div>
            <div className="bg-background text-foreground rounded-md p-3">
              <dt className="text-muted-foreground text-xs">Soma do arquivo</dt>
              <dd className="whitespace-nowrap text-lg font-semibold tabular-nums">
                {refusal.computedTotal === null ? '—' : formatBRL(refusal.computedTotal)}
              </dd>
            </div>
          </dl>
        </div>
      );
    case 'ARQUIVO_JA_PROCESSADO':
      return (
        <div className="space-y-3">
          <p>{refusal.userMessage}</p>
          {importsHref && (
            <Button asChild variant="outline" size="sm">
              <a href={importsHref}>
                <ListChecks className="h-4 w-4" aria-hidden="true" />
                Ver arquivos processados
              </a>
            </Button>
          )}
        </div>
      );
    case 'SEM_MAPEAMENTO':
    case 'SINAL_NAO_DECLARADO':
      return (
        <div className="space-y-3">
          <p>{refusal.userMessage}</p>
          {onReviewMapping ? (
            <ReviewMappingButton
              onClick={onReviewMapping}
              label={
                refusal.code === 'SEM_MAPEAMENTO' ? 'Configurar mapeamento' : 'Completar mapeamento'
              }
            />
          ) : (
            <p className="text-xs">
              Peça a alguém da equipe com acesso de configuração para ajustar o mapeamento.
            </p>
          )}
        </div>
      );
    case 'FORMATO_NAO_SUPORTADO':
    case 'ARQUIVO_INVALIDO':
      // A mensagem TIPADA do servidor é a instrução (o que enviar, o que conferir).
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
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium">{heading}</p>
      {columns.length === 0 ? (
        <p className="text-xs">—</p>
      ) : (
        <ul aria-label={heading} className="flex flex-wrap gap-1.5">
          {columns.map((column) => (
            <li
              key={column}
              className={
                emphasized
                  ? 'bg-background text-destructive ring-destructive/30 rounded px-2 py-0.5 text-xs font-semibold ring-1 ring-inset'
                  : 'bg-background text-foreground rounded px-2 py-0.5 text-xs'
              }
            >
              {column}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ReviewMappingButton({
  onClick,
  label = 'Revisar mapeamento',
}: {
  onClick: () => void;
  label?: string;
}) {
  return (
    <Button type="button" variant="outline" size="sm" onClick={onClick}>
      <Settings2 className="h-4 w-4" aria-hidden="true" />
      {label}
    </Button>
  );
}
