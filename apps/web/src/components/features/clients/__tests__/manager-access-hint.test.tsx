/**
 * "+N" ao lado do responsável na lista de clientes (86e390m4c).
 *
 * O que se prova: some quando só há uma pessoa; anuncia a explicação INTEIRA
 * no nome acessível (singular/plural); é focável por teclado; e tocar nele NÃO
 * navega para o detalhe (a linha da lista é clicável).
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import {
  ManagerAccessHint,
  managerAccessLabel,
} from '@/components/features/clients/manager-access-hint';
import { assertNoA11yViolations } from '@/test/a11y';

describe('ManagerAccessHint', () => {
  it('não renderiza nada com um gerente só (ou nenhum)', () => {
    const { container } = render(<ManagerAccessHint managerCount={1} />);
    expect(container).toBeEmptyDOMElement();
    const { container: zero } = render(<ManagerAccessHint managerCount={0} />);
    expect(zero).toBeEmptyDOMElement();
  });

  it('mostra "+1" com a explicação inteira no nome acessível e é focável', async () => {
    const { baseElement } = render(<ManagerAccessHint managerCount={2} />);
    const hint = screen.getByRole('img', { name: 'Mais 1 gerente com acesso a este cliente' });
    expect(hint).toHaveTextContent('+1');
    expect(hint).toHaveAttribute('tabindex', '0');
    await assertNoA11yViolations(baseElement);
  });

  it('pluraliza a partir de dois extras', () => {
    render(<ManagerAccessHint managerCount={4} />);
    expect(
      screen.getByRole('img', { name: 'Mais 3 gerentes com acesso a este cliente' }),
    ).toHaveTextContent('+3');
    expect(managerAccessLabel(1)).toBe('Mais 1 gerente com acesso a este cliente');
    expect(managerAccessLabel(2)).toBe('Mais 2 gerentes com acesso a este cliente');
  });

  it('tocar na dica não dispara o clique da linha (stopPropagation)', () => {
    const onRowClick = vi.fn();
    render(
      <div onClick={onRowClick} role="presentation">
        <ManagerAccessHint managerCount={3} />
      </div>,
    );
    fireEvent.click(screen.getByRole('img', { name: 'Mais 2 gerentes com acesso a este cliente' }));
    expect(onRowClick).not.toHaveBeenCalled();
  });
});
