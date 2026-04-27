'use client';

/**
 * Botão "Testar conexão" — Doc §9.2 (estados):
 *   - idle    → texto azul clicável "Testar conexão"
 *   - testing → spinner + "Testando..." (caller desabilita inputs/botões)
 *   - success → ✓ verde "Conexão verificada com sucesso" (persiste)
 *   - failure → X vermelho + mensagem do backend; volta a `idle` se o usuário
 *               alterar as credenciais (controlado pelo caller via `state`).
 *
 * Stateless: o caller mantém o estado e a referência das credenciais testadas.
 * Esse desacoplamento permite que o modal invalide `success` quando o usuário
 * editar uma credencial após o teste passar.
 */

import { Check, Loader2, X } from 'lucide-react';

import { cn } from '@/lib/utils';

export type TestConnectionState =
  | { kind: 'idle' }
  | { kind: 'testing' }
  | { kind: 'success' }
  | { kind: 'failure'; message: string };

interface TestConnectionButtonProps {
  state: TestConnectionState;
  disabled: boolean;
  onClick: () => void;
}

export function TestConnectionButton({ state, disabled, onClick }: TestConnectionButtonProps) {
  if (state.kind === 'testing') {
    return (
      <p
        className="text-muted-foreground inline-flex items-center gap-2 text-sm"
        aria-live="polite"
      >
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Testando...
      </p>
    );
  }

  if (state.kind === 'success') {
    return (
      <p
        className="inline-flex items-center gap-2 text-sm text-emerald-700 dark:text-emerald-400"
        aria-live="polite"
      >
        <Check className="h-4 w-4" aria-hidden="true" />
        Conexão verificada com sucesso
      </p>
    );
  }

  return (
    <div className="space-y-1">
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        className={cn(
          'text-sm font-medium underline-offset-4 hover:underline focus-visible:underline',
          'text-blue-600 dark:text-blue-400',
          'disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:no-underline',
          'focus-visible:ring-ring rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2',
        )}
      >
        Testar conexão
      </button>
      {state.kind === 'failure' && (
        <p
          className="text-destructive inline-flex items-center gap-2 text-sm"
          aria-live="assertive"
        >
          <X className="h-4 w-4" aria-hidden="true" />
          {state.message}
        </p>
      )}
    </div>
  );
}
