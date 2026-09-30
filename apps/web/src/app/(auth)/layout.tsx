/**
 * Layout da tela de login. Sem sidebar/menu — apenas centraliza o conteúdo.
 * As outras rotas públicas (landing e aviso de privacidade) têm layout próprio,
 * no grupo `(public)`.
 */
export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return (
    <main className="bg-muted/40 flex min-h-screen items-center justify-center px-4 py-12">
      {children}
    </main>
  );
}
