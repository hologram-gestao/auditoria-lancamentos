/**
 * Hero da landing (86e3fr9vz): a promessa em uma frase, para quem é, os dois
 * caminhos (contato é o primário, entrar é o secundário) e a vinheta de produto.
 *
 * Aurora e grade são decorativas (`aria-hidden`) e ficam ATRÁS do conteúdo; o
 * texto está sobre `bg-background`. A entrada é escalonada por `--lp-delay`
 * (80 ms por linha) e desliga sob movimento reduzido. O LCP é o título, texto.
 *
 * Refino de 30/09 (86e3gr6k5), um destaque só: a palavra `titleHighlight` em
 * gradiente (`.lp-gradient-text`). O sobretítulo vira pílula sólida com um ponto
 * pulsante, e os chips abaixo dos botões dizem o que entra e o que sai.
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
          {/* Pílula SÓLIDA (`bg-card`): o texto não fica sobre a aurora. `text-foreground`
              porque em `muted-foreground` o sobretítulo media 4,16:1 sobre o pico da
              aurora, antes da pílula. O ponto é irmão do `<p>`, fora da caixa que o e2e
              "texto sobre a aurora" mede. */}
          <div
            className="lp-fade-up lp-pill bg-card inline-flex max-w-full items-center gap-2.5 rounded-2xl border px-3.5 py-1.5 lg:rounded-full"
            style={delay(0)}
          >
            <span aria-hidden="true" className="lp-pulse-dot" />
            <p className="text-foreground min-w-0 text-sm font-medium">{hero.eyebrow}</p>
          </div>
          <h1
            id="hero-title"
            className="lp-fade-up mt-4 text-4xl font-semibold leading-[1.05] tracking-tight sm:text-5xl lg:text-6xl"
            style={delay(80)}
          >
            {hero.titleBefore}
            <span className="lp-gradient-text">{hero.titleHighlight}</span>
            {hero.titleAfter}
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
            <Button asChild size="lg" variant="brand" className="lp-glow-button">
              <a href={`#${CONTACT_ANCHOR}`}>{hero.primary}</a>
            </Button>
            <Button asChild size="lg" variant="outline">
              <Link href="/login">{hero.secondary}</Link>
            </Button>
          </div>
          <ul
            aria-label={hero.chipsLabel}
            className="lp-fade-up mt-6 flex flex-wrap gap-2"
            style={delay(320)}
          >
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

        <div className="lp-fade-up min-w-0" style={delay(400)}>
          <ProductVignette />
        </div>
      </div>
    </section>
  );
}
