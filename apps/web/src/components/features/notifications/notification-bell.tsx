'use client';

/**
 * Sino de notificações do header — Sprint 4 / R4.
 *
 * É a resposta à pergunta da reunião de 07/07: *"a pessoa sai dessa tela, como
 * é que ela sabe que acabou?"*. O contador incrementa sozinho (polling de 15 s,
 * só com a aba em foco) e clicar num item leva ao detalhe da conciliação — ou à
 * tela de erro, quando foi o caso.
 *
 * **Acessibilidade:** o contador vive num `role="status"`/`aria-live="polite"`,
 * então um leitor de tela anuncia a mudança sem roubar o foco de quem está
 * digitando. O `aria-label` do botão carrega o número, porque um badge visual
 * sozinho não existe para quem não enxerga.
 *
 * **Sem PII.** O texto é montado AQUI a partir de conta/mês/tipo/código — nada
 * do conteúdo do arquivo entra no aviso, e o erro aparece como CÓDIGO, nunca
 * como a linguagem interna do backend (S2/R9).
 *
 * **Instrumentação:** ao abrir o sino, cada não lida visível emite
 * `notificacao_entregue` (`via='sino'`, `latencia_s` = quanto tempo a
 * notificação passou esperando ser vista). É o que mede se o aviso ALCANÇA a
 * pessoa — e não apenas se ele foi gravado. O endpoint é idempotente por
 * (evento, sessão), então reabrir o sino não duplica nada.
 */

import { Bell, Loader2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useState } from 'react';

import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  useMarkNotificationRead,
  useNotifications,
  useUnreadNotificationsCount,
} from '@/hooks/use-notifications';
import type { Notification } from '@/lib/api/notifications';
import { recordNotificacaoEntregue } from '@/lib/api/usage-events';
import { formatReferenceMonth } from '@/lib/format';
import { cn } from '@/lib/utils';

const LIST_PAGE_SIZE = 10;

export function NotificationBell() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const countQuery = useUnreadNotificationsCount();
  const listQuery = useNotifications({ page: 1, pageSize: LIST_PAGE_SIZE }, { enabled: open });
  const markRead = useMarkNotificationRead();

  const unread = countQuery.data ?? 0;
  // `useMemo` mantém a identidade do array estável entre renders — sem isso o
  // efeito de emissão abaixo dispararia a cada render.
  const items = useMemo(() => listQuery.data?.data ?? [], [listQuery.data]);

  // Entrega: a pessoa ABRIU o sino e as não lidas estão na tela. Emitir na
  // criação da notificação mediria a máquina; emitir aqui mede o alcance.
  useEffect(() => {
    if (!open) return;
    for (const item of items) {
      if (item.read_at != null) continue;
      const latency = (Date.now() - new Date(item.created_at).getTime()) / 1000;
      void recordNotificacaoEntregue(item.session_id, 'sino', latency);
    }
  }, [open, items]);

  function handleOpenItem(item: Notification) {
    setOpen(false);
    // Marcar como lida é best-effort: falhar não pode impedir a navegação —
    // o próximo poll reconcilia o contador.
    markRead.mutate(item.id);
    router.push(`/clientes/${item.client_id}/conciliacao/${item.session_id}`);
  }

  return (
    // `modal={false}`: no modo modal (default) o Radix chama `hideOthers()` e
    // marca TODO o resto da página com `aria-hidden` — mas o resto continua
    // focável. Quem navega por teclado sai do popover e cai em controles que o
    // leitor de tela não enxerga ("foco no vazio"); é a violação
    // `aria-hidden-focus` do axe. Um sino de notificações não é um diálogo: ele
    // não precisa esconder a página nem travar o scroll. Sem o modo modal, o
    // Radix segue cuidando de Escape, clique fora, setas e devolução do foco ao
    // gatilho — só não mente para a tecnologia assistiva.
    <DropdownMenu open={open} onOpenChange={setOpen} modal={false}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="relative"
          aria-label={
            unread > 0 ? `Notificações — ${unread} não lidas` : 'Notificações — nenhuma não lida'
          }
        >
          <Bell className="h-5 w-5" aria-hidden="true" />
          {unread > 0 && (
            <span
              className="bg-destructive text-destructive-foreground absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[10px] font-semibold"
              aria-hidden="true"
            >
              {unread > 99 ? '99+' : unread}
            </span>
          )}
        </Button>
      </DropdownMenuTrigger>

      {/* Anúncio acessível fora do botão: `aria-live` num elemento que só muda
          de TEXTO é o que faz o leitor de tela avisar sem roubar o foco. */}
      <span role="status" aria-live="polite" className="sr-only">
        {unread > 0 ? `${unread} notificações não lidas` : 'Nenhuma notificação não lida'}
      </span>

      {/* O scroll fica no PRÓPRIO content: uma `<ul>` aqui dentro seria um
          `role="list"` dentro de `role="menu"` — filho não permitido
          (`aria-required-children`, critical). Os itens são `menuitem`. */}
      <DropdownMenuContent align="end" className="max-h-96 w-80 overflow-y-auto">
        <DropdownMenuLabel>Notificações</DropdownMenuLabel>
        <DropdownMenuSeparator />

        {listQuery.isLoading ? (
          <p className="text-muted-foreground flex items-center gap-2 px-2 py-6 text-sm">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Carregando…
          </p>
        ) : listQuery.isError ? (
          <p className="text-destructive px-2 py-6 text-sm" role="alert">
            Não foi possível carregar as notificações.
          </p>
        ) : items.length === 0 ? (
          <p className="text-muted-foreground px-2 py-6 text-center text-sm">
            Nenhuma notificação por aqui.
          </p>
        ) : (
          items.map((item) => (
            <DropdownMenuItem
              key={item.id}
              onSelect={() => handleOpenItem(item)}
              className={cn(
                'cursor-pointer items-start gap-2 py-2',
                item.read_at == null && 'font-medium',
              )}
            >
              {item.read_at == null && (
                <span className="bg-info mt-1.5 h-2 w-2 shrink-0 rounded-full" aria-hidden="true" />
              )}
              <span className="min-w-0">
                <span className="block">{notificationText(item)}</span>
                <span className="text-muted-foreground block text-xs">
                  {formatNotificationTime(item.created_at)}
                </span>
              </span>
            </DropdownMenuItem>
          ))
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * Frase do aviso, montada só com conta/mês/tipo/código.
 *
 * ⚠️ O contrato não devolve o NOME da conta (é dado do Omie, resolvido por
 * cliente) — daí o `Conta #{id}`. Ao clicar, o detalhe mostra tudo nomeado.
 */
export function notificationText(item: Notification): string {
  const mes = formatReferenceMonth(item.reference_month);
  const conta = `Conta #${item.omie_conta_id}`;
  if (item.tipo === 'erro') {
    const code = item.error_code != null ? ` (cód. ${item.error_code})` : '';
    return `Não foi possível concluir a conciliação de ${conta} — ${mes}${code}.`;
  }
  return `Conciliação de ${conta} — ${mes} processada. Clique para revisar.`;
}

/** `há 3 min` / `há 2 h` / `12/06/2026` para o que já é de outro dia. */
function formatNotificationTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const diffMs = Date.now() - date.getTime();
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) return 'agora';
  if (minutes < 60) return `há ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `há ${hours} h`;
  const day = String(date.getDate()).padStart(2, '0');
  const month = String(date.getMonth() + 1).padStart(2, '0');
  return `${day}/${month}/${date.getFullYear()}`;
}
