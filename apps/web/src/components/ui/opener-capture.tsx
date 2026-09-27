'use client';

import * as React from 'react';

/**
 * Guarda QUEM TINHA O FOCO quando um diálogo abriu (86e3eq9uy), para o
 * `DialogContent`/`AlertDialogContent` devolverem o foco a ele ao fechar.
 *
 * Por que um componente e não um `useState` no próprio Content: o wrapper da
 * casa monta com o diálogo FECHADO (o Radix só gateia o miolo pela `Presence`),
 * então "primeiro render do Content" é o carregamento da página, com o foco no
 * `body`. Este componente é FILHO do `DialogPrimitive.Content`: só existe com
 * o diálogo aberto, e o seu primeiro render acontece antes do commit — antes,
 * portanto, de um campo com `autoFocus` puxar o foco para dentro (o que também
 * é o motivo de `onOpenAutoFocus` não servir: o Radix nem o dispara nesse caso).
 *
 * O Radix, sozinho, devolve o foco só ao próprio `DialogTrigger`; diálogo aberto
 * por estado (item de menu, botão fora do Root) não tem trigger e o foco caía
 * no vazio. Com trigger, o abridor É o trigger: nada muda.
 */
export function OpenerCapture({ target }: { target: React.MutableRefObject<HTMLElement | null> }) {
  React.useState(() => {
    target.current =
      typeof document !== 'undefined' && document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    return null;
  });
  return null;
}

/** O `onCloseAutoFocus` que devolve o foco ao abridor guardado, se ainda existir. */
export function returnFocusToOpener(
  target: React.MutableRefObject<HTMLElement | null>,
  event: Event,
): void {
  if (event.defaultPrevented) return;
  const opener = target.current;
  if (opener?.isConnected) {
    event.preventDefault();
    opener.focus();
  }
}
