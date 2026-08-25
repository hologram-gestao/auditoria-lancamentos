/**
 * Testes do seletor de tema (86e2n39hb).
 *
 * Cobre: rótulo acessível do gatilho (critério da task: não só ícone), as três
 * opções como RADIO GROUP (a ativa anunciada com aria-checked), a troca
 * chamando o setTheme do next-themes, e axe com o menu aberto. A troca REAL de
 * classe no <html> e a persistência pós-F5 são medidas no e2e (browser), não
 * aqui — jsdom não roda o script inline do next-themes.
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const setThemeMock = vi.fn();
let currentTheme = 'system';
let currentResolved = 'light';

vi.mock('next-themes', () => ({
  useTheme: () => ({
    theme: currentTheme,
    resolvedTheme: currentResolved,
    setTheme: setThemeMock,
  }),
}));

// Import do SUT DEPOIS do `vi.mock` (a factory fecha sobre variáveis deste
// módulo — importar no topo as avaliaria antes da inicialização).
import { ThemeToggle } from '@/components/shared/theme-toggle';
import { assertNoA11yViolations } from '@/test/a11y';

beforeAll(() => {
  // Shims que o Radix espera e o jsdom não tem.
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => undefined;
  Element.prototype.releasePointerCapture = () => undefined;
  Element.prototype.scrollIntoView = () => undefined;
});

beforeEach(() => {
  setThemeMock.mockClear();
  currentTheme = 'system';
  currentResolved = 'light';
});

describe('ThemeToggle', () => {
  it('o gatilho tem rótulo acessível, não só ícone', () => {
    render(<ThemeToggle />);
    expect(screen.getByRole('button', { name: 'Alterar tema' })).toBeInTheDocument();
  });

  it('abre com as três opções em radio group e a ativa marcada', async () => {
    currentTheme = 'dark';
    currentResolved = 'dark';
    const user = userEvent.setup();
    const { baseElement } = render(<ThemeToggle />);

    await user.click(screen.getByRole('button', { name: 'Alterar tema' }));

    const opcoes = await screen.findAllByRole('menuitemradio');
    expect(opcoes.map((o) => o.textContent)).toEqual(['Claro', 'Escuro', 'Seguir o sistema']);
    expect(screen.getByRole('menuitemradio', { name: 'Escuro' })).toHaveAttribute(
      'aria-checked',
      'true',
    );
    expect(screen.getByRole('menuitemradio', { name: 'Claro' })).toHaveAttribute(
      'aria-checked',
      'false',
    );
    await assertNoA11yViolations(baseElement);
  });

  it('escolher uma opção chama o setTheme do next-themes', async () => {
    const user = userEvent.setup();
    render(<ThemeToggle />);

    await user.click(screen.getByRole('button', { name: 'Alterar tema' }));
    await user.click(await screen.findByRole('menuitemradio', { name: 'Escuro' }));

    await waitFor(() => {
      expect(setThemeMock).toHaveBeenCalledWith('dark');
    });
  });
});
