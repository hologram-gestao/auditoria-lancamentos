/**
 * Asserção de acessibilidade para os testes de componente (axe-core em jsdom).
 *
 * **Por que aqui e não só no Playwright:** o DoD da sprint pede axe-core sem
 * violações `critical`/`serious` nas telas tocadas. A suíte do Playwright
 * (`e2e/a11y.spec.ts`) cobre a página REAL — mas exige app + API + browser no
 * ar. Esta asserção roda no `pnpm test` de sempre, sem infra, e pega a maior
 * parte das regressões (rótulo faltando, `aria-*` inválido, ordem de heading,
 * botão sem nome acessível) no momento em que o componente é escrito.
 *
 * `color-contrast` fica DESLIGADO: jsdom não calcula layout nem resolve as CSS
 * vars do tema, então a regra só produziria `incomplete` ruidoso. Contraste é
 * verificado na esteira do Playwright, que roda num browser de verdade.
 */
import axe, { type Result } from 'axe-core';

/** Impactos que reprovam (o DoD fala em `critical`/`serious`). */
const BLOCKING_IMPACTS = new Set(['critical', 'serious']);

function describeViolations(violations: Result[]): string {
  return violations
    .map((v) => {
      const targets = v.nodes.map((n) => n.target.join(' ')).join(', ');
      return `- [${v.impact}] ${v.id}: ${v.help} (${targets})\n  ${v.helpUrl}`;
    })
    .join('\n');
}

/**
 * Roda o axe no container e lança com um relatório legível se houver violação
 * `critical`/`serious`. Falhar com a lista das regras é o que torna o erro
 * acionável — "a11y quebrou" sozinho não conserta nada.
 */
export async function assertNoA11yViolations(container: Element): Promise<void> {
  const results = await axe.run(container, {
    resultTypes: ['violations'],
    rules: { 'color-contrast': { enabled: false } },
  });
  const blocking = results.violations.filter(
    (v) => v.impact !== null && v.impact !== undefined && BLOCKING_IMPACTS.has(v.impact),
  );
  if (blocking.length > 0) {
    throw new Error(
      `axe-core encontrou ${blocking.length} violação(ões) critical/serious:\n${describeViolations(blocking)}`,
    );
  }
}
