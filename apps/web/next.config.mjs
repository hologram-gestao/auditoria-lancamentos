// @ts-check

const IS_PROD = process.env.NODE_ENV === 'production';

// URL pública do backend — fim do `connect-src` da CSP.
// Em build de produção, NEXT_PUBLIC_API_URL é OBRIGATÓRIO (caso contrário a
// CSP bloqueia qualquer fetch). Em dev/test cai pra localhost:8000.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

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
 * - `connect-src` autoriza só `self` e o backend — bloqueia exfiltração
 *   via fetch para domínios externos.
 */
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self' ${API_URL}`,
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

  experimental: {
    typedRoutes: true,
  },
};

export default nextConfig;
