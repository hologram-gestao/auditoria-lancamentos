/**
 * Landing pública na raiz (`/`) — épico 86e3fr9tj.
 *
 * Sem sessão, o middleware deixa passar; com sessão, manda para `/clientes`. A
 * página é server component: o único JS dela é o formulário e o `reveal.tsx`.
 * Todo o texto vem de `components/landing/content.ts`.
 */
import type { Metadata } from 'next';

import { landingMeta } from '@/components/landing/content';
import { LandingAudience } from '@/components/landing/landing-audience';
import { LandingContact } from '@/components/landing/landing-contact';
import { LandingHero } from '@/components/landing/landing-hero';
import { LandingHow } from '@/components/landing/landing-how';
import { LandingPains } from '@/components/landing/landing-pains';
import { LandingSecurity } from '@/components/landing/landing-security';

export const metadata: Metadata = {
  title: landingMeta.title,
  description: landingMeta.description,
  openGraph: {
    title: landingMeta.title,
    description: landingMeta.description,
    locale: 'pt_BR',
    type: 'website',
  },
  // O layout raiz marca tudo como `noindex` (o app é interno); a landing é a exceção.
  robots: { index: true, follow: true },
};

export default function LandingPage() {
  return (
    <>
      <LandingHero />
      <LandingAudience />
      <LandingPains />
      <LandingHow />
      <LandingSecurity />
      <LandingContact />
    </>
  );
}
