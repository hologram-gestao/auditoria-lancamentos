'use client';

/**
 * Efeitos da landing que precisam de JS (86e3fr9vz), num componente só e sem
 * biblioteca de animação:
 *
 *   1. marca a raiz `.landing` com `data-lp-js`, que é o que autoriza o CSS a
 *      esconder os blocos `[data-reveal]` (sem JS, tudo aparece);
 *   2. revela cada `[data-reveal]` quando ele entra na tela (`IntersectionObserver`),
 *      uma vez só;
 *   3. liga `data-scrolled` no header (`[data-lp-header]`) quando a página sai do topo.
 *
 * Não renderiza nada. Sob `prefers-reduced-motion` o CSS já mostra o estado final;
 * o observer continua rodando, sem efeito visível.
 */
import { useEffect } from 'react';

const SCROLLED_AFTER_PX = 8;

export function LandingEffects() {
  useEffect(() => {
    const root = document.querySelector<HTMLElement>('.landing');
    if (!root) return undefined;
    root.setAttribute('data-lp-js', '');

    const targets = Array.from(root.querySelectorAll<HTMLElement>('[data-reveal]'));
    let observer: IntersectionObserver | undefined;
    if ('IntersectionObserver' in window) {
      observer = new IntersectionObserver(
        (entries) => {
          for (const entry of entries) {
            if (!entry.isIntersecting) continue;
            entry.target.setAttribute('data-revealed', '');
            observer?.unobserve(entry.target);
          }
        },
        { rootMargin: '0px 0px -10% 0px', threshold: 0.08 },
      );
      targets.forEach((el) => observer?.observe(el));
    } else {
      targets.forEach((el) => el.setAttribute('data-revealed', ''));
    }

    const header = root.querySelector<HTMLElement>('[data-lp-header]');
    const onScroll = () => {
      if (!header) return;
      if (window.scrollY > SCROLLED_AFTER_PX) header.setAttribute('data-scrolled', '');
      else header.removeAttribute('data-scrolled');
    };
    onScroll();
    window.addEventListener('scroll', onScroll, { passive: true });

    return () => {
      observer?.disconnect();
      window.removeEventListener('scroll', onScroll);
      root.removeAttribute('data-lp-js');
    };
  }, []);

  return null;
}
