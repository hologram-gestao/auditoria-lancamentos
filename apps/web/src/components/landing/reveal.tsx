'use client';

/**
 * Efeitos da landing que precisam de JS (86e3fr9vz), num componente só e sem
 * biblioteca de animação:
 *
 *   1. marca a raiz `.landing` com `data-lp-js`, que é o que autoriza o CSS a
 *      esconder os blocos `[data-reveal]` (sem JS, tudo aparece);
 *   2. revela cada `[data-reveal]` quando ele entra na tela (`IntersectionObserver`),
 *      uma vez só;
 *   3. liga `data-scrolled` no header (`[data-lp-header]`) quando a página sai do topo;
 *   4. spotlight dos cards (86e3gr6k5): escreve a posição do ponteiro em `--lp-mx` e
 *      `--lp-my` (px) no `.lp-card` sob ele; o `.lp-card::after` desenha o brilho ali.
 *      Só com mouse de verdade (`hover: hover`) e SEM movimento reduzido: o brilho
 *      seguindo o ponteiro é movimento disparado por interação.
 *   5. "Como funciona" vivo (86e3gwzj0): em cada `[data-lp-stepper]`, o passo ativo
 *      (`data-active` no `[data-lp-step]`) avança a cada 3 s, um ciclo de 12 s para os
 *      quatro, SÓ enquanto a seção está na tela e sem ponteiro nem foco dentro dela.
 *      A linha de progresso vai até o centro da pastilha do passo ativo: a distância
 *      é medida no DOM e escrita em `--lp-progress-x` (linha horizontal, `lg`) e
 *      `--lp-progress-y` (vertical, abaixo). Sem JS ou sob movimento reduzido nada
 *      disso liga, e o bloco fica no estado final: todos os passos acesos.
 *
 * Não renderiza nada. Sob `prefers-reduced-motion` o CSS já mostra o estado final;
 * o observer continua rodando, sem efeito visível.
 */
import { useEffect } from 'react';

const SCROLLED_AFTER_PX = 8;

/** Tempo de cada passo aceso: quatro passos, um ciclo de 12 s. */
export const STEPPER_STEP_MS = 3000;

function prefersReducedMotion(): boolean {
  return (
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

/**
 * Liga o "passo a passo" de um bloco e devolve a função que o desliga. O bloco ganha
 * `data-lp-stepper-on` (o CSS só apaga os passos inativos com ele), e o passo ativo
 * ganha `data-active`.
 */
function startStepper(stepper: HTMLElement): () => void {
  const steps = Array.from(stepper.querySelectorAll<HTMLElement>('[data-lp-step]'));
  const line = stepper.querySelector<HTMLElement>('[data-lp-step-line]');
  if (steps.length < 2) return () => undefined;

  let active = 0;
  let visible = false;
  let paused = false;

  const paint = () => {
    steps.forEach((step, index) => {
      if (index === active) step.setAttribute('data-active', '');
      else step.removeAttribute('data-active');
    });
    const pill = steps[active]?.querySelector<HTMLElement>('[data-lp-step-pill]');
    if (!line || !pill) return;
    const lineBox = line.getBoundingClientRect();
    const pillBox = pill.getBoundingClientRect();
    const x = Math.max(0, pillBox.left + pillBox.width / 2 - lineBox.left);
    const y = Math.max(0, pillBox.top + pillBox.height / 2 - lineBox.top);
    stepper.style.setProperty('--lp-progress-x', `${x}px`);
    stepper.style.setProperty('--lp-progress-y', `${y}px`);
  };

  stepper.setAttribute('data-lp-stepper-on', '');
  paint();

  const timer = window.setInterval(() => {
    if (!visible || paused) return;
    active = (active + 1) % steps.length;
    paint();
  }, STEPPER_STEP_MS);

  let observer: IntersectionObserver | undefined;
  if ('IntersectionObserver' in window) {
    observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) visible = entry.isIntersecting;
      },
      { threshold: 0.25 },
    );
    observer.observe(stepper);
  } else {
    visible = true;
  }

  const pause = () => {
    paused = true;
  };
  const resume = () => {
    paused = stepper.matches(':hover') || stepper.contains(document.activeElement);
  };
  const onFocusOut = (event: FocusEvent) => {
    if (!stepper.contains(event.relatedTarget as Node | null)) paused = false;
  };
  stepper.addEventListener('pointerenter', pause);
  stepper.addEventListener('pointerleave', resume);
  stepper.addEventListener('focusin', pause);
  stepper.addEventListener('focusout', onFocusOut);
  window.addEventListener('resize', paint, { passive: true });

  return () => {
    window.clearInterval(timer);
    observer?.disconnect();
    stepper.removeEventListener('pointerenter', pause);
    stepper.removeEventListener('pointerleave', resume);
    stepper.removeEventListener('focusin', pause);
    stepper.removeEventListener('focusout', onFocusOut);
    window.removeEventListener('resize', paint);
    stepper.removeAttribute('data-lp-stepper-on');
    stepper.style.removeProperty('--lp-progress-x');
    stepper.style.removeProperty('--lp-progress-y');
    steps.forEach((step) => step.removeAttribute('data-active'));
  };
}

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

    const reducedMotion = prefersReducedMotion();
    const spotlight =
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(hover: hover)').matches &&
      !reducedMotion;
    const onPointerMove = (event: PointerEvent) => {
      if (event.pointerType !== 'mouse' || !(event.target instanceof Element)) return;
      const card = event.target.closest<HTMLElement>('.lp-card');
      if (!card) return;
      const rect = card.getBoundingClientRect();
      card.style.setProperty('--lp-mx', `${event.clientX - rect.left}px`);
      card.style.setProperty('--lp-my', `${event.clientY - rect.top}px`);
    };
    if (spotlight) root.addEventListener('pointermove', onPointerMove, { passive: true });

    const stopSteppers = reducedMotion
      ? []
      : Array.from(root.querySelectorAll<HTMLElement>('[data-lp-stepper]')).map(startStepper);

    return () => {
      stopSteppers.forEach((stop) => stop());
      observer?.disconnect();
      window.removeEventListener('scroll', onScroll);
      root.removeEventListener('pointermove', onPointerMove);
      root.removeAttribute('data-lp-js');
    };
  }, []);

  return null;
}
