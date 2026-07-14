/**
 * Formatadores PT-BR de uso geral (moeda, datas curtas, mapeamentos de domínio).
 *
 * Decisões:
 *   - `formatBRL` aceita `string | number` porque os valores monetários do
 *     back vêm como string (Decimal serializado pelo Pydantic v2). Parsing
 *     via `Number()` preserva precisão suficiente pra exibição (até 13
 *     dígitos significativos cobrem qualquer valor BRL realista).
 *   - `formatBRDate` faz parse manual de `YYYY-MM-DD` em vez de `new Date(iso)`.
 *     `new Date('2026-04-01')` é tratado como UTC pelo JS engine — em
 *     fusos a oeste de Greenwich (Brasil), volta para `2026-03-31` quando
 *     formatado localmente. Same precedente de `reconciliation-card.tsx` (S7).
 *   - `formatAccountType` mapeia o `Literal['checking','credit_card']` do
 *     back para PT-BR. Exhaustive switch — se um terceiro tipo aparecer, o
 *     compilador acusa via `never`.
 */

const BRL_FORMATTER = new Intl.NumberFormat('pt-BR', {
  style: 'currency',
  currency: 'BRL',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

export interface FormatBRLOptions {
  /** Quando true, prefixa `+` em valores positivos (negativo já vem com `-`). */
  signed?: boolean;
}

/**
 * Formata um valor monetário (string Decimal ou number) em BRL.
 *
 * Exemplos:
 *   formatBRL("1234.56")               → "R$ 1.234,56"
 *   formatBRL(-50)                     → "-R$ 50,00"
 *   formatBRL("1234.56", {signed:true})→ "+R$ 1.234,56"
 *   formatBRL(0, {signed:true})        → "R$ 0,00"  (zero não recebe sinal)
 *   formatBRL("abc")                   → "R$ —"     (fallback defensivo)
 */
export function formatBRL(value: string | number, opts: FormatBRLOptions = {}): string {
  const num = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(num)) {
    return 'R$ —';
  }
  const formatted = BRL_FORMATTER.format(num);
  if (opts.signed && num > 0) {
    return `+${formatted}`;
  }
  return formatted;
}

/**
 * Converte `YYYY-MM-DD` em `DD/MM/YYYY`. Não usa `new Date(iso)` por causa
 * do timezone-shift (ver §decisões acima). Em qualquer string fora do
 * formato, devolve a entrada inalterada — caller decide se quer um
 * fallback diferente.
 */
export function formatBRDate(iso: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) {
    return iso;
  }
  const [, year, month, day] = match;
  return `${day}/${month}/${year}`;
}

/**
 * Desloca uma data ISO `YYYY-MM-DD` em `deltaDays` (pode ser negativo) e
 * devolve outra string `YYYY-MM-DD`. A aritmética roda em UTC para não sofrer
 * o timezone-shift do `new Date(iso)` local (mesmo motivo de `formatBRDate`).
 * Em qualquer string fora do formato, devolve a entrada inalterada.
 *
 * Usado por FRONT 02.1: a data do Omie de uma linha conciliada é derivada do
 * `days_diff` assinado do contrato — `omie = transaction_date - days_diff`.
 */
export function shiftISODate(iso: string, deltaDays: number): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!match) {
    return iso;
  }
  const [, year, month, day] = match;
  const base = Date.UTC(Number(year), Number(month) - 1, Number(day));
  const shifted = new Date(base + deltaDays * 86_400_000);
  const y = shifted.getUTCFullYear();
  const m = String(shifted.getUTCMonth() + 1).padStart(2, '0');
  const d = String(shifted.getUTCDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

/** Mapeia o `account_type` do parse IA para rótulo em PT-BR. */
export function formatAccountType(type: 'checking' | 'credit_card'): string {
  switch (type) {
    case 'checking':
      return 'Conta Corrente';
    case 'credit_card':
      return 'Cartão de Crédito';
  }
}
