/**
 * Segurança e privacidade (86e3fr9vz): quatro fatos, como eles são, e o link para
 * o aviso de privacidade. Nada aqui afirma retenção ou treinamento do provedor de
 * IA (86e3anx75 segue aberta). Ao lado do título, a vinheta do cadeado (86e3gwzj0).
 */
import Link from 'next/link';

import { Card } from '@/components/ui/card';

import { security } from './content';
import { LandingIcon } from './landing-icon';
import { LandingSection, revealDelay } from './landing-section';
import { ManualDownload } from './manual-download';
import { SecurityVignette } from './section-vignettes';

export function LandingSecurity() {
  return (
    <LandingSection
      id="seguranca"
      eyebrow={security.eyebrow}
      title={security.title}
      aside={<SecurityVignette />}
    >
      <ul className="grid gap-4 sm:grid-cols-2 lg:gap-6">
        {security.items.map((item, index) => (
          <Card
            asChild
            variant="elevated"
            key={item.title}
            data-reveal
            style={revealDelay(index)}
            className="flex gap-4 rounded-xl p-6"
          >
            <li>
              <LandingIcon name={item.icon} />
              <div className="min-w-0">
                <h3 className="font-semibold">{item.title}</h3>
                <p className="text-muted-foreground mt-2 leading-relaxed">{item.text}</p>
              </div>
            </li>
          </Card>
        ))}
      </ul>
      <div
        data-reveal
        className="mt-8 flex flex-col gap-6 sm:flex-row sm:items-center sm:justify-between"
      >
        <Link
          href="/privacidade"
          className="focus-visible:ring-ring self-start rounded-sm font-medium underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 sm:self-auto"
        >
          {security.privacyLink}
        </Link>
        <ManualDownload id="manual-seguranca" />
      </div>
    </LandingSection>
  );
}
