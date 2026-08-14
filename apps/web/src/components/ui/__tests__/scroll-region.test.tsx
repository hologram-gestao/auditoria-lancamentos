/**
 * Trava do contrato do `<ScrollRegion>` (defeito 86e2u4nxg).
 *
 * O componente existe para que a decisão de acessibilidade de uma região
 * rolável (`tabIndex` + `role` + nome) tenha UMA implementação, e não uma cópia
 * por tela. A trava aqui é sobre os ATRIBUTOS, não sobre o axe: jsdom não tem
 * layout, nada transborda e a regra `scrollable-region-focusable` nunca dispara
 * — foi exatamente por isso que o `pnpm test` deixou passar o defeito irmão
 * 86e2gwuxn. A verificação em browser real é do Playwright
 * (`e2e/a11y-mocked.spec.ts`, job `web_a11y`).
 */

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ScrollRegion } from '@/components/ui/scroll-region';
import { assertNoA11yViolations } from '@/test/a11y';

describe('ScrollRegion', () => {
  it('é alcançável pelo teclado, com papel e nome', () => {
    render(<ScrollRegion label="Lista de exemplo">conteúdo</ScrollRegion>);

    const region = screen.getByRole('region', { name: 'Lista de exemplo' });
    expect(region).toHaveAttribute('tabindex', '0');
    // Sem `overflow` não há recorte, e o conteúdo volta a vazar da caixa.
    expect(region).toHaveClass('overflow-auto');
  });

  it('mantém o overflow ao receber classes de layout de quem chama', () => {
    render(
      <ScrollRegion label="Lista de exemplo" className="min-h-0 flex-1">
        conteúdo
      </ScrollRegion>,
    );

    const region = screen.getByRole('region', { name: 'Lista de exemplo' });
    expect(region).toHaveClass('overflow-auto', 'min-h-0', 'flex-1');
  });

  it('repassa atributos de estado ao elemento (ex.: aria-busy)', () => {
    render(
      <ScrollRegion label="Lista de exemplo" aria-busy>
        conteúdo
      </ScrollRegion>,
    );

    expect(screen.getByRole('region', { name: 'Lista de exemplo' })).toHaveAttribute(
      'aria-busy',
      'true',
    );
  });

  it('não introduz violação de a11y', async () => {
    const { container } = render(<ScrollRegion label="Lista de exemplo">conteúdo</ScrollRegion>);

    await assertNoA11yViolations(container);
  });
});
