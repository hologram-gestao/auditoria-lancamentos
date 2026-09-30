/**
 * Hero da landing (86e3fr9vz): a promessa em uma frase, para quem é, os dois
 * caminhos (contato é o primário, entrar é o secundário) e a vinheta de produto.
 *
 * Aurora e grade são decorativas (`aria-hidden`) e ficam ATRÁS do conteúdo; o
 * texto está sobre `bg-background`. A entrada é escalonada por `--lp-delay`
 * (80 ms por linha) e desliga sob movimento reduzido. O LCP é o título, texto.
 */
import Link from 'next/link';

import { Button } from '@/components/ui/button';

import { CONTACT_ANCHOR, hero } from './content';
import { ProductVignette } from './product-vignette';

function delay(ms: number): React.CSSProperties {
  return { '--lp-delay': `${ms}ms` } as React.CSSProperties;
}

export function LandingHero() {
  return (
    <section aria-labelledby="hero-title" className="relative isolate overflow-hidden">
      <div aria-hidden="true" className="lp-aurora -z-10">
        <div className="lp-aurora__blob lp-aurora__blob--1" />
        <div className="lp-aurora__blob lp-aurora__blob--2" />
        <div className="lp-aurora__blob lp-aurora__blob--3" />
        <div className="lp-grid" />
      </div>

      <div className="mx-auto grid max-w-6xl items-center gap-12 px-4 pb-20 pt-28 sm:px-6 sm:pt-36 lg:grid-cols-[1.1fr_1fr] lg:gap-16 lg:pb-28">
        <div className="min-w-0">
          <p
            className="lp-fade-up text-muted-foreground text-sm font-medium tracking-wide"
            style={delay(0)}
          >
            {hero.eyebrow}
          </p>
          <h1
            id="hero-title"
            className="lp-fade-up mt-4 text-4xl font-semibold leading-[1.05] tracking-tight sm:text-5xl lg:text-6xl"
            style={delay(80)}
          >
            {hero.title}
          </h1>
          <p
            className="lp-fade-up text-muted-foreground mt-6 max-w-xl text-base leading-relaxed sm:text-lg"
            style={delay(160)}
          >
            {hero.subtitle}
          </p>
          <div
            className="lp-fade-up mt-8 flex flex-col gap-3 sm:flex-row sm:items-center"
            style={delay(240)}
          >
            <Button asChild size="lg" className="lp-glow-button">
              <a href={`#${CONTACT_ANCHOR}`}>{hero.primary}</a>
            </Button>
            <Button asChild size="lg" variant="outline">
              <Link href="/login">{hero.secondary}</Link>
            </Button>
          </div>
        </div>

        <div className="lp-fade-up min-w-0" style={delay(400)}>
          <ProductVignette />
        </div>
      </div>
    </section>
  );
}
