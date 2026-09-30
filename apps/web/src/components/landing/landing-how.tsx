/**
 * Como funciona (86e3fr9vz): os quatro passos do fluxo núcleo, ligados por uma
 * linha que se desenha quando o bloco entra na tela (horizontal em `lg`, vertical
 * abaixo). A linha é decorativa; a ordem está na lista numerada.
 */
import { how } from './content';
import { LandingImage } from './landing-image';
import { LandingSection, revealDelay } from './landing-section';

export function LandingHow() {
  return (
    <LandingSection id="como-funciona" title={how.title}>
      <div data-reveal className="relative">
        <div
          aria-hidden="true"
          className="lp-line absolute bottom-6 left-5 top-6 w-px lg:bottom-auto lg:left-6 lg:right-6 lg:top-5 lg:h-px lg:w-auto"
        />
        <ol className="relative grid gap-8 lg:grid-cols-4 lg:gap-6">
          {how.steps.map((step, index) => (
            <li
              key={step.title}
              data-reveal
              style={revealDelay(index + 1)}
              className="flex gap-4 lg:flex-col"
            >
              <span
                aria-hidden="true"
                className="bg-card text-foreground flex h-10 w-10 shrink-0 items-center justify-center rounded-full border text-sm font-semibold tabular-nums"
              >
                {index + 1}
              </span>
              <div className="min-w-0">
                <h3 className="text-lg font-semibold">{step.title}</h3>
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
