import type { Metadata } from 'next';

import { PRODUCT_TAGLINE, PRODUCT_TITLE } from '@/lib/brand';

import { Providers } from './providers';

import './globals.css';

export const metadata: Metadata = {
  title: PRODUCT_TITLE,
  description: PRODUCT_TAGLINE,
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="pt-BR" suppressHydrationWarning>
      <body className="bg-background min-h-screen font-sans antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
