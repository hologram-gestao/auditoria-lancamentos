'use client';

/**
 * Botão "Testar conexão" — Doc §9.2 (estados):
 *   - idle    → link azul "Testar conexão" em sua própria linha
 *   - testing → spinner + "Testando..." (caller desabilita inputs/botões)
 *   - success → alert-card verde "Conexão verificada com sucesso"
 *   - failure → link "Testar conexão" + alert-card vermelho com a mensagem
 *               do backend (X + texto), permitindo nova tentativa
 *
 * Stateless: o caller mantém o estado e o ref das credenciais testadas.
 * O desacoplamento permite invalidar `success` quando o usuário edita uma
 * credencial após o teste passar (lógica do modal, não daqui).
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

const ALERT_BASE = 'flex items-start gap-2 rounded-md border px-3 py-2 text-sm';

export function TestConnectionButton({ state, disabled, onClick }: TestConnectionButtonProps) {
  if (state.kind === 'testing') {
    return (
      <p className="text-muted-foreground flex items-center gap-2 text-sm" aria-live="polite">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        Testando...
      </p>
    );
  }

  if (state.kind === 'success') {
    return (
      <div
        className={cn(
          ALERT_BASE,
          // 86e2n39hb — tokens semânticos: flipam entre os temas sozinhos.
          'border-success/30 bg-success-muted text-success',
        )}
        aria-live="polite"
      >
        <Check className="mt-0.5 h-4 w-4 flex-none" aria-hidden="true" />
        <span>Conexão verificada com sucesso</span>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div>
        <button
          type="button"
          onClick={onClick}
          disabled={disabled}
          className={cn(
            'text-sm font-medium underline-offset-4 hover:underline focus-visible:underline',
            'text-info',
            'disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:no-underline',
            'focus-visible:ring-ring rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2',
          )}
        >
          Testar conexão
        </button>
      </div>
      {state.kind === 'failure' && (
        <div
          className={cn(ALERT_BASE, 'border-destructive/30 bg-destructive-muted text-destructive')}
          aria-live="assertive"
        >
          <X className="mt-0.5 h-4 w-4 flex-none" aria-hidden="true" />
          <span>{state.message}</span>
        </div>
      )}
    </div>
  );
}
