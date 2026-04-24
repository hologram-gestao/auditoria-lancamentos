// @ts-check

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
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'Referrer-Policy', value: 'same-origin' },
          { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
        ],
      },
    ];
  },

  experimental: {
    typedRoutes: true,
  },
};

export default nextConfig;
