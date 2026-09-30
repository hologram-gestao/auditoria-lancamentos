/**
 * Painel de marca da tela de login (86e3h1h75): a coluna esquerda de `lg` para cima,
 * a ponte visual com a landing. A mesma aurora do hero em intensidade baixa
 * (`.lp-aurora--soft`), a logomark grande, a frase do hero com a palavra em gradiente e
 * os chips de formatos. Abaixo de `lg` ele não existe (`hidden`): o celular vê só o
 * card, que já leva logomark e título.
 *
 * Server component: nada aqui tem estado. A frase é um `<p>`, não um título: o `h1` da
 * página é o nome do produto, dentro do card.
 */
import { BrandMark } from '@/components/shared/brand-mark';

import { hero, login } from './content';

export function SignInBrandPanel() {
  return (
    <aside
      aria-label={login.panelLabel}
      className="relative isolate hidden overflow-hidden border-r lg:flex lg:flex-col lg:justify-center lg:px-12 xl:px-20"
    >
      <div aria-hidden="true" className="lp-aurora lp-aurora--soft -z-10">
        <div className="lp-aurora__blob lp-aurora__blob--1" />
        <div className="lp-aurora__blob lp-aurora__blob--2" />
        <div className="lp-aurora__blob lp-aurora__blob--3" />
        <div className="lp-grid" />
      </div>

      <div className="max-w-md">
        <BrandMark className="text-primary h-12" />
        <p
          data-testid="sign-in-panel-title"
          className="mt-10 text-3xl font-semibold leading-tight tracking-tight"
        >
          {hero.titleBefore}
          <span className="lp-gradient-text">{hero.titleHighlight}</span>
          {hero.titleAfter}
        </p>
        <ul aria-label={hero.chipsLabel} className="mt-8 flex flex-wrap gap-2">
          {hero.chips.map((chip) => (
            <li
              key={chip}
              className="bg-card text-muted-foreground rounded-full border px-3 py-1 text-xs font-medium"
            >
              {chip}
            </li>
          ))}
        </ul>
      </div>
    </aside>
  );
}
