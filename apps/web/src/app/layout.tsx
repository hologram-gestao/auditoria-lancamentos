import type { Metadata } from 'next';

import { Providers } from './providers';

import './globals.css';

export const metadata: Metadata = {
  title: 'Sistema de Auditoria de Lançamentos',
  description: 'Plataforma interna da Hologram Gestão para conciliação bancária.',
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
