/**
 * A hierarquia do plano contábil NA TELA (86e3n70p9).
 *
 * O servidor já devolve a lista na ordem da classificação (`sort_key`, derivada
 * na gravação): aqui só se LÊ o grau de cada conta para o recuo do nome. O grau é
 * o número de segmentos da classificação menos um (`1` = 0, `1.1` = 1,
 * `1.1.1.02.001` = 4); conta sem classificação não tem recuo. Nada aqui ordena:
 * ordenar no cliente quebraria a paginação, que é em SQL.
 *
 * O recuo é por classe LITERAL (o Tailwind só gera o que está escrito), com um
 * teto: o Domínio vai ao grau 4 e o modelo da plataforma raramente passa disso;
 * acima do teto a conta recua como a mais funda, em vez de sumir da tela em 390px.
 */

/** Grau da conta: segmentos da classificação menos um; sem classificação, 0. */
export function classificationDepth(classification: string | null | undefined): number {
  if (!classification) return 0;
  return classification.split('.').length - 1;
}

/** Recuo do nome por grau, do 0 ao teto. `pl-*` do Tailwind, 1rem por nível. */
const INDENT_CLASS_BY_DEPTH = ['pl-0', 'pl-4', 'pl-8', 'pl-12', 'pl-16', 'pl-20'] as const;

export const MAX_INDENT_DEPTH = INDENT_CLASS_BY_DEPTH.length - 1;

export function indentClassFor(depth: number): string {
  const bounded = Math.max(0, Math.min(depth, MAX_INDENT_DEPTH));
  return INDENT_CLASS_BY_DEPTH[bounded] ?? INDENT_CLASS_BY_DEPTH[0];
}
