'use client';

/**
 * Combobox com busca por digitação (design-system, Sprint 7 / FRONT 07.7).
 *
 * Existe porque o `<Select>` do Radix não filtra: com ~300 categorias do Omie,
 * escolher vira rolagem. O requisito da task é explícito — busca por digitação,
 * altura máxima com scroll e o padrão **APG de combobox** (teclado,
 * `aria-activedescendant`, foco visível).
 *
 * **Arranjo escolhido** (o mesmo do shadcn/ui, e é ele que resolve o conflito
 * de foco): o gatilho é um botão com `aria-haspopup="listbox"`, e o campo de
 * busca (`role="combobox"`) mora DENTRO do popover, junto da lista. Manter o
 * input fora do popover com `modal` seria colocar o foco fora do escopo que o
 * Radix prende — o `FocusScope` puxaria o foco de volta a cada tecla.
 *
 * **`Popover modal`** (default do `ui/popover`): popover dentro de gaveta herda
 * o `react-remove-scroll` do diálogo pai, que engole o `wheel` — sem `modal` a
 * lista abre e não rola.
 *
 * **Foco fica no input; a seleção anda por `aria-activedescendant`** (APG). As
 * opções não são focáveis: quem lê a tela ouve a opção ativa sem que o foco
 * saia do campo, e o `Enter` sempre age sobre ela.
 *
 * **Sobre o `scrollable-region-focusable` do axe:** a lista rolável não precisa
 * de `tabIndex` aqui — a regra ignora popup de combobox por construção
 * (`_isComboboxPopup` no matcher do axe-core 4.12). Ela é exceção justamente
 * porque o teclado chega pelo `aria-activedescendant`.
 */

import { Check, ChevronsUpDown, Loader2, Search } from 'lucide-react';
import * as React from 'react';

import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { cn } from '@/lib/utils';

export interface ComboboxOption {
  /** Valor que vai para o servidor (aqui, o `cCodCateg`). */
  value: string;
  /** O que o operador lê e busca. */
  label: string;
}

export interface ComboboxProps {
  options: ComboboxOption[];
  /** Valor selecionado, ou `null` quando ainda não há escolha. */
  value: string | null;
  onValueChange: (value: string) => void;
  /** Nome acessível do controle — obrigatório. */
  label: string;
  placeholder?: string;
  searchPlaceholder?: string;
  emptyMessage?: string;
  disabled?: boolean;
  /** Lista ainda carregando: o gatilho mostra spinner e não abre. */
  loading?: boolean;
  className?: string;
}

/** Ignora acento e caixa — "servicos" acha "Serviços". */
function normalize(text: string): string {
  return text
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase();
}

