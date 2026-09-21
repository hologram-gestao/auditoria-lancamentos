'use client';

/**
 * A dimensão de ORGANIZAÇÃO nas telas existentes (86e36ed1d) — o hook que
 * carrega as opções e o `<Select>` de FILTRO que as lista.
 *
 * Duas regras moram aqui para não serem redescobertas em cada tela:
 *
 *   1. **Criar ≠ filtrar.** Em CRIAÇÃO (`activeOnly`) a organização suspensa
 *      fica fora: o backend responde 409 e oferecer a opção seria mostrar ação
 *      que o servidor nega (§4.9). Em FILTRO ela aparece, marcada — os clientes
 *      de uma organização suspensa continuam existindo e a plataforma precisa
 *      alcançá-los.
 *   2. **Quem renderiza decide.** Nada aqui consulta a matriz: a tela pergunta
 *      `isPlatformScoped(user)` e simplesmente não monta o campo para o staff,
 *      que não escolhe organização nenhuma — a dele vem da LINHA, e um
 *      `organization_id` divergente no payload é 403, nunca ignorado.
 *
 * O campo de FORMULÁRIO não é um componente: ele é inline nas três telas, com
 * o `<FormControl>` envolvendo o `<SelectTrigger>` (e não o `<Select>` inteiro,
 * senão o Slot do Radix daria o `id` do `FormItem` ao componente errado e o
 * rótulo apontaria para o nada — a mesma armadilha já anotada no campo de senha
 * do "Novo Usuário"). O que eles compartilham é o `useOrganizationOptions`.
 *
 * `pageSize: 100` é o teto do backend e cobre com folga o número de BPOs
 * previsto; passando disso, o campo vira combobox com busca server-side.
 */

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useOrganizationsList } from '@/hooks/use-organizations';
import type { OrganizationItem } from '@/lib/api/organizations';

/** Sentinela do "sem filtro" — o Select do Radix não aceita `''` como valor. */
export const ALL_ORGANIZATIONS = 'all';

interface UseOrganizationOptionsArgs {
  /** Só busca quando o campo está em uso: nenhuma tela paga um GET à toa. */
  enabled: boolean;
  /** Criação: esconde as suspensas, que o backend recusaria com 409. */
  activeOnly?: boolean;
}

export function useOrganizationOptions({
  enabled,
  activeOnly = false,
}: UseOrganizationOptionsArgs): {
  organizations: OrganizationItem[];
  isLoading: boolean;
  isError: boolean;
} {
  const { data, isLoading, isError } = useOrganizationsList(
    { page: 1, pageSize: 100 },
    { enabled },
  );
  const all = data?.data ?? [];
  return {
    organizations: activeOnly ? all.filter((o) => o.active) : all,
    isLoading: enabled && isLoading,
    // Sem isto, a falha da busca vira uma lista VAZIA silenciosa: o campo é
    // obrigatório para a plataforma, o zod bloqueia o envio pedindo para
    // escolher, e não há nada para escolher nem explicação na tela.
    isError: enabled && isError,
  };
}

/**
 * Como a organização aparece numa opção. A suspensa é dita, senão a lista
 * vazia de clientes dela parece defeito da tela.
 */
export function organizationOptionLabel(organization: OrganizationItem): string {
  return organization.active ? organization.name : `${organization.name} (suspensa)`;
}

interface OrganizationFilterProps {
  value: string;
  onValueChange: (value: string) => void;
  /** Rótulo acessível do gatilho — filtro não tem `<label>` visível. */
  ariaLabel: string;
  allLabel?: string;
  className?: string;
}

/** O `<Select>` de filtro das listas (clientes, usuários). */
export function OrganizationFilterSelect({
  value,
  onValueChange,
  ariaLabel,
  allLabel = 'Todas as organizações',
  className,
}: OrganizationFilterProps) {
  const { organizations, isLoading } = useOrganizationOptions({ enabled: true });
  // No FILTRO a falha é benigna: sem opções, a lista fica sem recorte, que é o
  // estado inicial. Quem precisa dizer o erro é o formulário, onde o campo é
  // obrigatório.

  return (
    <Select value={value} onValueChange={onValueChange} disabled={isLoading}>
      <SelectTrigger className={className} aria-label={ariaLabel}>
        <SelectValue placeholder={allLabel} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={ALL_ORGANIZATIONS}>{allLabel}</SelectItem>
        {organizations.map((o) => (
          <SelectItem key={o.id} value={o.id}>
            {organizationOptionLabel(o)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/**
 * A linha que o formulário mostra quando a busca das organizações FALHA.
 *
 * Existe porque o campo é obrigatório para a plataforma: sem ela, a lista vem
 * vazia, o zod pede "Escolha a organização de destino" e não há o que escolher
 * nem o que explicar — um beco sem saída silencioso.
 */
export function OrganizationLoadError() {
  return (
    <p className="text-destructive text-xs">
      Não foi possível carregar as organizações. Recarregue a página para tentar de novo.
    </p>
  );
}
