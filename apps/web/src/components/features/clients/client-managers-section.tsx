'use client';

/**
 * Seção "Gerentes com acesso" do modal de edição de cliente (86e390m4c).
 *
 * Substitui o seletor "Gerente Responsável", que TROCAVA a pessoa: reatribuir
 * sobrescrevia o vínculo e o gerente anterior perdia o acesso em silêncio (a
 * Bruna, no cliente Hologram, 14/09/2026). Aqui a carteira é explícita:
 *
 *   - quem tem acesso hoje, com o RESPONSÁVEL marcado (um só — é o nome que
 *     aparece na coluna da lista de clientes);
 *   - adicionar gerente (ninguém sai);
 *   - tornar responsável (ninguém sai — e a tela diz isso, porque a palavra
 *     "reatribuir" carregava exatamente o significado oposto);
 *   - remover acesso, com confirmação NOMEANDO quem deixa de ver o cliente.
 *
 * As ações são imediatas, cada uma com a própria confirmação e toast — não
 * ficam presas ao "Salvar" do formulário: são mudanças de carteira, não de
 * dados do cliente. O responsável não tem "Remover": o servidor recusa (409) e
 * ação que o servidor nega não aparece na tela (CLAUDE.md §4.9).
 *
 * Quem pode ver/agir é decidido pelo pai (`hasPermission(user, 'edit_client')`);
 * a barreira real é o backend (admin pela matriz + tenant pela linha).
 */

import { Loader2, UserMinus } from 'lucide-react';
import { useId, useMemo, useState } from 'react';
import { toast } from 'sonner';

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { ScrollRegion } from '@/components/ui/scroll-region';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  useAddClientManager,
  useAssignClient,
  useClientManagers,
  useRemoveClientManager,
} from '@/hooks/use-clients';
import { useUsersList } from '@/hooks/use-users';
import { ApiError } from '@/lib/api/client';
import type { Client, ClientManager } from '@/lib/api/clients';
import { useAuthStore } from '@/stores/auth';

interface ClientManagersSectionProps {
  client: Client;
  /** Desabilita as ações enquanto o formulário do modal está salvando. */
  disabled?: boolean;
}

function errorMessage(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.userMessage : fallback;
}

