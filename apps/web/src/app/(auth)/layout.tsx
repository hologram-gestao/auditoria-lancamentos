/**
 * Layout das rotas públicas (login). Sem sidebar/menu — apenas centraliza o conteúdo.
 * Doc §7.1: única rota pública do sistema.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="bg-muted/40 flex min-h-screen items-center justify-center px-4 py-12">
      {children}
    </main>
  );
}
