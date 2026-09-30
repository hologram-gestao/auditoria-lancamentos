/**
 * `robots.txt` (86e3fr9vz): só a landing e o aviso de privacidade são públicos e
 * indexáveis; o resto do sistema é interno. O `noindex` do layout raiz continua
 * valendo como segunda camada nas páginas do app. O `sitemap` só entra quando o
 * domínio próprio existe (`NEXT_PUBLIC_SITE_URL`, 86e3fr9wm).
 */
import type { MetadataRoute } from 'next';

import { siteUrl } from '@/lib/site-url';

export default function robots(): MetadataRoute.Robots {
  const base = siteUrl();
  return {
    rules: [
      {
        userAgent: '*',
        allow: ['/$', '/privacidade'],
        disallow: ['/'],
      },
    ],
    ...(base ? { sitemap: new URL('/sitemap.xml', base).toString() } : {}),
  };
}
