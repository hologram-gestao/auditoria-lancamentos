/**
 * Domínio só por env (86e3fr9wm): sem `NEXT_PUBLIC_SITE_URL` nada muda (sem sitemap
 * no robots, sitemap vazio); com ela, URL absoluta nos dois.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import robots from '@/app/robots';
import sitemap from '@/app/sitemap';

import { siteUrl } from '../site-url';

afterEach(() => {
  vi.unstubAllEnvs();
});

describe('lib/site-url', () => {
  it('sem a variável, não há URL, sitemap nem linha de sitemap no robots', () => {
    vi.stubEnv('NEXT_PUBLIC_SITE_URL', '');
    expect(siteUrl()).toBeNull();
    expect(sitemap()).toEqual([]);
    expect(robots()).not.toHaveProperty('sitemap');
  });

  it('com a variável, sitemap e robots saem com URL absoluta', () => {
    vi.stubEnv('NEXT_PUBLIC_SITE_URL', 'https://hologramos.com.br');
    expect(siteUrl()?.origin).toBe('https://hologramos.com.br');
    expect(sitemap().map((entry) => entry.url)).toEqual([
      'https://hologramos.com.br/',
      'https://hologramos.com.br/privacidade',
    ]);
    expect(robots().sitemap).toBe('https://hologramos.com.br/sitemap.xml');
  });

  it('valor inválido estoura em vez de publicar URL errada', () => {
    vi.stubEnv('NEXT_PUBLIC_SITE_URL', 'hologramos.com.br');
    expect(() => siteUrl()).toThrow();
  });
});
