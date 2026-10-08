/**
 * As cinco vinhetas dos estados vazios (86e3h57b5): decorativas, leves (no máximo 20
 * elementos SVG) e pintadas só por token. A entrada de 600 ms mora no `globals.css`,
 * sob `no-preference` (travado em `app/__tests__/motion-css.test.ts`).
 */
import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import {
  ClientsVignette,
  MappingVignette,
  OriginVignette,
  PortfolioVignette,
  ReconciliationsVignette,
} from '../vignettes';

const VIGNETTES = [
  ['reconciliations', ReconciliationsVignette],
  ['origin', OriginVignette],
  ['portfolio', PortfolioVignette],
  ['mapping', MappingVignette],
  ['clients', ClientsVignette],
] as const;

describe.each(VIGNETTES)('vinheta %s', (name, Vignette) => {
  it('é decorativa e fica fora da ordem de foco', () => {
    const { container } = render(<Vignette />);
    const svg = container.querySelector('svg');
    expect(svg).toHaveAttribute('aria-hidden', 'true');
    expect(svg).toHaveAttribute('focusable', 'false');
    expect(svg).toHaveAttribute('data-vignette', name);
    expect(svg).toHaveClass('vignette');
  });

  it('tem no máximo 20 elementos SVG', () => {
    const { container } = render(<Vignette />);
    expect(container.querySelectorAll('svg *').length).toBeLessThanOrEqual(20);
  });

  it('cor só por token: nenhum fill/stroke literal, nenhuma classe de paleta', () => {
    const { container } = render(<Vignette />);
    for (const el of Array.from(container.querySelectorAll('svg, svg *'))) {
      for (const attr of ['fill', 'stroke', 'style']) {
        const value = el.getAttribute(attr) ?? '';
        expect(value, `${attr} de <${el.tagName}>`).not.toMatch(/#[0-9a-f]{3,8}|rgb|hsl\(/i);
      }
      const fill = el.getAttribute('fill');
      if (fill !== null) expect(fill).toBe('none');
      expect(el.getAttribute('class') ?? '').not.toMatch(
        /\b(fill|stroke)-(slate|gray|zinc|red|green|blue|emerald|amber|brand)\b/,
      );
    }
  });

  it('entra por partes, cada uma com o seu índice', () => {
    const { container } = render(<Vignette />);
    const parts = Array.from(container.querySelectorAll('[data-vig-part]'));
    expect(parts.length).toBeGreaterThan(1);
    parts.forEach((part, index) => {
      expect((part as SVGElement).style.getPropertyValue('--vig-i')).toBe(String(index));
    });
  });
});
