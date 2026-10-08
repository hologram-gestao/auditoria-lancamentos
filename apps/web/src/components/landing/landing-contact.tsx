/** Bloco do formulário de contato (86e3fr9vz): âncora `#contato` da página. */
import { Card } from '@/components/ui/card';

import { ContactForm } from './contact-form';
import { CONTACT_ANCHOR, contact } from './content';
import { LandingEyebrow } from './landing-section';

export function LandingContact() {
  return (
    <section
      id={CONTACT_ANCHOR}
      aria-labelledby={`${CONTACT_ANCHOR}-title`}
      className="lp-section relative isolate py-16 sm:py-24"
    >
      <div aria-hidden="true" className="lp-section-glow -z-10" />
      <div className="mx-auto grid max-w-6xl gap-10 px-4 sm:px-6 lg:grid-cols-[1fr_1.4fr] lg:gap-16">
        <div data-reveal>
          <LandingEyebrow>{contact.eyebrow}</LandingEyebrow>
          <h2
            id={`${CONTACT_ANCHOR}-title`}
            className="mt-3 text-3xl font-semibold leading-tight tracking-tight sm:text-4xl"
          >
            {contact.title}
          </h2>
          <p className="text-muted-foreground mt-4 max-w-md text-lg leading-relaxed">
            {contact.lead}
          </p>
        </div>
        <Card variant="elevated" data-reveal className="rounded-xl p-6 sm:p-8">
          <ContactForm />
        </Card>
      </div>
    </section>
  );
}