export function ClientManagersSection({ client, disabled = false }: ClientManagersSectionProps) {
  const currentUserId = useAuthStore((s) => s.user?.id);
  const headingId = useId();
  const managersQuery = useClientManagers(client.id);
  // Candidatos a entrar na carteira: gerentes DA ORGANIZAÇÃO DO CLIENTE
  // (86e36ed1d). Antes o filtro era `role === 'manager'` no navegador sobre a
  // primeira página de `/users` — com uma organização só isso coincidia com a
  // resposta certa; com N, ofereceria gerente de outra organização, e o backend
  // recusa com o MESMO 400 de "não é gerente" (`is_active_manager` é intra-org,
  // e a mensagem é única de propósito, contra enumeração). Perguntar ao
  // servidor pelo papel E pela organização é o que faz a lista oferecer só
  // quem o servidor aceitaria. O `pageSize=100` continua sendo o teto da rota e
  // cobre o time de uma organização; passando disso, o 101º gerente ativo some
  // do seletor em silêncio e o campo precisa virar busca server-side.
  const usersQuery = useUsersList({
    page: 1,
    pageSize: 100,
    role: 'manager',
    organizationId: client.organization.id,
  });
  const addMutation = useAddClientManager(client.id);
  const removeMutation = useRemoveClientManager(client.id);
  const assignMutation = useAssignClient(client.id);

  const [candidateId, setCandidateId] = useState('');
  const [removing, setRemoving] = useState<ClientManager | null>(null);
  const [promoting, setPromoting] = useState<ClientManager | null>(null);

  const managers = useMemo(() => managersQuery.data ?? [], [managersQuery.data]);
  const responsible = managers.find((m) => m.is_responsible) ?? null;
  // O papel e a organização já vieram filtrados do servidor; aqui sobra o que
  // ele não tem como saber: quem já está na carteira. O `active` continua local
  // porque a rota não filtra por ele (o backend devolve 400 para inativo).
  const candidates = useMemo(() => {
    const assigned = new Set(managers.map((m) => m.id));
    return (usersQuery.data?.data ?? []).filter((u) => u.active && !assigned.has(u.id));
  }, [managers, usersQuery.data]);

  const busy =
    disabled || addMutation.isPending || removeMutation.isPending || assignMutation.isPending;

  async function handleAdd() {
    const candidate = candidates.find((u) => u.id === candidateId);
    if (!candidate) return;
    try {
      await addMutation.mutateAsync({ user_id: candidate.id });
      setCandidateId('');
      toast.success(`${candidate.name} passou a ter acesso a ${client.name}.`);
    } catch (err) {
      toast.error(errorMessage(err, 'Não foi possível adicionar o gerente.'));
    }
  }

  async function handleRemove() {
    if (!removing) return;
    const target = removing;
    try {
      await removeMutation.mutateAsync(target.id);
      setRemoving(null);
      toast.success(`${target.name} deixou de ver ${client.name}.`);
    } catch (err) {
      toast.error(errorMessage(err, 'Não foi possível remover o acesso.'));
    }
  }

  async function handlePromote() {
    if (!promoting) return;
    const target = promoting;
    try {
      await assignMutation.mutateAsync({ user_id: target.id });
      setPromoting(null);
      toast.success(`${target.name} agora é o responsável por ${client.name}.`);
    } catch (err) {
      toast.error(errorMessage(err, 'Não foi possível trocar o responsável.'));
    }
  }

  return (
    <section aria-labelledby={headingId} className="space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <h3 id={headingId} className="text-sm font-medium">
          Gerentes com acesso
        </h3>
        {managersQuery.data && (
          <span className="text-muted-foreground text-xs tabular-nums">
            {managers.length} {managers.length === 1 ? 'pessoa' : 'pessoas'}
          </span>
        )}
      </div>
      <p className="text-muted-foreground text-xs">
        O responsável é quem aparece na lista de clientes. Trocar o responsável não remove o acesso
        de ninguém.
      </p>

      <ScrollRegion label="Gerentes com acesso ao cliente" className="max-h-56 rounded-md border">
        {managersQuery.isLoading ? (
          <p className="text-muted-foreground px-3 py-3 text-sm">Carregando gerentes...</p>
        ) : managersQuery.isError ? (
          <p className="text-destructive px-3 py-3 text-sm">
            {errorMessage(managersQuery.error, 'Não foi possível carregar os gerentes.')}
          </p>
        ) : managers.length === 0 ? (
          <p className="text-muted-foreground px-3 py-3 text-sm">
            Nenhum gerente tem acesso a este cliente.
          </p>
        ) : (
          <ul className="divide-y">
            {managers.map((m) => (
              <li
                key={m.id}
                className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-3 py-2"
              >
                {/* Em 390px o bloco de nome ocupa a linha inteira e as ações
                    descem para a linha de baixo — nada é espremido nem cortado. */}
                <div className="min-w-0 basis-full sm:flex-1 sm:basis-0">
                  <p className="truncate text-sm">
                    {m.name}
                    {m.id === currentUserId ? ' (você)' : ''}
                  </p>
                  <p className="text-muted-foreground truncate text-xs">{m.email}</p>
                </div>
                <div className="ml-auto flex shrink-0 items-center gap-1">
                  {/* Usuário desativado continua na carteira até o admin passar o
                      bastão — sem o selo ele não veria por que o cliente "sumiu". */}
                  {!m.active && <Badge variant="outline">Inativo</Badge>}
                  {m.is_responsible ? (
                    <Badge variant="secondary">Responsável</Badge>
                  ) : (
                    <>
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        disabled={busy}
                        onClick={() => setPromoting(m)}
                      >
                        Tornar responsável
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        disabled={busy}
                        aria-label={`Remover acesso de ${m.name}`}
                        onClick={() => setRemoving(m)}
                      >
                        <UserMinus className="h-4 w-4" aria-hidden="true" />
                      </Button>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </ScrollRegion>

      <div className="flex gap-2">
        <Select
          value={candidateId}
          onValueChange={setCandidateId}
          disabled={busy || usersQuery.isLoading || candidates.length === 0}
        >
          <SelectTrigger className="min-w-0 flex-1" aria-label="Adicionar gerente">
            <SelectValue
              placeholder={
                usersQuery.isLoading
                  ? 'Carregando...'
                  : candidates.length === 0
                    ? 'Nenhum outro gerente disponível'
                    : 'Selecione um gerente'
              }
            />
          </SelectTrigger>
          <SelectContent>
            {candidates.map((u) => (
              <SelectItem key={u.id} value={u.id}>
                {u.name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          type="button"
          variant="secondary"
          disabled={busy || candidateId.length === 0}
          onClick={handleAdd}
        >
          {addMutation.isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
          Adicionar
        </Button>
      </div>

      {/* Remover acesso — o aviso que não existia (a Bruna descobriu pelo sumiço). */}
      <AlertDialog open={removing !== null} onOpenChange={(open) => !open && setRemoving(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remover acesso</AlertDialogTitle>
            <AlertDialogDescription>
              <span className="text-foreground font-medium">{removing?.name}</span> deixa de ver o
              cliente <span className="text-foreground font-medium">{client.name}</span>. Nada é
              apagado: as conciliações e o histórico continuam no lugar; só o acesso dessa pessoa é
              removido.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={removeMutation.isPending}>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={handleRemove}
              disabled={removeMutation.isPending}
            >
              {removeMutation.isPending && (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              )}
              Remover acesso
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Tornar responsável — deixa claro que NINGUÉM perde o acesso. */}
      <AlertDialog open={promoting !== null} onOpenChange={(open) => !open && setPromoting(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Tornar responsável</AlertDialogTitle>
            <AlertDialogDescription>
              <span className="text-foreground font-medium">{promoting?.name}</span> passa a
              responder pelo cliente{' '}
              <span className="text-foreground font-medium">{client.name}</span> e aparece como
              responsável na lista. Ninguém perde o acesso:{' '}
              {responsible ? (
                <>
                  <span className="text-foreground font-medium">{responsible.name}</span> continua
                  na carteira como colaborador.
                </>
              ) : (
                'quem já tem acesso continua com acesso.'
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={assignMutation.isPending}>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              variant="default"
              onClick={handlePromote}
              disabled={assignMutation.isPending}
            >
              {assignMutation.isPending && (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              )}
              Confirmar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
