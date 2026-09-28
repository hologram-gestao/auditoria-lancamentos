/**
 * Campo monetário com máscara — RAW no estado, conversão só na exibição.
 *
 * O estado guarda uma STRING DE CENTAVOS (`"123456"`, `"-1250"`, `""`): nunca
 * `number`, nunca `parseFloat`, nunca `toFixed` no componente. O que a pessoa
 * digita vira dígitos (e o sinal, se houver); o que ela vê é o mesmo valor
 * formatado em pt-BR; o que vai ao servidor é o decimal `1234.56` que o padrão
 * do backend aceita (`^-?\d{1,12}([.,]\d{1,2})?$`). Três funções, uma por
 * borda — é o que impede a conta de acontecer em dois lugares e divergir.
 */

const CENTS_FORMATTER = new Intl.NumberFormat('pt-BR', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** Teto de dígitos inteiros do backend (`\d{1,12}`) mais as 2 casas. */
const MAX_CENTS_DIGITS = 14;

/**
 * Do que foi digitado para centavos crus: fica só o que é dígito, e um `-`
 * quando ele aparece em qualquer posição (a pessoa pode digitá-lo depois do
 * número). Zeros à esquerda saem; `"-"` sozinho é sinal sem valor → `""`.
 */
export function centsFromTyped(raw: string): string {
  const negative = raw.includes('-');
  const digits = raw
    .replace(/\D/g, '')
    .replace(/^0+(?=\d)/, '')
    .slice(0, MAX_CENTS_DIGITS);
  if (digits === '' || /^0+$/.test(digits)) return digits === '' ? '' : '0';
  return negative ? `-${digits}` : digits;
}

/** Centavos crus → texto do campo (`"123456"` → `"1.234,56"`; `""` → `""`). */
export function formatCentsForInput(cents: string): string {
  if (cents === '') return '';
  const negative = cents.startsWith('-');
  const digits = cents.replace(/\D/g, '');
  if (digits === '') return '';
  const value = Number(digits) / 100;
  if (!Number.isFinite(value)) return '';
  return `${negative && value !== 0 ? '-' : ''}${CENTS_FORMATTER.format(value)}`;
}

/**
 * Centavos crus → decimal do contrato (`"123456"` → `"1234.56"`, `"-5"` →
 * `"-0.05"`); `""` → `null` (a pessoa não informou o total). Só aritmética de
 * string: sem `Number`, sem arredondamento.
 */
export function centsToDecimalString(cents: string): string | null {
  if (cents === '') return null;
  const negative = cents.startsWith('-');
  const digits = cents.replace(/\D/g, '');
  if (digits === '') return null;
  const padded = digits.padStart(3, '0');
  const whole = padded.slice(0, -2).replace(/^0+(?=\d)/, '');
  const fraction = padded.slice(-2);
  const isZero = /^0*$/.test(digits);
  return `${negative && !isZero ? '-' : ''}${whole}.${fraction}`;
}
