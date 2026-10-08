'use client';

/**
 * Abas do tour (86e3gqfkf): o ÚNICO estado do tour, e o único JS dele. Sem fetch.
 *
 * Troca automática a cada 6 s, e só enquanto a seção está na tela, ninguém mexeu e
 * o ponteiro não está sobre a tela mostrada. Para de vez no primeiro clique ou foco
 * nas abas ou no painel, e nem começa sob `prefers-reduced-motion`.
 *
 * Quem EXPLICA a troca na tela é uma linha de 2 px na base da aba ativa (86e3h0xcr):
 * decorativa (`aria-hidden`), cresce de 0 a 100 % nos 6 s do relógio enquanto ele está
 * armado e fica cheia quando ele para (ponteiro em cima, fora da tela, parada). A `key`
 * muda a cada armada, então a animação recomeça junto com o `setTimeout`.
 *
 * Como a troca é conteúdo que se atualiza sozinho por mais de 5 s, existe um botão
 * para pausar e retomar (WCAG 2.2.2), no molde do carrossel do APG: só ícone, com o
 * nome acessível dizendo o que faz, à direita da faixa de abas, e só quando há troca a
 * controlar. Sob movimento reduzido não há troca, nem linha, nem botão. Leitor de tela
 * não ouve a troca: o painel não é `aria-live`.
 *
 * Os painéis ficam montados (`forceMount`: a imagem da próxima aba não some e volta)
 * e o inativo leva `hidden`, mais o `data-[state=inactive]:hidden` do `TabsContent`;
 * o ativo entra com fade de 250 ms (`.lp-tour-panel` no `landing.css`).
 *
 * A imagem é `unoptimized` de propósito: o web roda em `output: 'standalone'` sem
 * `sharp`, e nesse modo o otimizador do Next 14 responde 500. Os PNGs já saem
 * otimizados em `public/landing/tour/` (menos de 100 KB cada).
 */
import { CheckCircle2, Pause, Play } from 'lucide-react';
import Image from 'next/image';
import { useCallback, useEffect, useRef, useState } from 'react';

import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

export const TOUR_AUTO_ADVANCE_MS = 6000;

export interface TourItemView {
  id: string;
  tab: string;
  frameTitle: string;
  alt: string;
  text: string;
  bullets: string[];
  image: { src: string; width: number; height: number };
}

export interface TourLabels {
  tabs: string;
  pauseLabel: string;
  resumeLabel: string;
}

function prefersReducedMotion(): boolean {
  return (
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
}

export function LandingTourTabs({ items, labels }: { items: TourItemView[]; labels: TourLabels }) {
  const [value, setValue] = useState(items[0]?.id ?? '');
  // `null` até o efeito ler a mídia: no servidor não se sabe, e o botão de pausa só
  // aparece depois da hidratação, quando há troca a controlar.
  const [autoAllowed, setAutoAllowed] = useState<boolean | null>(null);
  const [stopped, setStopped] = useState(false);
  const [visible, setVisible] = useState(false);
  const [hovered, setHovered] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setAutoAllowed(!prefersReducedMotion());
    const root = rootRef.current;
    if (!root || !('IntersectionObserver' in window)) {
      setVisible(true);
      return undefined;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) setVisible(entry.isIntersecting);
      },
      { threshold: 0.35 },
    );
    observer.observe(root);
    return () => observer.disconnect();
  }, []);

  const running = autoAllowed === true && !stopped;
  /** O relógio da troca está armado agora (o e2e espera por isto antes de avançar o tempo). */
  const armed = running && visible && !hovered && items.length > 1;

  useEffect(() => {
    if (!armed) return undefined;
    const timer = window.setTimeout(() => {
      const index = items.findIndex((item) => item.id === value);
      setValue(items[(index + 1) % items.length]?.id ?? value);
    }, TOUR_AUTO_ADVANCE_MS);
    return () => window.clearTimeout(timer);
  }, [armed, value, items]);

  /** Primeiro clique ou foco nas abas ou no painel: a pessoa assumiu, a troca para. */
  const stop = useCallback(() => setStopped(true), []);

  return (
    <div ref={rootRef} data-autoplay={armed ? 'on' : 'off'}>
      <Tabs value={value} onValueChange={setValue} className="grid gap-6">
        <div className="flex items-start justify-between gap-3">
          <TabsList
            aria-label={labels.tabs}
            className="h-auto min-w-0 flex-wrap justify-start"
            onPointerDown={stop}
            onFocus={stop}
          >
            {items.map((item) => (
              <TabsTrigger key={item.id} value={item.id} className="relative overflow-hidden">
                {item.tab}
                {autoAllowed && item.id === value && (
                  <span
                    key={`${item.id}-${armed ? 'on' : 'off'}`}
                    aria-hidden="true"
                    data-lp-tab-progress={armed ? 'running' : 'full'}
                    className="lp-tab-progress"
                    style={{ '--lp-tab-ms': `${TOUR_AUTO_ADVANCE_MS}ms` } as React.CSSProperties}
                  />
                )}
              </TabsTrigger>
            ))}
          </TabsList>
          {autoAllowed && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="text-muted-foreground shrink-0"
              aria-label={stopped ? labels.resumeLabel : labels.pauseLabel}
              onClick={() => setStopped((current) => !current)}
            >
              {stopped ? <Play aria-hidden="true" /> : <Pause aria-hidden="true" />}
            </Button>
          )}
        </div>

        {items.map((item) => (
          <TabsContent
            key={item.id}
            value={item.id}
            forceMount
            // Com `forceMount` o Radix passa `hidden={false}` a todo painel; o inativo
            // sai da árvore pelo atributo, não só pela classe.
            hidden={item.id !== value}
            className="lp-tour-panel mt-0 rounded-xl"
            onPointerDown={stop}
            onFocus={stop}
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
          >
            <div className="grid items-center gap-8 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] lg:gap-12">
              <div className="min-w-0">
                <p className="text-lg leading-relaxed">{item.text}</p>
                <ul className="mt-5 grid gap-3">
                  {item.bullets.map((bullet) => (
                    <li key={bullet} className="flex gap-3">
                      <CheckCircle2
                        aria-hidden="true"
                        className="lp-brand-text mt-0.5 h-5 w-5 shrink-0"
                      />
                      <span className="text-muted-foreground leading-relaxed">{bullet}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <figure className="lp-tilt min-w-0">
                <div className="lp-tilt__card lp-frame edge-gradient bg-card relative rounded-xl border">
                  {/* Barra de navegador de mentira: decorativa, o `alt` diz o que é a tela. */}
                  <div
                    aria-hidden="true"
                    className="bg-muted flex items-center gap-3 rounded-t-xl border-b px-4 py-2.5"
                  >
                    <span className="flex shrink-0 gap-1.5">
                      <span className="bg-destructive h-2.5 w-2.5 rounded-full" />
                      <span className="bg-warning h-2.5 w-2.5 rounded-full" />
                      <span className="bg-success h-2.5 w-2.5 rounded-full" />
                    </span>
                    <span
                      data-lp-frame-title
                      className="text-muted-foreground min-w-0 truncate text-xs font-medium"
                    >
                      {item.frameTitle}
                    </span>
                  </div>
                  <div className="overflow-hidden rounded-b-xl">
                    <Image
                      src={item.image.src}
                      width={item.image.width}
                      height={item.image.height}
                      alt={item.alt}
                      unoptimized
                      className="block h-auto w-full"
                    />
                  </div>
                </div>
              </figure>
            </div>
          </TabsContent>
        ))}
      </Tabs>
    </div>
  );
}
