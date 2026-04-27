'use client';

/**
 * Modal "Editar Cliente" — Doc §9.3.
 *
 * Comportamento:
 *   - Nome pré-preenchido editável; status (Ativo/Inativo) também.
 *   - App Key e App Secret sempre VAZIOS com placeholder `••••••••`. Se o
 *     usuário deixar vazio, as credenciais existentes são mantidas. Se
 *     preencher, o "Testar conexão" é obrigatório antes de salvar.
 *   - Admin vê e pode trocar o gerente responsável; o `assign` chama um
 *     endpoint SEPARADO (`PATCH /clients/{id}/assign`). Reatribuição e
 *     atualização de campos rolam em paralelo (Promise.all).
 *   - Manager (não-admin) não vê a seção de gerente; só nome/status/credenciais.
 *
 * Erros tratados:
 *   - PATCH /clients/{id} com `IncompleteCredentialsError` (400) → toast.
 *     A validação Zod já bloqueia a maioria dos casos client-side.
 *   - `FORBIDDEN` em assign (manager inválido) → toast.
 *   - Demais erros → toast destrutivo com `userMessage`.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useAssignClient, useTestConnection, useUpdateClient } from '@/hooks/use-clients';
import { useUsersList } from '@/hooks/use-users';
import { ApiError } from '@/lib/api/client';
import type { Client, UpdateClientPayload } from '@/lib/api/clients';
import { updateClientSchema, type UpdateClientFormValues } from '@/lib/validation/clients';

import { PasswordInput } from './password-input';
import { TestConnectionButton, type TestConnectionState } from './test-connection-button';

interface EditClientModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  client: Client | null;
  currentUserRole: 'admin' | 'manager';
}

export function EditClientModal({
  open,
  onOpenChange,
  client,
  currentUserRole,
}: EditClientModalProps) {
  const isAdmin = currentUserRole === 'admin';

  const [showKey, setShowKey] = useState(false);
  const [showSecret, setShowSecret] = useState(false);
  const [testState, setTestState] = useState<TestConnectionState>({ kind: 'idle' });
  // Última dupla submetida ao test (sucesso OU falha). Manter num ref evita
  // colocar testState como dep do useEffect — se estivesse, setar `failure`
  // dispararia o effect que rebobina pra idle antes da UI mostrar a mensagem.
  const lastTestedRef = useRef<{ key: string; secret: string } | null>(null);

  const updateMutation = useUpdateClient(client?.id ?? '');
  const assignMutation = useAssignClient(client?.id ?? '');
  const testMutation = useTestConnection();

  // Lista de gerentes só importa para admin. `pageSize=100` cobre
  // o tamanho esperado do time interno da Hologram (MVP) — se passar disso,
  // S15+ adicionará filtro server-side.
  const usersQuery = useUsersList({ page: 1, pageSize: 100 }, { enabled: open && isAdmin });
  const managers = useMemo(
    () => (usersQuery.data?.data ?? []).filter((u) => u.active && u.role === 'manager'),
    [usersQuery.data],
  );

  const form = useForm<UpdateClientFormValues>({
    resolver: zodResolver(updateClientSchema),
    defaultValues: {
      name: '',
      active: 'active',
      omie_app_key: '',
      omie_app_secret: '',
      manager_id: undefined,
    },
    mode: 'onSubmit',
  });

  const watchedKey = useWatch({ control: form.control, name: 'omie_app_key' });
  const watchedSecret = useWatch({ control: form.control, name: 'omie_app_secret' });

  // Sincroniza o form sempre que o modal abre (ou o cliente-alvo muda).
  useEffect(() => {
    if (open && client) {
      form.reset({
        name: client.name,
        active: client.active ? 'active' : 'inactive',
        omie_app_key: '',
        omie_app_secret: '',
        manager_id: client.responsible_manager?.id,
      });
      setShowKey(false);
      setShowSecret(false);
      setTestState({ kind: 'idle' });
      lastTestedRef.current = null;
      updateMutation.reset();
      assignMutation.reset();
      testMutation.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, client]);

  // Volta a `idle` quando o usuário edita key/secret APÓS um teste. Reage só
  // a mudanças dos campos — testState NÃO é dependência (ver create-client-modal).
  useEffect(() => {
    if (lastTestedRef.current === null) return;
    const { key, secret } = lastTestedRef.current;
    if (watchedKey !== key || watchedSecret !== secret) {
      lastTestedRef.current = null;
      setTestState({ kind: 'idle' });
    }
  }, [watchedKey, watchedSecret]);

  const credsFilled =
    (watchedKey ?? '').trim().length > 0 || (watchedSecret ?? '').trim().length > 0;
  const credsBothFilled =
    (watchedKey ?? '').trim().length > 0 && (watchedSecret ?? '').trim().length > 0;

  async function handleTest() {
    const key = (form.getValues('omie_app_key') ?? '').trim();
    const secret = (form.getValues('omie_app_secret') ?? '').trim();
    if (!key || !secret) return;
    setTestState({ kind: 'testing' });
    try {
      const res = await testMutation.mutateAsync({
        omie_app_key: key,
        omie_app_secret: secret,
      });
      lastTestedRef.current = { key, secret };
      setTestState(res.ok ? { kind: 'success' } : { kind: 'failure', message: res.message });
    } catch (err) {
      lastTestedRef.current = { key, secret };
      const message =
        err instanceof ApiError ? err.userMessage : 'Não foi possível testar a conexão.';
      setTestState({ kind: 'failure', message });
    }
  }

  async function onSubmit(values: UpdateClientFormValues) {
    if (!client) return;

    // Se preencheu credenciais, exige teste OK. O guard do botão já cobre,
    // mas mantemos a verificação para o caso de submit por Enter.
    if (credsFilled && testState.kind !== 'success') {
      toast.error('Teste a conexão antes de salvar as novas credenciais.');
      return;
    }

    const updatePayload: UpdateClientPayload = {
      name: values.name,
      active: values.active === 'active',
    };
    if (credsBothFilled) {
      updatePayload.omie_app_key = (values.omie_app_key ?? '').trim();
      updatePayload.omie_app_secret = (values.omie_app_secret ?? '').trim();
    }

    const ops: Promise<unknown>[] = [updateMutation.mutateAsync(updatePayload)];

    // Reatribuição é admin-only e só chamada se o gerente realmente mudou.
    const previousManagerId = client.responsible_manager?.id;
    if (isAdmin && values.manager_id && values.manager_id !== previousManagerId) {
      ops.push(assignMutation.mutateAsync({ user_id: values.manager_id }));
    }

    try {
      await Promise.all(ops);
      toast.success('Cliente atualizado.');
      onOpenChange(false);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.userMessage : 'Não foi possível salvar as alterações.';
      toast.error(msg);
    }
  }

  const isSubmitting = updateMutation.isPending || assignMutation.isPending;
  const isTesting = testState.kind === 'testing';
  const inputsDisabled = isSubmitting || isTesting;

  const canTest =
    !inputsDisabled &&
    (watchedKey ?? '').trim().length > 0 &&
    (watchedSecret ?? '').trim().length > 0;

  // Save liberado quando: nome preenchido E (credenciais vazias OU teste OK).
  const canSubmit =
    !isSubmitting &&
    !isTesting &&
    (form.getValues('name') ?? '').trim().length > 0 &&
    (!credsFilled || testState.kind === 'success');

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Editar Cliente</DialogTitle>
          <DialogDescription>
            Deixe os campos de credenciais vazios para manter os valores atuais.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Nome do cliente</FormLabel>
                  <FormControl>
                    <Input autoComplete="off" autoFocus disabled={inputsDisabled} {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="omie_app_key"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>App Key Omie</FormLabel>
                  <FormControl>
                    <PasswordInput
                      visible={showKey}
                      onToggle={() => setShowKey((v) => !v)}
                      disabled={inputsDisabled}
                      autoComplete="off"
                      placeholder="••••••••"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="omie_app_secret"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>App Secret Omie</FormLabel>
                  <FormControl>
                    <PasswordInput
                      visible={showSecret}
                      onToggle={() => setShowSecret((v) => !v)}
                      disabled={inputsDisabled}
                      autoComplete="off"
                      placeholder="••••••••"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <TestConnectionButton state={testState} disabled={!canTest} onClick={handleTest} />

            <FormField
              control={form.control}
              name="active"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Status</FormLabel>
                  <Select
                    value={field.value}
                    onValueChange={field.onChange}
                    disabled={inputsDisabled}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value="active">Ativo</SelectItem>
                      <SelectItem value="inactive">Inativo</SelectItem>
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

            {isAdmin && (
              <FormField
                control={form.control}
                name="manager_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Gerente Responsável</FormLabel>
                    <Select
                      value={field.value ?? ''}
                      onValueChange={field.onChange}
                      disabled={inputsDisabled || usersQuery.isLoading}
                    >
                      <FormControl>
                        <SelectTrigger>
                          <SelectValue
                            placeholder={
                              usersQuery.isLoading ? 'Carregando...' : 'Selecione um gerente'
                            }
                          />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {managers.map((m) => (
                          <SelectItem key={m.id} value={m.id}>
                            {m.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
            )}

            <DialogFooter className="gap-2 sm:gap-2">
              <Button
                type="button"
                variant="outline"
                onClick={() => onOpenChange(false)}
                disabled={isSubmitting}
              >
                Cancelar
              </Button>
              <Button type="submit" disabled={!canSubmit}>
                {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                Salvar
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  );
}
