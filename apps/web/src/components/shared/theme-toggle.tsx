'use client';

/**
 * Seletor de tema do header (86e2n39hb) — Claro / Escuro / Hologram / Sistema.
 *
 * Padrão do app: **Hologram** (decisão do Pedro, 25/08/2026) — o default vive
 * no `ThemeProvider` (`app/providers.tsx`); aqui só se ESCOLHE, e a escolha
 * persiste no localStorage (comportamento nativo do next-themes).
 *
 * Radio group de verdade (`DropdownMenuRadioGroup`): o leitor de tela anuncia
 * qual opção está ativa, em vez de itens soltos. O rótulo do gatilho é
 * `aria-label` (critério da task: não só ícone).
 *
 * Hidratação: o servidor não sabe o tema (localStorage é do cliente) — antes
 * de `mounted` o ícone é a logomark fixa (o caso comum: ninguém escolheu nada
 * e o tema é o Hologram), e só depois da montagem ele reflete o
 * `resolvedTheme`. Renderizar o "real" direto divergiria do HTML do servidor
 * (hydration mismatch clássico do next-themes).
 */
import { Monitor, Moon, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';
import { useEffect, useState } from 'react';

import { BrandMark } from '@/components/shared/brand-mark';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

export function ThemeToggle() {
  const { theme, resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);

  const icon =
    !mounted || resolvedTheme === 'hologram' ? (
      <BrandMark className="h-5 w-5" />
    ) : resolvedTheme === 'dark' ? (
      <Moon className="h-5 w-5" aria-hidden="true" />
    ) : (
      <Sun className="h-5 w-5" aria-hidden="true" />
    );

  return (
    // `modal={false}`, como no sino: o modo modal (default) chama `hideOthers()`
    // e marca a página com `aria-hidden` mantendo elementos focáveis — o axe
    // reprova com `aria-hidden-focus` (serious). Um menu pequeno não precisa
    // esconder a página nem travar o scroll.
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="shrink-0" aria-label="Alterar tema">
          {icon}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        <DropdownMenuRadioGroup
          value={mounted ? (theme ?? 'hologram') : 'hologram'}
          onValueChange={setTheme}
        >
          <DropdownMenuRadioItem value="light">
            <Sun className="mr-2 h-4 w-4" aria-hidden="true" />
            Claro
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">
            <Moon className="mr-2 h-4 w-4" aria-hidden="true" />
            Escuro
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="hologram">
            <BrandMark className="mr-2 h-4 w-4" />
            Hologram
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="system">
            <Monitor className="mr-2 h-4 w-4" aria-hidden="true" />
            Seguir o sistema
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
