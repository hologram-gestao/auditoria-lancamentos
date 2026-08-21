/**
 * Badge da coluna "Situação" (FRONT 9.12). String lenient — mostra fallback
 * cinza para estados não previstos (memória feedback_pydantic).
 */
import { Check, MinusCircle, AlertTriangle, Upload } from 'lucide-react';

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';

interface SituationBadgeProps {
  situation: string;
  /** Explicação das datas (arquivo × Omie) p/ `conciliado_data_divergente`. */
  title?: string;
}

/**
 * Dica acessível no padrão da `QualificationCell` — a coluna VIZINHA
 * (86e2u513j era duas colunas com dois padrões de dica; 86e2u513n alinha).
 * O `title` nativo não aparecia em toque, não era alcançável por teclado e o
 * leitor de tela não anunciava — o dado que EXPLICA o badge ficava invisível
 * para boa parte dos usos.
 *
 * `role="img"` + `aria-label`: ARIA proíbe `aria-label` em role genérico (span
 * cru) — com role de imagem o rótulo vira o nome acessível e carrega a
 * explicação inteira, tooltip aberto ou não. `tabIndex={0}` dá o alcance por
 * teclado (o Radix abre a dica no foco).
 */
function BadgeComDica({
  dica,
  ariaLabel,
  className,
  children,
}: {
  dica: string;
  ariaLabel: string;
  className: string;
  children: React.ReactNode;
}) {
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            role="img"
            tabIndex={0}
            aria-label={ariaLabel}
            className={cn(
              className,
              'focus-visible:ring-ring focus-visible:outline-none focus-visible:ring-2',
            )}
          >
            {children}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="text-xs leading-snug">
          {dica}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

export function SituationBadge({ situation, title }: SituationBadgeProps) {
  if (situation === 'conciliado_data_divergente') {
    const classes = cn(
      'inline-flex items-center gap-1 rounded-full bg-orange-100 px-2 py-0.5 text-xs font-medium text-orange-800',
      'dark:bg-orange-900/40 dark:text-orange-200',
    );
    const conteudo = (
      <>
        <AlertTriangle className="h-3 w-3" aria-hidden="true" />
        Data divergente
      </>
    );
    if (title === undefined) {
      return <span className={classes}>{conteudo}</span>;
    }
    return (
      <BadgeComDica dica={title} ariaLabel={`Data divergente — ${title}`} className={classes}>
        {conteudo}
      </BadgeComDica>
    );
  }
  if (situation === 'conciliado') {
    return (
      <span
        className={cn(
          'inline-flex items-center gap-1 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800',
          'dark:bg-emerald-900/40 dark:text-emerald-200',
        )}
      >
        <Check className="h-3 w-3" aria-hidden="true" />
        Conciliado
      </span>
    );
  }
  if (situation === 'sem_omie') {
    return (
      <span
        className={cn(
          'inline-flex items-center gap-1 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800',
          'dark:bg-amber-900/40 dark:text-amber-200',
        )}
      >
        <AlertTriangle className="h-3 w-3" aria-hidden="true" />
        Sem Omie
      </span>
    );
  }
  if (situation === 'ignorado') {
    return (
      <span
        className={cn(
          'inline-flex items-center gap-1 rounded-full bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-700',
          'dark:bg-slate-700 dark:text-slate-200',
        )}
      >
        <MinusCircle className="h-3 w-3" aria-hidden="true" />
        Ignorado
      </span>
    );
  }
  return (
    <span className="inline-flex rounded-full bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-700 dark:bg-slate-700 dark:text-slate-200">
      {situation}
    </span>
  );
}

/**
 * "Lançada no Omie" (Sprint 7 / FRONT 07.6) — o desfecho do lançamento na
 * própria linha, ao lado da situação.
 *
 * **Pinta por TOKEN semântico** (`bg-success-muted`/`text-success`, o par que o
 * `theme-contrast.test.ts` cobre nos dois temas), e não pela paleta crua que os
 * badges acima ainda usam: nada novo nasce fora dos tokens.
 *
 * ⚠️ **Alcance declarado.** O contrato da linha (`ListedFileEntry`) NÃO tem
 * campo de "lançada pelo ADL": depois do envio ela vira `conciliado` com
 * `omie_lancamento_id`, indistinguível de uma linha que o matcher conciliou.
 * Por isso este badge é alimentado pelo RESUMO do lote (fato observado nesta
 * visita), nunca inferido da listagem — inferir marcaria de "lançada" uma linha
 * conciliada pelo cruzamento, que é dizer ao operador que o ADL escreveu no ERP
 * quando não escreveu. O sinal PERSISTENTE do vínculo continua sendo a ação
 * indisponível com o motivo "já está vinculada a um lançamento do Omie".
 * Fechar isso de vez pede um campo no backend (ver HANDOFF).
 */
export function LancadaNoOmieBadge({ omieLancamentoId }: { omieLancamentoId: number | null }) {
  const classes =
    'bg-success-muted text-success inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium';
  const conteudo = (
    <>
      <Upload className="h-3 w-3" aria-hidden="true" />
      Lançada no Omie
    </>
  );
  if (omieLancamentoId === null) {
    return <span className={classes}>{conteudo}</span>;
  }
  const dica = `Lançamento Omie nº ${omieLancamentoId} criado pelo ADL nesta sessão.`;
  return (
    <BadgeComDica dica={dica} ariaLabel={`Lançada no Omie — ${dica}`} className={classes}>
      {conteudo}
    </BadgeComDica>
  );
}