export function Combobox({
  options,
  value,
  onValueChange,
  label,
  placeholder = 'Selecionar…',
  searchPlaceholder = 'Buscar…',
  emptyMessage = 'Nada encontrado.',
  disabled = false,
  loading = false,
  className,
}: ComboboxProps) {
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState('');
  const [activeIndex, setActiveIndex] = React.useState(0);
  const listId = React.useId();
  const optionId = (index: number) => `${listId}-option-${index}`;
  const listRef = React.useRef<HTMLUListElement>(null);

  const selected = options.find((o) => o.value === value) ?? null;

  const filtered = React.useMemo(() => {
    const q = normalize(query.trim());
    if (q === '') return options;
    return options.filter((o) => normalize(`${o.value} ${o.label}`).includes(q));
  }, [options, query]);

  // Digitar move a opção ativa para o topo do novo resultado — senão o `Enter`
  // escolheria um item que já saiu da lista.
  React.useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  // A opção ativa tem de estar VISÍVEL: navegar por teclado numa lista com
  // altura máxima é inútil se a seleção sai da área rolável.
  React.useEffect(() => {
    if (!open) return;
    const node = listRef.current?.children.item(activeIndex);
    if (node instanceof HTMLElement) node.scrollIntoView({ block: 'nearest' });
  }, [activeIndex, open, filtered.length]);

  function commit(index: number): void {
    const option = filtered[index];
    if (option === undefined) return;
    onValueChange(option.value);
    setOpen(false);
    setQuery('');
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLInputElement>): void {
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault();
        setActiveIndex((i) => Math.min(i + 1, filtered.length - 1));
        break;
      case 'ArrowUp':
        event.preventDefault();
        setActiveIndex((i) => Math.max(i - 1, 0));
        break;
      case 'Home':
        event.preventDefault();
        setActiveIndex(0);
        break;
      case 'End':
        event.preventDefault();
        setActiveIndex(Math.max(filtered.length - 1, 0));
        break;
      case 'Enter':
        event.preventDefault();
        commit(activeIndex);
        break;
      default:
        break;
    }
  }

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setQuery('');
      }}
    >
      <PopoverTrigger
        type="button"
        disabled={disabled || loading}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={selected === null ? label : `${label}: ${selected.label}`}
        className={cn(
          'border-input bg-background ring-offset-background focus-visible:ring-ring flex h-9 w-full cursor-pointer items-center justify-between gap-2 rounded-md border px-3 py-1 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50',
          className,
        )}
      >
        <span className={cn('truncate', selected === null && 'text-muted-foreground')}>
          {selected?.label ?? placeholder}
        </span>
        {loading ? (
          <Loader2 className="h-4 w-4 shrink-0 animate-spin opacity-50" aria-hidden="true" />
        ) : (
          <ChevronsUpDown className="h-4 w-4 shrink-0 opacity-50" aria-hidden="true" />
        )}
      </PopoverTrigger>

      {/* O conteúdo do Popover do Radix é um `role="dialog"`, e diálogo sem
          nome acessível reprova `aria-dialog-name` (SERIOUS) — medido, não
          suposto: o axe acusou na primeira execução desta suíte. */}
      <PopoverContent
        aria-label={label}
        className="w-[--radix-popover-trigger-width] min-w-[16rem] p-0"
      >
        <div className="flex items-center gap-2 border-b px-3">
          <Search className="text-muted-foreground h-4 w-4 shrink-0" aria-hidden="true" />
          <input
            // eslint-disable-next-line jsx-a11y/no-autofocus -- o popover só
            // abre por ação do usuário e o campo de busca é o motivo de ele
            // existir; sem isto o operador precisa de um Tab a cada linha.
            autoFocus
            role="combobox"
            aria-expanded
            aria-controls={listId}
            aria-autocomplete="list"
            aria-activedescendant={filtered.length > 0 ? optionId(activeIndex) : undefined}
            aria-label={`${label} — buscar`}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={searchPlaceholder}
            className="placeholder:text-muted-foreground h-9 w-full bg-transparent text-sm outline-none"
          />
        </div>

        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          aria-label={label}
          className="max-h-60 overflow-y-auto p-1"
        >
          {filtered.length === 0 && (
            <li role="presentation" className="text-muted-foreground px-2 py-3 text-sm">
              {emptyMessage}
            </li>
          )}
          {filtered.map((option, index) => (
            <li
              key={option.value}
              id={optionId(index)}
              role="option"
              aria-selected={option.value === value}
              // O foco NÃO sai do campo de busca (APG): o mousedown padrão
              // tiraria, e a lista fecharia antes do clique.
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => commit(index)}
              onMouseEnter={() => setActiveIndex(index)}
              className={cn(
                'flex cursor-pointer items-center gap-2 rounded-sm px-2 py-1.5 text-sm',
                index === activeIndex && 'bg-accent text-accent-foreground',
              )}
            >
              <Check
                className={cn('h-4 w-4 shrink-0', option.value === value ? 'opacity-100' : 'opacity-0')}
                aria-hidden="true"
              />
              <span className="truncate">{option.label}</span>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  );
}
