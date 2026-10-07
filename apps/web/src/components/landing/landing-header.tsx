/**
 * Header fixo da landing e do aviso de privacidade (86e3fr9vz).
 *
 * Transparente no topo; ao rolar, `reveal.tsx` liga `data-scrolled` e o CSS dá fundo
 * sólido, borda e sombra. Um primário por bloco: "Entrar em contato" é o primário,
 * "Entrar" é o secundário (`outline`). Abaixo de `sm` o rótulo "Hologram" some e
 * fica a logomark, como no header do app.
 *
 * "Entrar em contato" aponta para `/#contato`: na landing é navegação de âncora na
 * mesma página; na privacidade, volta à landing já no formulário.
 */
import Link from 'next/link';

import { BrandMark } from '@/components/shared/brand-mark';
import { Button } from '@/components/ui/button';

import { CONTACT_ANCHOR, header } from './content';

export function LandingHeader() {
  return (
    <header data-lp-header className="lp-header fixed inset-x-0 top-0 z-40">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-3 px-4 sm:px-6">
        <Link
          href="/"
          className="focus-visible:ring-ring flex min-w-0 items-center gap-2 rounded-md focus-visible:outline-none focus-visible:ring-2"
          aria-label={`${header.brand}, página inicial`}
        >
          <BrandMark className="text-logo h-7 shrink-0" />
          <span className="hidden text-lg font-semibold tracking-tight sm:inline">
            {header.brand}
          </span>
        </Link>
        <nav aria-label={header.navLabel} className="flex shrink-0 items-center gap-2 sm:gap-3">
          <Button asChild variant="outline" size="sm">
            <Link href="/login">{header.signIn}</Link>
          </Button>
          <Button asChild size="sm" variant="brand" className="lp-glow-button">
            <a href={`/#${CONTACT_ANCHOR}`}>{header.contact}</a>
          </Button>
        </nav>
      </div>
    </header>
  );
}
