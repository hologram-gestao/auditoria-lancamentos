/**
 * URL pública do site (86e3fr9wm), só por env: `NEXT_PUBLIC_SITE_URL`.
 *
 * Hoje ela NÃO existe: o domínio `hologramos.com.br` ainda não foi registrado nem
 * aponta para o Cloud Run. Sem ela, a landing sai sem `metadataBase`, sem canônica e
 * sem `sitemap` no `robots.txt`, exatamente como antes. Quando o domínio apontar, a
 * variável entra como build-arg do `Dockerfile.web` (roteiro em
 * `Docs/landing/DOMINIO_E_NOME.md`); é `NEXT_PUBLIC_`, então vale o valor do BUILD.
 *
 * Valor inválido estoura no build (`new URL`): URL canônica errada publicada é pior
 * que um build vermelho.
 */
export function siteUrl(): URL | null {
  const raw = process.env['NEXT_PUBLIC_SITE_URL']?.trim();
  return raw ? new URL(raw) : null;
}

/** Caminhos públicos e indexáveis: os mesmos que o `robots.txt` libera. */
export const PUBLIC_INDEXABLE_PATHS = ['/', '/privacidade'] as const;
