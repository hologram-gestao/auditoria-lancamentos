// @ts-check

const IS_PROD = process.env.NODE_ENV === 'production';

// URL interna da API — usada pelo proxy `/api/v1/*` (rewrites abaixo). O
// browser nunca vê essa URL: chamadas saem como `/api/v1/...` (mesma origem
// do Web) e o Next reverse-proxia pra esse host server-side. Em prod, Cloud
// Run injeta como env var. Em dev local, vem de `.env.local`. Em build sem
// a var setada (CI lint), fallback pra localhost evita crash no `next build`.
const INTERNAL_API_URL = process.env.INTERNAL_API_URL ?? 'http://localhost:8000';

/**
 * Content-Security-Policy do front (P0-002).
 *
 * - `'unsafe-inline'` em `style-src` é necessário pelo Tailwind/shadcn (CSS
 *   inline gerado em runtime). Aceitável: não é vetor de XSS sem
 *   `script-src 'unsafe-inline'` (que evitamos).
 * - `'unsafe-inline'` em `script-src` é necessário para os scripts inline
 *   que o Next 14 injeta (hydration). Trocar por nonce exige refactor mais
 *   profundo (next/script nonce strategy) — fica como dívida P2.
 * - `frame-ancestors 'none'` substitui `X-Frame-Options: DENY` em browsers
 *   modernos; mantemos os dois (defesa em profundidade).
 * - `connect-src 'self'` é suficiente porque todas as chamadas pra API vão
 *   pra `/api/v1/*` (mesma origem) e são proxiadas server-side via rewrites.
 *   Browser nunca toca a URL real do backend — evita problemas de cookie
 *   cross-site (PSL em *.run.app) e fecha exfiltração via fetch externo.
 */
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
  ...(IS_PROD ? ['upgrade-insecure-requests'] : []),
].join('; ');

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // Build standalone permite imagem Docker mínima (apps/web/.next/standalone)
  output: 'standalone',
  // Transpile workspace packages (shared-types)
  transpilePackages: ['@auditoria/shared-types'],

  // Headers de segurança (complementares ao nginx/proxy em prod)
  async headers() {
    const baseHeaders = [
      { key: 'X-Content-Type-Options', value: 'nosniff' },
      { key: 'X-Frame-Options', value: 'DENY' },
      { key: 'Referrer-Policy', value: 'same-origin' },
      {
        key: 'Permissions-Policy',
        value:
          'accelerometer=(), camera=(), geolocation=(), gyroscope=(), ' +
          'magnetometer=(), microphone=(), payment=(), usb=()',
      },
      { key: 'Content-Security-Policy', value: CSP },
    ];
    if (IS_PROD) {
      baseHeaders.push({
        key: 'Strict-Transport-Security',
        value: 'max-age=31536000; includeSubDomains',
      });
    }
    return [
      {
        source: '/:path*',
        headers: baseHeaders,
      },
    ];
  },

  // Proxy reverso BFF: browser fala com Next em `/api/v1/*` (mesma origem),
  // Next encaminha pra API real server-side. Resolve o problema do cookie
  // HttpOnly cross-site quando Web e API estão em sub-domínios diferentes
  // sob a PSL (caso *.run.app sem custom domain comum).
  async rewrites() {
    return [
      {
        source: '/api/v1/:path*',
        destination: `${INTERNAL_API_URL}/api/v1/:path*`,
      },
    ];
  },

  experimental: {
    typedRoutes: true,
  },
};

export default nextConfig;
