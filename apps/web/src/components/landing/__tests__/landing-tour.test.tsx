/**
 * Tour do produto (86e3gqfkf): abas por clique e por teclado, a troca automática
 * (6 s, só visível, para no primeiro clique, pausa pelo botão só de ícone, nem começa
 * sob movimento reduzido), a linha de progresso da aba ativa (86e3h0xcr) e as
 * dimensões declaradas iguais às dos PNGs em `public/`.
 */
import { readFileSync, statSync } from 'node:fs';
import { resolve } from 'node:path';

import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { tour } from '../content';
import { LandingTour, TOUR_IMAGES } from '../landing-tour';
import { TOUR_AUTO_ADVANCE_MS } from '../landing-tour-tabs';

const PUBLIC_DIR = resolve(__dirname, '../../../../public');

/** Mídia e visibilidade controladas: jsdom não tem `matchMedia` nem `IntersectionObserver`. */
function mockEnvironment({ reducedMotion = false }: { reducedMotion?: boolean } = {}) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: query.includes('prefers-reduced-motion') ? reducedMotion : false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })),
  );
  class VisibleObserver {
    constructor(private readonly callback: IntersectionObserverCallback) {}
    observe(target: Element) {
      this.callback(
        [{ isIntersecting: true, target } as IntersectionObserverEntry],
        this as unknown as IntersectionObserver,
      );
    }
    disconnect() {}
    unobserve() {}
  }
  vi.stubGlobal('IntersectionObserver', VisibleObserver);
}

function selectedTab(): string {
  return screen.getByRole('tab', { selected: true }).textContent ?? '';
}

