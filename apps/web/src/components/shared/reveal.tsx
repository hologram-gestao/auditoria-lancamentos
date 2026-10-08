'use client';

/**
 * Revelação na rolagem (86e3h579d): o bloco entra uma vez, quando aparece na tela.
 * Nasceu na landing (`components/landing/reveal.tsx`) e virou primitivo do app.
 *
 * Três peças:
 *   - `observeReveal(targets, options)`: o `IntersectionObserver` que marca
 *     `data-revealed` em cada alvo na primeira vez que ele entra na tela e para de
 *     observá-lo. Sem `IntersectionObserver` (jsdom, navegador antigo), revela tudo na
 *     hora. É a parte que a landing e o app dividem.
 *   - `useReveal(ref)`: liga o observer num elemento do app e o marca com
 *     `data-reveal-armed`, que é o que autoriza o CSS a escondê-lo até a entrada. Sem
 *     JS, ou antes da hidratação, o conteúdo está visível.
 *   - `<Reveal>`: o elemento (`as`, ou o filho com `asChild`) com `data-reveal` e o hook.
 *
 * O efeito do app é a regra `[data-reveal-armed]` do `globals.css`: 350 ms, sobe 8 px,
 * por ANIMAÇÃO e não por transição, para não brigar com a transição de hover do
 * `Card variant="elevated"` quando os dois vestem o mesmo elemento. Mora SÓ sob
 * `prefers-reduced-motion: no-preference`: com movimento reduzido nada fica escondido
 * e nada se mexe. A landing tem a própria regra (600 ms, 16 px) no `landing.css`, que
 * não mudou; os alvos dela não ganham `data-reveal-armed`.
 *
 * Nunca em linha de tabela, em lista virtualizada nem em loop: é a entrada de um
 * BLOCO, uma vez.
 */
import { Slot } from '@radix-ui/react-slot';
import * as React from 'react';

/** Margem e limiar do app: o bloco revela quando uma faixa dele passa da borda de baixo. */
const APP_REVEAL_OPTIONS: IntersectionObserverInit = {
  rootMargin: '0px 0px -24px 0px',
  threshold: 0,
};

/**
 * Observa os alvos e marca `data-revealed` em cada um na primeira entrada. Devolve a
 * função que desliga o observer.
 */
export function observeReveal(
  targets: readonly Element[],
  options: IntersectionObserverInit = APP_REVEAL_OPTIONS,
): () => void {
  if (typeof window === 'undefined' || !('IntersectionObserver' in window)) {
    targets.forEach((el) => el.setAttribute('data-revealed', ''));
    return () => undefined;
  }
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      entry.target.setAttribute('data-revealed', '');
      observer.unobserve(entry.target);
    }
  }, options);
  targets.forEach((el) => observer.observe(el));
  return () => observer.disconnect();
}

// No servidor o `useLayoutEffect` avisa; lá não há o que observar.
const useIsomorphicLayoutEffect =
  typeof window === 'undefined' ? React.useEffect : React.useLayoutEffect;

/**
 * Arma a revelação do elemento: antes da pintura (layout effect), para o bloco que já
 * nasce na tela não piscar visível e depois sumir. Uma vez só: elemento já revelado
 * não é escondido de novo.
 */
export function useReveal(ref: React.RefObject<HTMLElement | null>): void {
  useIsomorphicLayoutEffect(() => {
    const el = ref.current;
    if (!el || el.hasAttribute('data-revealed')) return undefined;
    el.setAttribute('data-reveal-armed', '');
    return observeReveal([el]);
  }, [ref]);
}

type RevealElement = 'div' | 'section' | 'li' | 'ul' | 'article';

export interface RevealProps extends React.HTMLAttributes<HTMLElement> {
  /** Elemento renderizado (padrão `div`). Ignorado com `asChild`. */
  as?: RevealElement;
  /** Veste o filho único em vez de criar um elemento. */
  asChild?: boolean;
  /** Atraso da entrada, para escalonar itens de um grupo. */
  delayMs?: number;
}

function assignRef<T>(ref: React.ForwardedRef<T>, value: T | null): void {
  if (typeof ref === 'function') ref(value);
  else if (ref) ref.current = value;
}

export const Reveal = React.forwardRef<HTMLElement, RevealProps>(
  ({ as = 'div', asChild = false, delayMs, style, ...props }, forwardedRef) => {
    const innerRef = React.useRef<HTMLElement | null>(null);
    useReveal(innerRef);
    const setRef = React.useCallback(
      (node: HTMLElement | null) => {
        innerRef.current = node;
        assignRef(forwardedRef, node);
      },
      [forwardedRef],
    );
    const mergedStyle =
      delayMs !== undefined
        ? ({ ...style, '--reveal-delay': `${delayMs}ms` } as React.CSSProperties)
        : style;
    const Comp: React.ElementType = asChild ? Slot : as;
    return <Comp ref={setRef} data-reveal="" style={mergedStyle} {...props} />;
  },
);
Reveal.displayName = 'Reveal';
