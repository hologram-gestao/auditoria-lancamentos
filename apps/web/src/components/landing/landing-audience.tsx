/**
 * Para quem (86e3fr9vz): três cards curtos, um por público. No hover do card, a
 * pastilha do ícone cresce um pouco (`.lp-audience` no `landing.css`). Sem vinheta
 * ao lado do título desde a 86e3h0xcr.
 */
import { Card } from '@/components/ui/card';

import { audience } from './content';
import { LandingIcon } from './landing-icon';
import { LandingImage } from './landing-image';
import { LandingSection, revealDelay } from './landing-section';

export function LandingAudience() {
  return (
    <LandingSection id="para-quem" eyebrow={audience.eyebrow} title={audience.title}>
      <ul className="lp-audience grid gap-4 md:grid-cols-3 md:gap-6">
        {audience.items.map((item, index) => (
          <Card
            asChild
            variant="elevated"
            key={item.title}
            data-reveal
            style={revealDelay(index)}
            className="rounded-xl p-6"
          >
            <li>
              <LandingIcon name={item.icon} />
              <h3 className="mt-5 text-lg font-semibold">{item.title}</h3>
              <p className="text-muted-foreground mt-2 leading-relaxed">{item.text}</p>
            </li>
          </Card>
        ))}
      </ul>
      <LandingImage slot="standard" className="mt-10 max-w-3xl" />
    </LandingSection>
  );
}
