'use client';

/**
 * Degradação amigável de rota sem permissão (Sprint 5 / R4 · FRONT 05.7).
 *
 * Entrar por deep link numa rota que o papel não alcança tem que dar NISTO —
 * mensagem em português + caminho de volta — e nunca tela branca, stack trace do
 * framework, ou (pior) dado do outro tenant renderizado a partir de uma resposta
 * 403/404.
 *
 * **Não é barreira de segurança.** Quem nega é o backend; este componente só
 * evita que a pessoa fique olhando para o vazio. Por isso ele não recebe nem
 * mostra nada do recurso negado: só o motivo genérico.
 */

import { ShieldAlert } from 'lucide-react';
import Link from 'next/link';

import { Button } from '@/components/ui/button';

interface AccessDeniedProps {
  /** Título curto; default serve para a maioria das rotas. */
  title?: string;
  /** Frase em PT-BR explicando o bloqueio SEM citar o recurso alheio. */
  message?: string;
  /** Para onde o "voltar" leva. */
  backHref: string;
  backLabel: string;
}

export function AccessDenied({
  title = 'Você não tem acesso a este recurso',
  message = 'Seu perfil não permite ver esta página. Se você precisa deste acesso, fale com o responsável pela sua conta.',
  backHref,
  backLabel,
}: AccessDeniedProps) {
  return (
    <div
      role="alert"
      className="bg-card mx-auto flex max-w-xl flex-col items-center gap-4 rounded-lg border p-8 text-center"
    >
      <ShieldAlert className="text-muted-foreground h-8 w-8" aria-hidden="true" />
      <div className="space-y-1.5">
        {/* `h2`: estas telas vivem DENTRO do shell do cliente, que já
            tem o `h1` com o nome do cliente — um segundo `h1` quebraria a
            ordem de cabeçalhos da página. */}
        <h2 className="text-lg font-semibold">{title}</h2>
        <p className="text-muted-foreground text-sm">{message}</p>
      </div>
      <Button asChild variant="outline">
        <Link href={backHref}>{backLabel}</Link>
      </Button>
    </div>
  );
}
