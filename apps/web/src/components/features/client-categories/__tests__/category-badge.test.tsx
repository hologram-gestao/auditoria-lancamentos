/**
 * Chip da categoria (86e34jd8m): tom vira token do tema; tom desconhecido cai
 * no neutro sem quebrar; sem violação de a11y.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { CategoryBadge } from '@/components/features/client-categories/category-badge';
import { assertNoA11yViolations } from '@/test/a11y';

describe('CategoryBadge', () => {
  it('mostra o nome e aplica o token do tom', async () => {
    const { container } = render(<CategoryBadge name="Fintech" tone="info" />);
    const chip = screen.getByText('Fintech');
    expect(chip.className).toContain('text-info');
    expect(chip.className).toContain('bg-info-muted');
    await assertNoA11yViolations(container);
  });

  it('tom desconhecido cai no neutro (valor novo no banco não quebra a lista)', () => {
    render(<CategoryBadge name="Saúde" tone="rosa-choque" />);
    const chip = screen.getByText('Saúde');
    expect(chip.className).toContain('text-muted-foreground');
    expect(chip.className).not.toContain('text-info');
  });
});
