/**
 * `robots.txt` (86e3fr9vz): só a landing e o aviso de privacidade são públicos e
 * indexáveis; o resto do sistema é interno. O `noindex` do layout raiz continua
 * valendo como segunda camada nas páginas do app.
 */
import type { MetadataRoute } from 'next';

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: '*',
        allow: ['/$', '/privacidade'],
        disallow: ['/'],
      },
    ],
  };
}
