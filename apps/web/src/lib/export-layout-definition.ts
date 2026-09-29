/**
 * Leitura e rótulos da DEFINIÇÃO de um layout de exportação — Sprint 13.
 *
 * Na leitura o contrato entrega a definição como `{ [key]: unknown }`
 * (`ExportLayoutVersionItem.definition`, "mesma forma do pedido"). Em vez de
 * `as LayoutDefinitionPayload`, este leitor ESTREITA campo a campo: uma chave
 * que o backend renomeie vira "definição ilegível" na tela, nunca um
 * `undefined` impresso como parâmetro.
 *
 * Os rótulos existem porque a pessoa que confere o layout é o contador, não
 * quem escreveu o JSON: `;` sozinho numa célula é fácil de não ver, `crlf` não
 * diz nada.
 */
import type {
  AmountFormatPayload,
  LayoutColumnPayload,
  LayoutDefinitionPayload,
} from '@/lib/contracts';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function readColumn(value: unknown): LayoutColumnPayload | null {
  if (!isRecord(value) || typeof value.field !== 'string') return null;
  const header = value.header;
  return { field: value.field, header: typeof header === 'string' ? header : null };
}

function readAmountFormat(value: unknown): AmountFormatPayload | null {
  if (!isRecord(value)) return null;
  const { prefix, thousandsSeparator, decimalSeparator, decimalPlaces } = value;
  if (
    typeof prefix !== 'string' ||
    typeof thousandsSeparator !== 'string' ||
    typeof decimalSeparator !== 'string' ||
    typeof decimalPlaces !== 'number'
  ) {
    return null;
  }
  return { prefix, thousandsSeparator, decimalSeparator, decimalPlaces };
}

/** `null` quando a definição não tem a forma esperada. */
export function readLayoutDefinition(raw: unknown): LayoutDefinitionPayload | null {
  if (!isRecord(raw) || !Array.isArray(raw.columns)) return null;
  const columns = raw.columns.map(readColumn);
  const amountFormat = readAmountFormat(raw.amountFormat);
  const { separator, hasHeader, encoding, lineEnding, dateFormat } = raw;
  if (
    columns.some((c) => c === null) ||
    amountFormat === null ||
    typeof separator !== 'string' ||
    typeof hasHeader !== 'boolean' ||
    typeof encoding !== 'string' ||
    typeof lineEnding !== 'string' ||
    typeof dateFormat !== 'string'
  ) {
    return null;
  }
  return {
    columns: columns.filter((c): c is LayoutColumnPayload => c !== null),
    separator,
    hasHeader,
    encoding,
    lineEnding,
    dateFormat,
    amountFormat,
  };
}

/** O vocabulário FECHADO de campos do backend (`LayoutField`), em PT-BR. */
const FIELD_LABELS: Record<string, string> = {
  data: 'Data',
  conta_debito: 'Conta débito',
  conta_credito: 'Conta crédito',
  valor: 'Valor',
  historico: 'Histórico',
  competencia: 'Competência',
  codigo_categoria_origem: 'Código da categoria na origem',
};

/** Campo fora do vocabulário conhecido aparece cru — nunca some da lista. */
export function layoutFieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field;
}

const CHAR_NAMES: Record<string, string> = {
  ';': 'ponto e vírgula',
  ',': 'vírgula',
  '.': 'ponto',
  '|': 'barra vertical',
  '\t': 'tabulação',
  ' ': 'espaço',
};

/** `;` → `ponto e vírgula (;)`. Vazio = "nenhum" (milhar sem separador). */
export function layoutCharLabel(char: string): string {
  if (char === '') return 'nenhum';
  // Tabulação e espaço não se enxergam entre parênteses: só o nome.
  if (char === '\t' || char === ' ') return CHAR_NAMES[char] ?? char;
  const name = CHAR_NAMES[char];
  return name ? `${name} (${char})` : `«${char}»`;
}

export function layoutLineEndingLabel(lineEnding: string): string {
  switch (lineEnding) {
    case 'crlf':
      return 'Windows (CRLF), inclusive depois da última linha';
    case 'lf':
      return 'Unix (LF), inclusive depois da última linha';
    default:
      return lineEnding;
  }
}

export function layoutEncodingLabel(encoding: string): string {
  const normalized = encoding.toLowerCase();
  if (['latin-1', 'latin1', 'iso-8859-1', 'iso8859-1'].includes(normalized)) {
    return 'Latin-1 (ISO-8859-1)';
  }
  if (['utf-8', 'utf8'].includes(normalized)) return 'UTF-8';
  return encoding;
}

/**
 * Um exemplo de valor no formato do layout (1234,5 → `R$ 1.234,50`). Montado a
 * partir dos parâmetros, sem `Intl`: o que se quer mostrar é exatamente o que o
 * gerador escreveria, com os separadores DO LAYOUT.
 */
export function layoutAmountExample(format: AmountFormatPayload): string {
  const places = Math.max(0, format.decimalPlaces);
  const decimals = '5'.padEnd(places, '0').slice(0, places);
  const integer = `1${format.thousandsSeparator}234`;
  return places > 0
    ? `${format.prefix}${integer}${format.decimalSeparator}${decimals}`
    : `${format.prefix}${integer}`;
}
