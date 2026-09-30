/**
 * Dinamismo abaixo do hero (86e3gwzj0): o "Como funciona" vivo do `reveal.tsx`
 * (um passo aceso por vez, 3 s cada, só visível, pausa no hover e no foco, nada sob
 * movimento reduzido) e o teto de elementos da vinheta de "Segurança", a única.
 */
import { act, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { LandingAudience } from '../landing-audience';
import { LandingHow } from '../landing-how';
import { STEPPER_STEP_MS, LandingEffects } from '../reveal';
import { SecurityVignette } from '../section-vignettes';

function mockEnvironment({
  reducedMotion = false,
  visible = true,
}: { reducedMotion?: boolean; visible?: boolean } = {}) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: query.includes('prefers-reduced-motion') ? reducedMotion : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  class Observer {
    constructor(private readonly callback: IntersectionObserverCallback) {}
    observe(target: Element) {
      this.callback(
        [{ isIntersecting: visible, target } as IntersectionObserverEntry],
        this as unknown as IntersectionObserver,
      );
    }
    disconnect() {}
    unobserve() {}
  }
  vi.stubGlobal('IntersectionObserver', Observer);
}

function renderLanding() {
  const view = render(
    <div className="landing">
      <LandingHow />
      <LandingEffects />
    </div>,
  );
  const stepper = view.container.querySelector<HTMLElement>('[data-lp-stepper]');
  if (!stepper) throw new Error('sem [data-lp-stepper]');
  const activeIndex = () =>
    Array.from(stepper.querySelectorAll('[data-lp-step]')).findIndex((step) =>
      step.hasAttribute('data-active'),
    );
  return { stepper, activeIndex };
}

describe('"Como funciona" vivo', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it(`acende um passo por vez, ${STEPPER_STEP_MS} ms cada, e volta ao primeiro`, () => {
    mockEnvironment();
    const { stepper, activeIndex } = renderLanding();
    expect(stepper).toHaveAttribute('data-lp-stepper-on');
    expect(activeIndex()).toBe(0);
    act(() => vi.advanceTimersByTime(STEPPER_STEP_MS));
    expect(activeIndex()).toBe(1);
    act(() => vi.advanceTimersByTime(STEPPER_STEP_MS * 3));
    expect(activeIndex()).toBe(0);
    // A linha de progresso recebe a distância até a pastilha ativa.
    expect(stepper.style.getPropertyValue('--lp-progress-x')).toMatch(/px$/);
  });

  it('pausa com o ponteiro em cima e volta quando ele sai', () => {
    mockEnvironment();
    const { stepper, activeIndex } = renderLanding();
    fireEvent.pointerEnter(stepper);
    act(() => vi.advanceTimersByTime(STEPPER_STEP_MS * 3));
    expect(activeIndex()).toBe(0);
    fireEvent.pointerLeave(stepper);
    act(() => vi.advanceTimersByTime(STEPPER_STEP_MS));
    expect(activeIndex()).toBe(1);
  });

  it('pausa com o foco dentro do bloco', () => {
    mockEnvironment();
    const { stepper, activeIndex } = renderLanding();
    fireEvent.focusIn(stepper);
    act(() => vi.advanceTimersByTime(STEPPER_STEP_MS * 2));
    expect(activeIndex()).toBe(0);
  });

  it('não avança com o bloco fora da tela', () => {
    mockEnvironment({ visible: false });
    const { activeIndex } = renderLanding();
    act(() => vi.advanceTimersByTime(STEPPER_STEP_MS * 3));
    expect(activeIndex()).toBe(0);
  });

  it('sob movimento reduzido não liga: nenhum passo apagado, nada muda', () => {
    mockEnvironment({ reducedMotion: true });
    const { stepper, activeIndex } = renderLanding();
    expect(stepper).not.toHaveAttribute('data-lp-stepper-on');
    act(() => vi.advanceTimersByTime(STEPPER_STEP_MS * 4));
    expect(activeIndex()).toBe(-1);
  });
});

describe('vinhetas das seções', () => {
  it('Segurança: decorativa e com no máximo 20 elementos SVG', () => {
    const { container } = render(<SecurityVignette />);
    const svg = container.querySelector('svg');
    expect(svg).toHaveAttribute('aria-hidden', 'true');
    expect(container.querySelectorAll('svg, svg *').length).toBeLessThanOrEqual(20);
  });

  it('Para quem não tem vinheta (os avatares saíram na 86e3h0xcr)', () => {
    const { container } = render(<LandingAudience />);
    expect(container.querySelector('[data-lp-vignette]')).toBeNull();
  });
});
