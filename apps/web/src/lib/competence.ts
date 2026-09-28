/**
 * Competência (`YYYY-MM`) — a unidade da vigência do de-para e da base de
 * movimentos (Sprint 12 / R0 · R4).
 *
 * Aritmética SÓ sobre a string, nunca `new Date('2026-06')`: o JS lê isso como
 * UTC e, no fuso do Brasil, volta para maio (mesmo motivo do `formatBRDate`).
 *
 * A competência CORRENTE de referência é a do SERVIDOR sempre que houver uma
 * (`MappingListResponse.competence`); a do relógio do navegador é só o
 * fallback enquanto ela não chegou — duas pessoas em fusos diferentes, na
 * virada do mês, não podem ver padrões diferentes.
 */

export const COMPETENCE_PATTERN = /^\d{4}-(0[1-9]|1[0-2])$/;

/**
 * Aceita `unknown` de propósito: é chamada também sobre `ApiError.details`, que
 * desde a Sprint 14 carrega valores de tipos variados (listas, números) e por
 * isso é `Record<string, unknown>`. O predicado estreita para `string`.
 */
export function isCompetence(value: unknown): value is string {
  return typeof value === 'string' && COMPETENCE_PATTERN.test(value);
}

/** Competência corrente pelo relógio LOCAL — fallback, ver o cabeçalho. */
export function localCurrentCompetence(now: Date = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, '0');
  return `${now.getFullYear()}-${month}`;
}

/** `2026-01` → `2025-12`. Entrada fora do formato volta inalterada. */
export function previousCompetence(competence: string): string {
  if (!isCompetence(competence)) return competence;
  const [yearText, monthText] = competence.split('-');
  const year = Number(yearText);
  const month = Number(monthText);
  if (month === 1) return `${year - 1}-12`;
  return `${year}-${String(month - 1).padStart(2, '0')}`;
}

/** `true` quando `a` é anterior a `b` — comparação lexicográfica vale em `YYYY-MM`. */
export function isBefore(a: string, b: string): boolean {
  return a < b;
}

/**
 * Lista de competências vinda de `details.competences` dos 409 de vigência
 * (`"2026-06,2026-07"`). O `details` do erro é `Record<string, unknown>`, então
 * qualquer coisa que não seja string vira lista vazia — nunca um `.split` num
 * número.
 */
export function parseCompetenceList(raw: unknown): string[] {
  if (typeof raw !== 'string' || raw === '') return [];
  return raw
    .split(',')
    .map((item) => item.trim())
    .filter((item) => item !== '');
}