describe('LandingTour', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  describe('abas', () => {
    beforeEach(() => mockEnvironment({ reducedMotion: true }));

    it('mostra as cinco telas, a primeira ativa, com o print e o texto dela', () => {
      render(<LandingTour />);
      const tabs = screen.getAllByRole('tab');
      expect(tabs.map((tab) => tab.textContent)).toEqual(tour.items.map((item) => item.tab));
      expect(selectedTab()).toBe('Conciliação');
      expect(screen.getByRole('img', { name: tour.items[0].alt })).toBeVisible();
      expect(screen.getByText(tour.items[0].text)).toBeVisible();
      // Os outros painéis ficam montados, mas escondidos.
      expect(screen.queryByRole('img', { name: tour.items[1].alt })).toBeNull();
    });

    it('troca pelo clique', async () => {
      const user = userEvent.setup();
      render(<LandingTour />);
      await user.click(screen.getByRole('tab', { name: 'Lançar no Omie' }));
      expect(selectedTab()).toBe('Lançar no Omie');
      expect(screen.getByText(tour.items[2].text)).toBeVisible();
      expect(screen.getByText(tour.items[0].text)).not.toBeVisible();
    });

    it('troca pelo teclado (ArrowRight e End)', async () => {
      const user = userEvent.setup();
      render(<LandingTour />);
      await user.click(screen.getByRole('tab', { name: 'Conciliação' }));
      await user.keyboard('{ArrowRight}');
      expect(selectedTab()).toBe('Anomalias');
      expect(screen.getByRole('tab', { name: 'Anomalias' })).toHaveFocus();
      await user.keyboard('{End}');
      expect(selectedTab()).toBe('Carteira');
    });
  });

  describe('troca automática', () => {
    beforeEach(() => vi.useFakeTimers());

    it(`avança a cada ${TOUR_AUTO_ADVANCE_MS} ms e volta ao começo depois da última`, () => {
      mockEnvironment();
      render(<LandingTour />);
      expect(selectedTab()).toBe('Conciliação');
      act(() => vi.advanceTimersByTime(TOUR_AUTO_ADVANCE_MS - 1));
      expect(selectedTab()).toBe('Conciliação');
      act(() => vi.advanceTimersByTime(1));
      expect(selectedTab()).toBe('Anomalias');
      for (let i = 0; i < tour.items.length - 1; i++) {
        act(() => vi.advanceTimersByTime(TOUR_AUTO_ADVANCE_MS));
      }
      expect(selectedTab()).toBe('Conciliação');
    });

    it('para de vez no primeiro clique numa aba', () => {
      mockEnvironment();
      render(<LandingTour />);
      act(() => vi.advanceTimersByTime(TOUR_AUTO_ADVANCE_MS));
      expect(selectedTab()).toBe('Anomalias');
      const dePara = screen.getByRole('tab', { name: 'De-para' });
      fireEvent.pointerDown(dePara);
      fireEvent.mouseDown(dePara, { button: 0 });
      expect(selectedTab()).toBe('De-para');
      act(() => vi.advanceTimersByTime(TOUR_AUTO_ADVANCE_MS * 3));
      expect(selectedTab()).toBe('De-para');
      expect(screen.getByRole('button', { name: tour.resumeLabel })).toBeInTheDocument();
    });

    it('para no foco das abas', () => {
      mockEnvironment();
      render(<LandingTour />);
      act(() => screen.getByRole('tab', { name: 'Conciliação' }).focus());
      act(() => vi.advanceTimersByTime(TOUR_AUTO_ADVANCE_MS * 2));
      expect(selectedTab()).toBe('Conciliação');
    });

    it('o botão pausa e retoma a troca', () => {
      mockEnvironment();
      render(<LandingTour />);
      fireEvent.click(screen.getByRole('button', { name: tour.pauseLabel }));
      act(() => vi.advanceTimersByTime(TOUR_AUTO_ADVANCE_MS * 2));
      expect(selectedTab()).toBe('Conciliação');
      fireEvent.click(screen.getByRole('button', { name: tour.resumeLabel }));
      act(() => vi.advanceTimersByTime(TOUR_AUTO_ADVANCE_MS));
      expect(selectedTab()).toBe('Anomalias');
    });

    it('a aba ativa tem a linha de progresso decorativa, que recomeça a cada troca', () => {
      mockEnvironment();
      render(<LandingTour />);
      const progress = () =>
        screen.getByRole('tab', { selected: true }).querySelector('[data-lp-tab-progress]');
      const first = progress();
      expect(first).toHaveAttribute('aria-hidden', 'true');
      expect(first).toHaveAttribute('data-lp-tab-progress', 'running');
      expect((first as HTMLElement).style.getPropertyValue('--lp-tab-ms')).toBe(
        `${TOUR_AUTO_ADVANCE_MS}ms`,
      );
      // Só a aba ativa tem linha.
      expect(document.querySelectorAll('[data-lp-tab-progress]')).toHaveLength(1);
      act(() => vi.advanceTimersByTime(TOUR_AUTO_ADVANCE_MS));
      expect(selectedTab()).toBe('Anomalias');
      // Elemento NOVO na aba nova: a animação começa do zero.
      expect(progress()).not.toBe(first);
      expect(progress()).toHaveAttribute('data-lp-tab-progress', 'running');
    });

    it('com a troca parada, a linha fica cheia', () => {
      mockEnvironment();
      render(<LandingTour />);
      fireEvent.click(screen.getByRole('button', { name: tour.pauseLabel }));
      const line = screen
        .getByRole('tab', { selected: true })
        .querySelector('[data-lp-tab-progress]');
      expect(line).toHaveAttribute('data-lp-tab-progress', 'full');
    });

    it('o botão de pausa é só ícone: nenhum texto visível, o nome acessível diz o que faz', () => {
      mockEnvironment();
      render(<LandingTour />);
      const pause = screen.getByRole('button', { name: tour.pauseLabel });
      expect(pause.textContent).toBe('');
      expect(pause).not.toHaveAttribute('title');
      fireEvent.click(pause);
      const resume = screen.getByRole('button', { name: tour.resumeLabel });
      expect(resume.textContent).toBe('');
    });

    it('não troca, e não mostra o botão, sob movimento reduzido', () => {
      mockEnvironment({ reducedMotion: true });
      render(<LandingTour />);
      act(() => vi.advanceTimersByTime(TOUR_AUTO_ADVANCE_MS * 3));
      expect(selectedTab()).toBe('Conciliação');
      expect(screen.queryByRole('button', { name: tour.pauseLabel })).toBeNull();
      expect(document.querySelector('[data-lp-tab-progress]')).toBeNull();
    });

    it('não troca enquanto a seção está fora da tela', () => {
      mockEnvironment();
      class HiddenObserver {
        constructor(private readonly callback: IntersectionObserverCallback) {}
        observe(target: Element) {
          this.callback(
            [{ isIntersecting: false, target } as IntersectionObserverEntry],
            this as unknown as IntersectionObserver,
          );
        }
        disconnect() {}
        unobserve() {}
      }
      vi.stubGlobal('IntersectionObserver', HiddenObserver);
      render(<LandingTour />);
      act(() => vi.advanceTimersByTime(TOUR_AUTO_ADVANCE_MS * 3));
      expect(selectedTab()).toBe('Conciliação');
    });
  });

  describe('prints', () => {
    it.each(Object.entries(TOUR_IMAGES))(
      '%s: largura e altura declaradas são as do PNG, e o arquivo tem menos de 400 KB',
      (_id, image) => {
        const file = resolve(PUBLIC_DIR, image.src.slice(1));
        const png = readFileSync(file);
        // Cabeçalho PNG: assinatura de 8 bytes, depois o chunk IHDR com largura e altura.
        expect(png.subarray(12, 16).toString('ascii')).toBe('IHDR');
        expect(png.readUInt32BE(16)).toBe(image.width);
        expect(png.readUInt32BE(20)).toBe(image.height);
        expect(statSync(file).size).toBeLessThan(400 * 1024);
      },
    );
  });
});
