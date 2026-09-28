'use client';

/**
 * Seção "Mapeamento de colunas" da aba Origem por arquivo (Sprint 14 — FRONT
 * 14.5 / R1 · R5).
 *
 * Três estados, e o que cada papel vê:
 *   - sem mapeamento → estado vazio com "Configurar mapeamento" — só para quem
 *     tem `manage_input_mapping`; o operador lê a explicação e nenhum botão;
 *   - com mapeamento → o resumo legível (campo ← coluna, formatos, convenção de
 *     sinal) e "Alterar mapeamento" — idem;
 *   - erro na carga → "Tentar novamente".
 *
 * A LEITURA não pede permissão (o servidor libera o GET a todo papel que
 * alcança o cliente): o operador precisa ver o que será aplicado antes de
 * enviar. Cliente encerrado é só-leitura (o servidor nega o PUT com 409).
 */

import { Settings2 } from 'lucide-react';

import { Button } from '@/components/ui/button';
import { ApiError } from '@/lib/api/client';
import type { InputMapping } from '@/lib/contracts';
import { formatCreatedAt } from '@/lib/format';

import { MappingSummary } from './mapping-summary';

interface InputMappingSectionProps {
  mapping: InputMapping | null | undefined;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  onRetry: () => void;
  /** `manage_input_mapping` E cliente aberto. */
  canManage: boolean;
  isClosed: boolean;
  /** Abre o editor — vazio (criar) ou com o mapeamento atual (alterar). */
  onConfigure: () => void;
}

export function InputMappingSection({
  mapping,
  isLoading,
  isError,
  error,
  onRetry,
  canManage,
  isClosed,
  onConfigure,
}: InputMappingSectionProps) {
  return (
    <section
      aria-labelledby="input-mapping-heading"
      className="bg-card space-y-3 rounded-lg border p-4"
      data-testid="input-mapping-section"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h3 id="input-mapping-heading" className="text-base font-semibold">
            Mapeamento de colunas
          </h3>
          <p className="text-muted-foreground text-sm">
            Qual coluna do arquivo é data, descrição, valor e categoria — declarado uma vez e
            reaplicado a cada arquivo enviado.
          </p>
        </div>
        {canManage && mapping && (
          <Button type="button" variant="outline" onClick={onConfigure}>
            <Settings2 className="h-4 w-4" aria-hidden="true" />
            Alterar mapeamento
          </Button>
        )}
      </div>

      {isLoading ? (
        <div className="space-y-2" aria-hidden="true">
          <div className="bg-muted h-4 w-2/3 animate-pulse rounded" />
          <div className="bg-muted h-4 w-1/2 animate-pulse rounded" />
        </div>
      ) : isError ? (
        <div role="alert" className="space-y-2">
          <p className="text-destructive text-sm">
            {error instanceof ApiError
              ? error.userMessage
              : 'Não foi possível carregar o mapeamento de colunas.'}
          </p>
          <Button type="button" variant="outline" size="sm" onClick={onRetry}>
            Tentar novamente
          </Button>
        </div>
      ) : mapping ? (
        <>
          <MappingSummary mapping={mapping} />
          <p className="text-muted-foreground text-xs">
            Atualizado em {formatCreatedAt(mapping.updatedAt)}.
          </p>
        </>
      ) : (
        <EmptyMapping canManage={canManage} isClosed={isClosed} onConfigure={onConfigure} />
      )}
    </section>
  );
}

function EmptyMapping({
  canManage,
  isClosed,
  onConfigure,
}: {
  canManage: boolean;
  isClosed: boolean;
  onConfigure: () => void;
}) {
  return (
    <div
      role="status"
      data-testid="input-mapping-empty"
      className="flex flex-col items-start gap-3 rounded-lg border border-dashed p-4"
    >
      <div className="space-y-1">
        <p className="text-sm font-medium">Este cliente ainda não tem mapeamento de colunas</p>
        <p className="text-muted-foreground text-sm">
          {canManage
            ? 'Escolha um arquivo do cliente para ver as colunas dele e dizer qual é a data, a descrição, o valor e a categoria. O envio do mês só processa depois disso.'
            : isClosed
              ? 'Cliente encerrado: nenhum mapeamento pode ser configurado.'
              : 'Sem o mapeamento o arquivo do mês não processa. Peça a alguém da equipe com acesso de configuração para criá-lo.'}
        </p>
      </div>
      {canManage && (
        <Button type="button" onClick={onConfigure}>
          <Settings2 className="h-4 w-4" aria-hidden="true" />
          Configurar mapeamento
        </Button>
      )}
    </div>
  );
}
