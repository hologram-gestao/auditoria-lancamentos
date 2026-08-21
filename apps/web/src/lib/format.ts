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

const MONTH_NAMES_PT = [
  'Janeiro',
  'Fevereiro',
  'Março',
  'Abril',
  'Maio',
  'Junho',
  'Julho',
  'Agosto',
  'Setembro',
  'Outubro',
  'Novembro',
  'Dezembro',
] as const;

/**
 * Mês de referência (`YYYY-MM-DD`, sempre dia 1) → `"Junho de 2026"`.
 *
 * Formatador ÚNICO do mês de referência: antes cada tela reimplementava o
 * parse manual, e "valor derivado calculado em 2 lugares diverge" vale também
 * para rótulo. Parse manual (sem `new Date(iso)`) pelo mesmo motivo do
 * `formatBRDate`: `new Date('2026-06-01')` é UTC e volta para maio no Brasil.
 *
 * Aceita também `YYYY-MM` (formato do `<input type="month">`). Entrada fora do
 * padrão volta inalterada — caller decide o fallback.
 */
export function formatReferenceMonth(referenceMonth: string | undefined | null): string {
  if (!referenceMonth) return '—';
  const match = /^(\d{4})-(\d{2})(?:-\d{2})?$/.exec(referenceMonth);
  if (!match) return referenceMonth;
  const [, year, month] = match;
  const index = Number(month) - 1;
  const name = MONTH_NAMES_PT[index];
  if (name === undefined) return referenceMonth;
  return `${name} de ${year}`;
}

/** `YYYY-MM-DD` (dia 1) → `YYYY-MM`, o formato do `<input type="month">`. */
export function toMonthInputValue(referenceMonth: string): string {
  const match = /^(\d{4})-(\d{2})/.exec(referenceMonth);
  return match ? `${match[1]}-${match[2]}` : referenceMonth;
}

/**
 * Rótulo PT-BR do `account_type` do OMIE (código de 2 letras).
 *
 * Formatador ÚNICO desse mapeamento — a tela de Contas Bancárias e o card da
 * conta consomem daqui. ⚠️ `CA` = Conta Aplicação (investimento), NÃO cartão
 * (bug M-1, auditoria 20/05/2026); cartão é `CR`.
 *
 * A doc da Omie declara mais 11 tipos (`AC, AD, CE, CG, CN, CV, MT, PG`, …):
 * os menos comuns caem no default e aparecem com o código cru, para um tipo
 * novo do Omie não quebrar a UI.
 */
export function formatOmieAccountType(type: string): string {
  switch (type.trim().toUpperCase()) {
    case 'CC':
      return 'Conta Corrente';
    case 'CR':
      return 'Cartão de Crédito';
    case 'CA':
      return 'Conta Aplicação';
    case 'CP':
      return 'Conta Poupança';
    case 'CX':
      return 'Caixinha';
    default:
      return type;
  }
}

/**
 * `synced_at` (ISO) → "Sincronizado agora" / "Sincronizado há 3 horas".
 *
 * Sem `date-fns` aqui de propósito: é a mesma frase em duas telas e o cálculo
 * cabe em 6 linhas. Abaixo de 1 min é "agora" — dizer "há 4 segundos" logo
 * depois do clique parece bug.
 */
export function formatSyncedAt(syncedAt: string | null | undefined): string {
  if (!syncedAt) return 'Nunca sincronizado';
  const date = new Date(syncedAt);
  if (Number.isNaN(date.getTime())) return 'Nunca sincronizado';
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 60_000) return 'Sincronizado agora';
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 60) return `Sincronizado há ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `Sincronizado há ${hours} h`;
  const days = Math.floor(hours / 24);
  return `Sincronizado há ${days} dia${days === 1 ? '' : 's'}`;
}

/** Mapeia o `account_type` do parse IA para rótulo em PT-BR. */
export function formatAccountType(type: 'checking' | 'credit_card' | 'investment'): string {
  switch (type) {
    case 'checking':
      return 'Conta Corrente';
    case 'credit_card':
      return 'Cartão de Crédito';
    case 'investment':
      return 'Conta Aplicação';
  }
}

/** `2026-06-12T14:32:00Z` → `12/06/2026 às 14h32` (timezone do navegador).
 *  Morava no card da lista; o header da revisão passou a usar também
 *  (86e2n39f1) — uma cópia só, nunca duas. */
export function formatCreatedAt(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const day = String(date.getDate()).padStart(2, '0');
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  return `${day}/${month}/${date.getFullYear()} às ${hours}h${minutes}`;
}
