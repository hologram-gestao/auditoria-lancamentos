/**
 * Dores e respostas (86e3fr9vz): quatro pares, do mais caro ao mais barato
 * (`Docs/landing/COPY.md` tem a fonte de cada dor e a evidência de cada resposta).
 */
import { ArrowRight } from 'lucide-react';

import { pains } from './content';
import { LandingSection, revealDelay } from './landing-section';

export function LandingPains() {
  return (
    <LandingSection id="dores" eyebrow={pains.eyebrow} title={pains.title}>
      <ol className="grid gap-4 lg:gap-6">
        {pains.items.map((item, index) => (
          <li
            key={item.pain}
            data-reveal
            style={revealDelay(index)}
            className="lp-card bg-card grid gap-4 rounded-xl border p-6 md:grid-cols-[1fr_auto_1fr] md:items-start md:gap-8"
          >
            <div>
              <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">
                {pains.painLabel}
              </p>
              <p className="mt-2 leading-relaxed">{item.pain}</p>
            </div>
            <ArrowRight
              aria-hidden="true"
              className="text-muted-foreground hidden h-5 w-5 md:mt-7 md:block"
            />
            <div>
              <p className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">
                {pains.answerLabel}
              </p>
              <p className="mt-2 leading-relaxed">{item.answer}</p>
            </div>
          </li>
        ))}
      </ol>
    </LandingSection>
  );
}
