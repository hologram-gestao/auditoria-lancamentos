/**
 * Para quem (86e3fr9vz): três cards curtos, um por público. A vinheta ao lado do
 * título (86e3gwzj0) liga três avatares fictícios a um ponto só; no hover do card, a
 * pastilha do ícone cresce um pouco (`.lp-audience` no `landing.css`).
 */
import { audience } from './content';
import { LandingIcon } from './landing-icon';
import { LandingImage } from './landing-image';
import { LandingSection, revealDelay } from './landing-section';
import { AudienceVignette } from './section-vignettes';

export function LandingAudience() {
  return (
    <LandingSection
      id="para-quem"
      eyebrow={audience.eyebrow}
      title={audience.title}
      aside={<AudienceVignette />}
    >
      <ul className="lp-audience grid gap-4 md:grid-cols-3 md:gap-6">
        {audience.items.map((item, index) => (
          <li
            key={item.title}
            data-reveal
            style={revealDelay(index)}
            className="lp-card bg-card rounded-xl border p-6"
          >
            <LandingIcon name={item.icon} />
            <h3 className="mt-5 text-lg font-semibold">{item.title}</h3>
            <p className="text-muted-foreground mt-2 leading-relaxed">{item.text}</p>
          </li>
        ))}
      </ul>
      <LandingImage slot="standard" className="mt-10 max-w-3xl" />
    </LandingSection>
  );
}
