/**
 * `robots.txt` (86e3fr9vz): só a landing, o aviso de privacidade e o manual em PDF
 * (86e3gr6k5) são públicos e indexáveis; o resto do sistema é interno. O `noindex` do
 * layout raiz continua valendo como segunda camada nas páginas do app. O `sitemap` só
 * entra quando o domínio próprio existe (`NEXT_PUBLIC_SITE_URL`, 86e3fr9wm).
 */
import type { MetadataRoute } from 'next';

import { manual } from '@/components/landing/content';
import { siteUrl } from '@/lib/site-url';

export default function robots(): MetadataRoute.Robots {
  const base = siteUrl();
  return {
    rules: [
      {
        userAgent: '*',
        allow: ['/$', '/privacidade', manual.href],
        disallow: ['/'],
      },
    ],
    ...(base ? { sitemap: new URL('/sitemap.xml', base).toString() } : {}),
  };
}
