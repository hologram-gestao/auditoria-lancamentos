'use client';

/**
 * "Como funciona" do de-para (86e3n70pn, reunião com o Murilo em 08/10/2026).
 *
 * No primeiro uso por um escritório novo, ninguém sabia o que era destino, alvo,
 * decisão nem herdada: as quatro palavras da tela. O bloco explica cada uma em uma
 * frase, no topo, e é recolhível pela MESMA moldura dos totais
 * (`CollapsibleSummary`, estado por tela no `localStorage`): quem já sabe recolhe
 * uma vez e a tela lembra.
 */

import { CollapsibleSummary } from '@/components/shared/collapsible-summary';

export const HOW_IT_WORKS_TERMS: readonly { term: string; text: string }[] = [
  {
    term: 'Destino',
    text: 'Para onde a classificação vai: demonstrativo contábil, fluxo de caixa, conta contábil… A mesma categoria pode ir para lugares diferentes em cada destino.',
  },
  {
    term: 'Alvo',
    text: 'A linha do destino que recebe a categoria, com código e nome. Os alvos são o catálogo da organização; na conta contábil, o plano contábil do cliente.',
  },
  {
    term: 'Decisão',
    text: 'Para qual alvo cada categoria do cliente vai neste destino, ou "Não mapear". Vale a partir de uma competência.',
  },
  {
    term: 'Herdada',
    text: 'Decisão proposta pela conta de demonstrativo que a categoria já traz no Omie (só no demonstrativo). Fica herdada até alguém confirmar ou alterar.',
  },
];

export function MappingHowItWorks() {
  return (
    <CollapsibleSummary
      storageKey="de-para-como-funciona"
      showLabel="Mostrar como funciona"
      hideLabel="Ocultar como funciona"
      collapsed={
        <p>
          <span className="font-semibold">Como funciona:</span>{' '}
          <span className="text-muted-foreground">destino, alvo, decisão e herdada.</span>
        </p>
      }
      footnote="Como funciona o de-para, em quatro palavras."
    >
      <section aria-labelledby="mapping-how-it-works-heading" className="space-y-2">
        <h2 id="mapping-how-it-works-heading" className="text-sm font-semibold">
          Como funciona
        </h2>
        <dl className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          {HOW_IT_WORKS_TERMS.map(({ term, text }) => (
            <div key={term} className="bg-card space-y-1 rounded-lg border p-3">
              <dt className="text-sm font-medium">{term}</dt>
              <dd className="text-muted-foreground text-sm">{text}</dd>
            </div>
          ))}
        </dl>
      </section>
    </CollapsibleSummary>
  );
}
