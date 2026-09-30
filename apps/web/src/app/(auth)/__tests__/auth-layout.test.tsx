/**
 * Shell da tela de login (86e3h1h75): a ponte entre a landing e o sistema.
 *
 * As duas colunas de `lg` para cima são CSS (o jsdom não aplica media query); aqui se
 * prova o que é DOM: o tema Hologram fixo no wrapper, o painel de marca com a frase do
 * hero e os chips, o caminho de volta para o site e o nome da empresa. A largura e o
 * contraste sobre a aurora são medidos em browser (`e2e/a11y-mocked.spec.ts`).
 */
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { hero, login } from '@/components/landing/content';

import AuthLayout from '../layout';

function renderLayout() {
  return render(
    <AuthLayout>
      <div data-testid="conteudo-da-pagina" />
    </AuthLayout>,
  );
}

describe('AuthLayout — a ponte entre a landing e o sistema', () => {
  it('fixa o tema Hologram no wrapper, como a landing', () => {
    const { container } = renderLayout();
    const wrapper = container.firstElementChild;
    expect(wrapper).toHaveClass('hologram');
    expect(wrapper).toHaveClass('lp-public');
    // Nenhum seletor de tema na página de marca.
    expect(screen.queryByRole('button', { name: /tema/i })).toBeNull();
  });

  it('tem o painel de marca com a frase do hero e os chips de formatos', () => {
    renderLayout();
    const painel = screen.getByRole('complementary', { name: login.panelLabel });
    expect(painel).toHaveTextContent(`${hero.titleBefore}${hero.titleHighlight}${hero.titleAfter}`);
    expect(within(painel).getByText(hero.titleHighlight)).toHaveClass('lp-gradient-text');
    const chips = within(painel).getByRole('list', { name: hero.chipsLabel });
    expect(within(chips).getAllByRole('listitem')).toHaveLength(hero.chips.length);
    // O painel só existe de `lg` para cima: abaixo, o celular vê só o card.
    expect(painel).toHaveClass('hidden', 'lg:flex');
  });

  it('leva de volta para o site e assina com o nome da empresa', () => {
    renderLayout();
    const main = screen.getByRole('main');
    expect(within(main).getByRole('link', { name: login.backToSite })).toHaveAttribute('href', '/');
    expect(within(main).getByText(login.footer)).toBeInTheDocument();
    expect(within(main).getByTestId('conteudo-da-pagina')).toBeInTheDocument();
  });
});
