'use client';

/**
 * "Conciliado aqui × conciliado no Omie" (86e3t4vpq, terceiro fato do FCA de
 * 09/10/2026).
 *
 * A linha conciliada mostra a situação do lançamento no Omie, e as duas
 * situações podem discordar: a plataforma diz "Conciliado" e o Omie diz "Não
 * conciliado" quando o cliente ainda não marcou o lançamento lá dentro. O bloco
 * explica, uma vez, no topo da aba, que a marcação do Omie não decide o
 * cruzamento. Recolhível pela MESMA moldura dos totais e do "Como funciona" do
 * de-para (`CollapsibleSummary`, estado por tela no `localStorage`).
 */

import { useId } from 'react';

import { CollapsibleSummary } from '@/components/shared/collapsible-summary';

import { OMIE_STATUS_EXPLANATION } from './situation-badge';

export function OmieStatusExplainer() {
  const headingId = useId();
  return (
    <CollapsibleSummary
      storageKey="revisao-situacao-no-omie"
      showLabel="Mostrar a explicação"
      hideLabel="Ocultar a explicação"
      collapsed={
        <p>
          <span className="font-semibold">Situação no Omie:</span>{' '}
          <span className="text-muted-foreground">não decide o cruzamento.</span>
        </p>
      }
      footnote="O que o selo “No Omie” quer dizer."
    >
      <section aria-labelledby={headingId} className="bg-card space-y-1 rounded-lg border p-3">
        <h2 id={headingId} className="text-sm font-semibold">
          Conciliado aqui e conciliado no Omie
        </h2>
        <p className="text-muted-foreground text-sm">{OMIE_STATUS_EXPLANATION}</p>
      </section>
    </CollapsibleSummary>
  );
}
