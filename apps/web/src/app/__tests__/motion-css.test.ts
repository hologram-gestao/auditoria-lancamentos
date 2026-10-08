/**
 * Movimento só com permissão (86e3h579d e 86e3h57a5).
 *
 * Regra do Pedro para o app autenticado: movimento só na entrada de um bloco e na
 * resposta a uma ação, e NADA se mexe sob `prefers-reduced-motion: reduce`. O gate de
 * a11y em navegador roda com movimento reduzido, então ele não veria uma animação que
 * escapasse da regra; este teste lê o `globals.css` de verdade e reprova qualquer
 * declaração de movimento (animação, transição, transform, translate) ou o "antes" da
 * revelação (`opacity: 0`) fora de um `@media` que peça `no-preference`.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';

import postcss, { type AtRule, type Declaration, type Node } from 'postcss';
import { describe, expect, it } from 'vitest';

const CSS = readFileSync(path.resolve(__dirname, '../globals.css'), 'utf8');

const MOTION_PROPS =
  /^(animation|animation-[a-z-]+|transition|transition-[a-z-]+|transform|translate)$/;

function ancestors(node: Declaration): AtRule[] {
  const out: AtRule[] = [];
  for (
    let parent = node.parent as Node | undefined;
    parent;
    parent = parent.parent as Node | undefined
  ) {
    if (parent.type === 'atrule') out.push(parent as AtRule);
  }
  return out;
}

function underNoPreference(decl: Declaration): boolean {
  return ancestors(decl).some(
    (at) => at.name === 'media' && /prefers-reduced-motion:\s*no-preference/.test(at.params),
  );
}

function insideKeyframes(decl: Declaration): boolean {
  return ancestors(decl).some((at) => /keyframes$/.test(at.name));
}

describe('globals.css: movimento só sob prefers-reduced-motion: no-preference', () => {
  const root = postcss.parse(CSS);

  it('nenhuma animação, transição ou deslocamento fora do bloco de movimento', () => {
    const escapes: string[] = [];
    root.walkDecls((decl) => {
      if (!MOTION_PROPS.test(decl.prop) || insideKeyframes(decl)) return;
      if (!underNoPreference(decl)) {
        const rule =
          decl.parent?.type === 'rule' ? (decl.parent as { selector: string }).selector : '?';
        escapes.push(`${rule} { ${decl.prop}: ${decl.value} }`);
      }
    });
    expect(escapes).toEqual([]);
  });

  it('o "antes" da revelação (bloco invisível) só existe com movimento permitido', () => {
    const hidden: string[] = [];
    root.walkDecls('opacity', (decl) => {
      const rule =
        decl.parent?.type === 'rule' ? (decl.parent as { selector: string }).selector : '';
      if (rule.includes('data-reveal') && !underNoPreference(decl)) hidden.push(rule);
    });
    expect(hidden).toEqual([]);
  });

  it('os três primitivos têm a regra de movimento (o teste acima não passa vazio)', () => {
    const selectors: string[] = [];
    root.walkDecls((decl) => {
      if (MOTION_PROPS.test(decl.prop) && underNoPreference(decl) && decl.parent?.type === 'rule') {
        selectors.push((decl.parent as { selector: string }).selector);
      }
    });
    const joined = selectors.join('\n');
    expect(joined).toContain('.card-elevated');
    expect(joined).toContain('.animated-check path');
    expect(joined).toContain('[data-reveal-armed][data-revealed]');
  });

  it('o card sobe só com ponteiro de verdade (hover: hover)', () => {
    let lift: Declaration | undefined;
    root.walkDecls('transform', (decl) => {
      if (
        decl.parent?.type === 'rule' &&
        (decl.parent as { selector: string }).selector === '.card-elevated:hover'
      ) {
        lift = decl;
      }
    });
    expect(lift).toBeDefined();
    const medias = lift ? ancestors(lift).map((at) => at.params) : [];
    expect(medias.some((params) => /\(hover:\s*hover\)/.test(params))).toBe(true);
  });
});
