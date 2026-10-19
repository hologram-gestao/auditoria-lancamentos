/**
 * Badge da coluna "Situação" (FRONT 9.12). String lenient — mostra fallback
 * cinza para estados não previstos (memória feedback_pydantic).
 */
import { Check, MinusCircle, AlertTriangle, Upload } from 'lucide-react';

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { PRODUCT_SHORT_NAME } from '@/lib/brand';
import type { OmiePostingState } from '@/lib/contracts';
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

/**
 * A situação do lançamento NO OMIE (86e3t4vpq, terceiro fato do FCA de 09/10).
 *
 * É a marcação que o cliente faz no próprio Omie (`cSituacao`), e ela NÃO entra
 * no cruzamento (`matcher.py`: "O matcher não filtra por status"). A frase é a
 * mesma no tooltip e no bloco explicativo da aba, por isso mora aqui.
 */
export const OMIE_STATUS_EXPLANATION =
  "Marcação feita no próprio Omie. Ela não entra no cruzamento: a plataforma compara o arquivo do banco com os lançamentos do Omie. Um lançamento com par aqui e 'Não conciliado' lá significa que o Omie está desatualizado.";

/** O único valor que a Omie devolve para "já conciliado lá dentro" (fixtures reais). */
const OMIE_RECONCILED_STATUS = 'Conciliado';

/**
 * Selo "No Omie: <situação>", com o texto como a Omie devolveu (o campo é
 * texto livre no contrato; nada de enum no front). Sem situação (lançamento
 * recém-criado volta sem `cSituacao`, e o cache grava `""`), não há selo:
 * inventar "Conciliado" seria afirmar o que a Omie não disse.
 */
export function OmieStatusBadge({ status }: { status: string | null | undefined }) {
  const text = status?.trim() ?? '';
  if (text === '') return null;
  const reconciled = text === OMIE_RECONCILED_STATUS;
  const label = `No Omie: ${text}`;
  return (
    <BadgeComDica
      dica={OMIE_STATUS_EXPLANATION}
      ariaLabel={`${label}. ${OMIE_STATUS_EXPLANATION}`}
      className={cn(
        'inline-flex w-fit items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium',
        reconciled ? 'bg-muted text-muted-foreground' : 'bg-warning-muted text-warning',
      )}
    >
      {label}
    </BadgeComDica>
  );
}

export function SituationBadge({ situation, title }: SituationBadgeProps) {
  // 86e2n39hb — badges pintam por TOKEN (par `-muted` + sólido, travado no
  // theme-contrast.test.ts nos dois temas), nunca pela paleta crua. O laranja
  // (data divergente) e o âmbar (sem Omie) colapsaram no MESMO `warning` de
  // propósito: `orange-100` e `amber-100` eram vizinhos quase indistinguíveis
  // e o distintivo real sempre foi o RÓTULO — dois tokens quentes seriam
  // estrutura sem função.
  if (situation === 'conciliado_data_divergente') {
    const classes =
      'bg-warning-muted text-warning inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium';
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
      <span className="bg-success-muted text-success inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium">
        <Check className="h-3 w-3" aria-hidden="true" />
        Conciliado
      </span>
    );
  }
  if (situation === 'sem_omie') {
    return (
      <span className="bg-warning-muted text-warning inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium">
        <AlertTriangle className="h-3 w-3" aria-hidden="true" />
        Sem Omie
      </span>
    );
  }
  if (situation === 'ignorado') {
    return (
      <span className="bg-muted text-muted-foreground inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium">
        <MinusCircle className="h-3 w-3" aria-hidden="true" />
        Ignorado
      </span>
    );
  }
  return (
    <span className="bg-muted text-muted-foreground inline-flex rounded-full px-2 py-0.5 text-xs font-medium">
      {situation}
    </span>
  );
}

/** Nome do passo da provisão na frase do selo. */
const STEP_LABEL: Record<string, string> = {
  fornecedor: 'fornecedor',
  titulo: 'título',
  baixa: 'baixa',
  concluido: 'concluído',
};

