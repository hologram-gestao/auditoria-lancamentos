/**
 * Como funciona (86e3fr9vz): os quatro passos do fluxo núcleo, ligados por uma
 * linha que se desenha quando o bloco entra na tela (horizontal em `lg`, vertical
 * abaixo). A linha é decorativa; a ordem está na lista numerada.
 *
 * Refino de 30/09 (86e3gr6k5): grade sutil atrás do bloco e o número de cada passo
 * numa pastilha com o gradiente da marca (o mesmo da `.lp-icon`).
 *
 * Vivo (86e3gwzj0): `data-lp-stepper` entrega o bloco ao `reveal.tsx`, que acende um
 * passo por vez (pastilha com o brilho da marca, título em `text-foreground`, os
 * outros em `muted`) e estende a linha de progresso até ele, em loop de 12 s, só
 * enquanto o bloco está na tela. Sem JS ou sob movimento reduzido, todos os passos
 * ficam acesos. O dígito mora num `span` próprio para o e2e medir o contraste dele
 * contra a pastilha acesa.
 */
import { how } from './content';
import { LandingImage } from './landing-image';
import { LandingSection, revealDelay } from './landing-section';

export function LandingHow() {
  return (
    <LandingSection id="como-funciona" eyebrow={how.eyebrow} title={how.title} backdrop="grid">
      <div data-reveal data-lp-stepper className="relative">
        <div
          aria-hidden="true"
          data-lp-step-line
          className="lp-line absolute bottom-6 left-5 top-6 w-px lg:bottom-auto lg:left-6 lg:right-6 lg:top-5 lg:h-px lg:w-auto"
        >
          <div className="lp-line__progress" />
        </div>
        <ol className="relative grid gap-8 lg:grid-cols-4 lg:gap-6">
          {how.steps.map((step, index) => (
            <li
              key={step.title}
              data-reveal
              data-lp-step
              style={revealDelay(index + 1)}
              className="flex gap-4 lg:flex-col"
            >
              <span
                aria-hidden="true"
                data-lp-step-pill
                className="lp-icon lp-step-number text-foreground flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-sm font-semibold tabular-nums"
              >
                <span data-lp-step-digit>{index + 1}</span>
              </span>
              <div className="min-w-0">
                <h3 className="lp-step-title text-lg font-semibold">{step.title}</h3>
                <p className="text-muted-foreground mt-2 leading-relaxed">{step.text}</p>
              </div>
            </li>
          ))}
        </ol>
      </div>
      <LandingImage slot="wide" className="mt-12" />
    </LandingSection>
  );
}
