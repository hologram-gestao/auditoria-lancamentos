/**
 * Aviso de privacidade do formulário de contato (decisão D8 do plano da landing).
 *
 * Curto e factual: quais dados, para quê, base legal, quem lê, por quanto tempo e
 * como pedir exclusão. O texto mora em `components/landing/content.ts` e a versão
 * comentada em `Docs/landing/COPY.md`.
 */
import type { Metadata } from 'next';
import Link from 'next/link';

import { privacy } from '@/components/landing/content';
import { siteUrl } from '@/lib/site-url';

const SITE_URL = siteUrl();

export const metadata: Metadata = {
  title: privacy.metaTitle,
  description: privacy.metaDescription,
  robots: { index: true, follow: true },
  // URL absoluta só com o domínio próprio: `lib/site-url.ts`.
  ...(SITE_URL ? { metadataBase: SITE_URL, alternates: { canonical: '/privacidade' } } : {}),
};

export default function PrivacyPage() {
  return (
    <article className="mx-auto max-w-3xl px-4 pb-20 pt-28 sm:px-6 sm:pt-36">
      <h1 className="text-4xl font-semibold tracking-tight">{privacy.title}</h1>
      <p className="text-muted-foreground mt-4 text-lg leading-relaxed">{privacy.intro}</p>
      <p className="text-muted-foreground mt-2 text-sm">{privacy.updated}</p>

      <div className="bg-card mt-10 divide-y rounded-xl border">
        {privacy.sections.map((section) => (
          <section key={section.title} className="p-6">
            <h2 className="text-lg font-semibold">{section.title}</h2>
            <p className="text-muted-foreground mt-2 leading-relaxed">{section.text}</p>
          </section>
        ))}
      </div>

      <p className="mt-10">
        <Link
          href="/"
          className="focus-visible:ring-ring rounded-sm font-medium underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2"
        >
          {privacy.back}
        </Link>
      </p>
    </article>
  );
}
