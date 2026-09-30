/**
 * `sitemap.xml` (86e3fr9wm): a landing e o aviso de privacidade, com URL absoluta.
 *
 * Só tem entradas quando `NEXT_PUBLIC_SITE_URL` existe (`lib/site-url.ts`): sitemap
 * com a URL `*.run.app` ensinaria o buscador o endereço errado. Sem a variável sai
 * vazio, e o `robots.txt` nem aponta para ele.
 */
import type { MetadataRoute } from 'next';

import { PUBLIC_INDEXABLE_PATHS, siteUrl } from '@/lib/site-url';

export default function sitemap(): MetadataRoute.Sitemap {
  const base = siteUrl();
  if (!base) return [];
  return PUBLIC_INDEXABLE_PATHS.map((path) => ({ url: new URL(path, base).toString() }));
}