/**
 * O que o sistema escreveu (ou tentou escrever) no Omie por esta linha, na
 * própria linha, ao lado da situação (Sprint 7; número visível desde a
 * 86e3n70qj; provisão desde a 86e3n70q0, bloco C).
 *
 * **Verdade do banco.** O estado vem do servidor (`omie_posting` na listagem,
 * lido de `reconciliation_omie_postings`), não de uma observação da tela: o selo
 * sobrevive ao recarregamento e é o mesmo para quem abre a conciliação depois.
 * Uma linha conciliada pelo CRUZAMENTO não tem intenção, e por isso nunca leva o
 * selo — dizer que o sistema escreveu no ERP quando não escreveu seria pior que
 * não dizer nada. O cache da visita (`usePostedInSession`) só pinta o selo entre
 * o envio e o refetch.
 *
 * - direto confirmado: "Lançado no Omie · nº X";
 * - provisão confirmada: "Provisionado · título nº X";
 * - provisão começada e não terminada: "Lançamento parado · passo Y", em aviso,
 *   com a mensagem do servidor na dica;
 * - direto enviado sem confirmação: "Lançamento sem confirmação", em aviso.
 *
 * Pinta por TOKEN semântico (o par `-muted` + sólido do `theme-contrast.test.ts`).
 */
export function OmiePostingBadge({ posting }: { posting: OmiePostingState | null | undefined }) {
  if (posting === null || posting === undefined) return null;
  const success =
    'bg-success-muted text-success inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium';
  const warning =
    'bg-warning-muted text-warning inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium';

  if (posting.status === 'confirmed' && posting.kind === 'provisao') {
    const rotulo =
      posting.payable_id === null
        ? 'Provisionado'
        : `Provisionado · título nº ${posting.payable_id}`;
    const dica =
      posting.payment_id === null
        ? `Título criado pelo ${PRODUCT_SHORT_NAME} no Contas a Pagar do Omie, a vencer e sem baixa: a baixa é feita quando a fatura for paga.`
        : `Título criado pelo ${PRODUCT_SHORT_NAME} no Contas a Pagar do Omie e baixado na conta do cartão (baixa nº ${posting.payment_id}).`;
    return (
      <BadgeComDica dica={dica} ariaLabel={`${rotulo}. ${dica}`} className={success}>
        <Upload className="h-3 w-3" aria-hidden="true" />
        {rotulo}
      </BadgeComDica>
    );
  }
  if (posting.status === 'confirmed') {
    // `whitespace-nowrap`: "nº" e o número não podem se separar numa coluna estreita.
    const rotulo =
      posting.omie_lancamento_id === null
        ? 'Lançado no Omie'
        : `Lançado no Omie · nº ${posting.omie_lancamento_id}`;
    const dica = `Lançado pelo ${PRODUCT_SHORT_NAME} na conta do cartão no Omie, na data da compra.`;
    return (
      <BadgeComDica dica={dica} ariaLabel={`${rotulo}. ${dica}`} className={success}>
        <Upload className="h-3 w-3" aria-hidden="true" />
        {rotulo}
      </BadgeComDica>
    );
  }
  if (posting.kind === 'provisao') {
    const step = posting.step ?? '';
    const rotulo = `Lançamento parado · passo ${STEP_LABEL[step] ?? step}`;
    const dica =
      posting.error_message ??
      'Houve envio ao Omie sem confirmação neste passo. Confira no Omie antes de retomar.';
    return (
      <BadgeComDica dica={dica} ariaLabel={`${rotulo}. ${dica}`} className={warning}>
        <AlertTriangle className="h-3 w-3" aria-hidden="true" />
        {rotulo}
      </BadgeComDica>
    );
  }
  if (posting.status === 'pending' && posting.attempts > 0) {
    const rotulo = 'Lançamento sem confirmação';
    const dica =
      'Houve envio direto ao Omie sem confirmação. Confira no Omie antes de tentar de novo.';
    return (
      <BadgeComDica dica={dica} ariaLabel={`${rotulo}. ${dica}`} className={warning}>
        <AlertTriangle className="h-3 w-3" aria-hidden="true" />
        {rotulo}
      </BadgeComDica>
    );
  }
  return null;
}
