/**
 * Resumo legível do MAPEAMENTO DE ENTRADA (Sprint 14 — FRONT 14.5 / R5):
 * campo ← coluna, formato do arquivo, formato de data, separador decimal e a
 * convenção de sinal. É o que o operador lê antes de enviar ("será aplicado"),
 * e o que quem configura confere antes de alterar.
 *
 * Só renderiza — server component por padrão. Os nomes de coluna são ESTRUTURA
 * do arquivo (o servidor os guarda em claro), nunca conteúdo de célula.
 */
import type { InputMapping } from '@/lib/contracts';
import {
  CATEGORY_MODE_LABELS,
  CSV_DELIMITER_LABELS,
  DATE_FORMAT_LABELS,
  DECIMAL_SEPARATOR_LABELS,
  ENCODING_LABELS,
  FILE_FORMAT_LABELS,
  MAPPING_FIELD_LABELS,
  mappedColumns,
  SIGN_CONVENTION_LABELS,
} from '@/lib/input-mapping';
import { cn } from '@/lib/utils';

interface MappingSummaryProps {
  mapping: InputMapping;
  /**
   * `compact` é o card "Será aplicado" do envio: só campo ← coluna e a
   * convenção, numa linha por par. O completo acrescenta formato, data,
   * separador e o modo de categoria.
   */
  compact?: boolean;
  className?: string;
}

export function MappingSummary({ mapping, compact = false, className }: MappingSummaryProps) {
  const columns = mappedColumns(mapping);
  const signLabel = SIGN_CONVENTION_LABELS[mapping.signConvention];
  const natureDetail =
    mapping.signConvention === 'coluna_natureza' && mapping.debitValue && mapping.creditValue
      ? ` — débito "${mapping.debitValue}", crédito "${mapping.creditValue}"`
      : '';

  return (
    <div
      className={cn('space-y-3 text-sm', className)}
      // A tela mostra os dois de uma vez (seção + card "Será aplicado"): ids
      // distintos para o teste e o e2e apontarem para o certo.
      data-testid={compact ? 'mapping-summary-compact' : 'mapping-summary'}
    >
      {/* Uma lista de definições por par: a coluna do ARQUIVO à direita, sem
          ambiguidade sobre qual lado é o campo do sistema. */}
      <dl
        className={cn('grid gap-x-6 gap-y-1.5', compact ? 'sm:grid-cols-2' : 'sm:grid-cols-3')}
        aria-label="Campos mapeados"
      >
        {columns.map(({ field, column }) => (
          <div key={field} className="flex min-w-0 items-baseline gap-2">
            <dt className="text-muted-foreground shrink-0">{MAPPING_FIELD_LABELS[field]}</dt>
            <dd className="min-w-0 truncate font-medium">
              <span aria-hidden="true">← </span>
              {column}
            </dd>
          </div>
        ))}
      </dl>

      <dl className="text-muted-foreground grid gap-x-6 gap-y-1 sm:grid-cols-2">
        <div className="flex items-baseline gap-2">
          <dt className="shrink-0">Sinal</dt>
          <dd className="text-foreground">
            {signLabel}
            {natureDetail}
          </dd>
        </div>
        {!compact && (
          <>
            <div className="flex items-baseline gap-2">
              <dt className="shrink-0">Formato</dt>
              <dd className="text-foreground">
                {FILE_FORMAT_LABELS[mapping.fileFormat]}
                {mapping.fileFormat === 'csv' && mapping.csvDelimiter && mapping.encoding
                  ? ` · ${CSV_DELIMITER_LABELS[mapping.csvDelimiter]} · ${ENCODING_LABELS[mapping.encoding]}`
                  : ''}
              </dd>
            </div>
            <div className="flex items-baseline gap-2">
              <dt className="shrink-0">Data</dt>
              <dd className="text-foreground">{DATE_FORMAT_LABELS[mapping.dateFormat]}</dd>
            </div>
            <div className="flex items-baseline gap-2">
              <dt className="shrink-0">Decimal</dt>
              <dd className="text-foreground">
                {DECIMAL_SEPARATOR_LABELS[mapping.decimalSeparator]}
              </dd>
            </div>
            {mapping.categoryColumn && (
              <div className="flex items-baseline gap-2">
                <dt className="shrink-0">Categoria</dt>
                <dd className="text-foreground">{CATEGORY_MODE_LABELS[mapping.categoryMode]}</dd>
              </div>
            )}
          </>
        )}
      </dl>
    </div>
  );
}
