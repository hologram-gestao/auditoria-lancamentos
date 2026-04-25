'use client';

/**
 * Stub mínimo de S3 — apenas valida o fluxo end-to-end (login → área autenticada).
 * S6 substitui pelo CRUD real (BACK 3.x · FRONT 3.x).
 */

import { useAuthStore } from '@/stores/auth';

export default function ClientesPage() {
  const user = useAuthStore((s) => s.user);

  if (user === null) {
    // O layout pai já redireciona; este branch só satisfaz o type-checker.
    return null;
  }

  return (
    <div className="space-y-2">
      <h1 className="text-2xl font-semibold">Clientes</h1>
      <p className="text-muted-foreground text-sm">
        Logado como <span className="text-foreground font-medium">{user.email}</span> (
        <span className="capitalize">{user.role}</span>).
      </p>
      <p className="text-muted-foreground text-sm">Lista de clientes será implementada em S6.</p>
    </div>
  );
}
