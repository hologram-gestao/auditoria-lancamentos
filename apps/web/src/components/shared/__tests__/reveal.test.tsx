import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { observeReveal, Reveal } from '../reveal';

/** `IntersectionObserver` de mentira: guarda o callback para o teste disparar. */
class FakeObserver {
  static instances: FakeObserver[] = [];
  observed: Element[] = [];
  unobserved: Element[] = [];
  disconnected = false;
  constructor(public callback: IntersectionObserverCallback) {
    FakeObserver.instances.push(this);
  }
  observe(el: Element) {
    this.observed.push(el);
  }
  unobserve(el: Element) {
    this.unobserved.push(el);
  }
  disconnect() {
    this.disconnected = true;
  }
  fire(el: Element, isIntersecting: boolean) {
    this.callback(
      [{ target: el, isIntersecting } as IntersectionObserverEntry],
      this as unknown as IntersectionObserver,
    );
  }
}

describe('Reveal (86e3h579d)', () => {
  beforeEach(() => {
    FakeObserver.instances = [];
    vi.stubGlobal('IntersectionObserver', FakeObserver);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('marca data-reveal, arma a revelação e só revela quando o bloco entra na tela', () => {
    render(<Reveal data-testid="bloco">conteúdo</Reveal>);
    const bloco = screen.getByTestId('bloco');
    expect(bloco).toHaveAttribute('data-reveal');
    expect(bloco).toHaveAttribute('data-reveal-armed');
    expect(bloco).not.toHaveAttribute('data-revealed');

    const observer = FakeObserver.instances[0];
    expect(observer?.observed).toContain(bloco);
    observer?.fire(bloco, false);
    expect(bloco).not.toHaveAttribute('data-revealed');
    observer?.fire(bloco, true);
    expect(bloco).toHaveAttribute('data-revealed');
    // Uma vez só: deixa de observar.
    expect(observer?.unobserved).toContain(bloco);
  });

  it('desliga o observer ao desmontar', () => {
    const { unmount } = render(<Reveal>conteúdo</Reveal>);
    unmount();
    expect(FakeObserver.instances[0]?.disconnected).toBe(true);
  });

  it('escalona pelo delayMs e veste o filho com asChild', () => {
    render(
      <Reveal asChild delayMs={120}>
        <section aria-label="Bloco">conteúdo</section>
      </Reveal>,
    );
    const bloco = screen.getByRole('region', { name: 'Bloco' });
    expect(bloco).toHaveAttribute('data-reveal');
    expect(bloco.style.getPropertyValue('--reveal-delay')).toBe('120ms');
  });

  it('renderiza o elemento pedido em `as`', () => {
    render(
      <ul>
        <Reveal as="li">item</Reveal>
      </ul>,
    );
    expect(screen.getByRole('listitem')).toHaveAttribute('data-reveal');
  });
});

describe('observeReveal', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('sem IntersectionObserver revela tudo na hora (nada fica escondido)', () => {
    // `'IntersectionObserver' in window` precisa ser falso, não só undefined.
    const original = Object.getOwnPropertyDescriptor(window, 'IntersectionObserver');
    delete (window as { IntersectionObserver?: unknown }).IntersectionObserver;
    const a = document.createElement('div');
    const b = document.createElement('div');
    const stop = observeReveal([a, b]);
    expect(a).toHaveAttribute('data-revealed');
    expect(b).toHaveAttribute('data-revealed');
    stop();
    if (original) Object.defineProperty(window, 'IntersectionObserver', original);
  });

  it('com observer, revela cada alvo na própria entrada', () => {
    FakeObserver.instances = [];
    vi.stubGlobal('IntersectionObserver', FakeObserver);
    const a = document.createElement('div');
    const b = document.createElement('div');
    observeReveal([a, b], { threshold: 0.08 });
    const observer = FakeObserver.instances[0];
    observer?.fire(a, true);
    expect(a).toHaveAttribute('data-revealed');
    expect(b).not.toHaveAttribute('data-revealed');
  });
});
