'use client';

/**
 * Error boundary da rota "Glossário" do cliente.
 *
 * Regra do design-system: **nunca** vazar o erro interno do framework. O
 * `error.message` do Next em produção é uma mensagem genérica com um digest, e
 * em dev é o stack — nos dois casos é ruído para quem usa. A tela mostra uma
 * frase em português e dois caminhos: tentar de novo ou voltar.
 */

import Link from 'next/link';
import { useEffect } from 'react';

import { Button } from '@/components/ui/button';

export default function GlossaryError({
  error,
  reset,
  params,
}: {
  error: Error & { digest?: string };
  reset: () => void;
  params?: { clientId?: string };
}) {
  useEffect(() => {
    // O detalhe fica no console (para quem depura), não na tela.
    console.error('[clientes/glossario] erro na rota', error);
  }, [error]);

  const backHref = params?.clientId ? `/clientes/${params.clientId}` : '/clientes';

  return (
    <div
      role="alert"
      className="bg-card mx-auto flex max-w-xl flex-col items-center gap-4 rounded-lg border p-8 text-center"
    >
      <div className="space-y-1.5">
        {/* `h2` pelo mesmo motivo do `AccessDenied`: o shell do cliente
            já rende o `h1`. */}
        <h2 className="text-lg font-semibold">Não foi possível carregar o glossário</h2>
        <p className="text-muted-foreground text-sm">
          Algo deu errado ao abrir esta página. Tente novamente em instantes.
        </p>
      </div>
      <div className="flex flex-wrap items-center justify-center gap-2">
        <Button type="button" onClick={reset}>
          Tentar novamente
        </Button>
        <Button asChild variant="outline">
          <Link href={backHref}>Voltar para o cliente</Link>
        </Button>
      </div>
    </div>
  );
}
