/**
 * Vinheta de produto do hero (86e3fr9vz): um card de conciliação com dado FICTÍCIO
 * em que os selos passam de "Pendente" para "Conciliado", um de cada vez, em loop.
 * É a demonstração do produto sem vídeo e sem screenshot.
 *
 * Tudo é CSS (`landing.css`): os dois selos de cada linha ocupam a mesma célula e
 * trocam por `visibility` em degraus, então nunca há cor misturada no meio da troca.
 * Sob `prefers-reduced-motion`, a vinheta fica no estado final (tudo conciliado).
 *
 * Para leitor de tela a vinheta é UMA figura com nome ("exemplo ilustrativo"); o
 * miolo é `aria-hidden`, porque linhas de um cliente inventado não são conteúdo.
 *
 * Refino de 30/09 (86e3gr6k5): um mini-card sobre o canto inferior esquerdo (uma
 * anomalia apontada), FORA do card inclinado, flutuando 6 px em 6 s. Dá profundidade
 * sem mexer no card principal; parado sob movimento reduzido. Superfície sólida.
 */
import { AlertTriangle, CheckCircle2 } from 'lucide-react';

import { vignette } from './content';

/** Intervalo entre a troca de uma linha e a da seguinte. */
const FLIP_STEP_MS = 1200;

export function ProductVignette() {
  return (
    <figure aria-label={vignette.label} className="lp-tilt relative w-full">
      <div aria-hidden="true" className="lp-tilt__card bg-card rounded-xl border p-5 sm:p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="text-sm font-semibold">{vignette.title}</p>
            <p className="text-muted-foreground mt-0.5 text-xs">{vignette.subtitle}</p>
          </div>
          <span className="bg-muted text-muted-foreground shrink-0 rounded-full px-2 py-0.5 text-xs font-medium">
            {vignette.status}
          </span>
        </div>

        <div className="bg-muted mt-4 h-1 overflow-hidden rounded-full">
          <div className="lp-progress bg-success h-full w-full rounded-full" />
        </div>

        <ul className="mt-4 divide-y">
          {vignette.rows.map((row, index) => (
            <li
              key={`${row.date}-${index}`}
              className="flex items-center justify-between gap-3 py-2.5 text-sm"
            >
              <div className="flex min-w-0 items-center gap-3">
                <span className="text-muted-foreground w-10 shrink-0 text-xs tabular-nums">
                  {row.date}
                </span>
                <span className="truncate">{row.description}</span>
              </div>
              <div className="flex shrink-0 items-center gap-3">
                <span className="hidden whitespace-nowrap tabular-nums sm:inline">
                  {row.amount}
                </span>
                <span
                  className="lp-badge-stack"
                  style={
                    {
                      '--lp-delay': `${index * FLIP_STEP_MS}ms`,
                    } as React.CSSProperties
                  }
                >
                  <span className="lp-badge--pending bg-warning-muted text-warning rounded-full px-2 py-0.5 text-center text-xs font-medium">
                    {vignette.pending}
                  </span>
                  <span className="lp-badge--done bg-success-muted text-success rounded-full px-2 py-0.5 text-center text-xs font-medium">
                    {vignette.done}
                  </span>
                </span>
              </div>
            </li>
          ))}
        </ul>

        <div className="mt-3 flex items-center justify-between border-t pt-3 text-sm">
          <span className="text-muted-foreground inline-flex items-center gap-1.5">
            <CheckCircle2 className="text-success h-4 w-4" />
            {vignette.footerLabel}
          </span>
          <span className="whitespace-nowrap font-semibold tabular-nums">
            {vignette.footerValue}
          </span>
        </div>
      </div>

      <div
        aria-hidden="true"
        className="lp-float bg-card absolute -bottom-16 left-3 max-w-[16rem] rounded-lg border p-3 lg:-left-8"
      >
        <span className="bg-warning-muted text-warning inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium">
          <AlertTriangle className="h-3 w-3" />
          {vignette.flagBadge}
        </span>
        <p className="mt-2 text-sm font-medium">{vignette.flagText}</p>
      </div>
    </figure>
  );
}
