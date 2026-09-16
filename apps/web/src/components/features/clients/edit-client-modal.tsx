'use client';

/**
 * Modal "Editar Cliente" — Doc §9.3.
 *
 * Comportamento:
 *   - Nome pré-preenchido editável; status (Ativo/Inativo) também.
 *   - App Key e App Secret sempre VAZIOS com placeholder `••••••••`. Se o
 *     usuário deixar vazio, as credenciais existentes são mantidas. Se
 *     preencher, o "Testar conexão" é obrigatório antes de salvar.
 *   - Admin vê a seção "Gerentes com acesso" (86e390m4c —
 *     `client-managers-section.tsx`): quem tem acesso, quem é o responsável,
 *     adicionar, remover (com aviso nomeando quem perde o acesso) e tornar
 *     responsável (sem remover ninguém). As ações da carteira são IMEDIATAS,
 *     cada uma com a própria confirmação — o "Salvar" só grava os campos.
 *   - Manager (não-admin) não vê a seção; só nome/status/credenciais.
 *   - O corpo do formulário rola dentro do modal (`ScrollRegion`), com header e
 *     rodapé fixos: em 390px a seção de gerentes empurraria o "Salvar" para fora
 *     da viewport — o defeito que o gate de a11y NÃO mede (CLAUDE.md §7).
 *
 * Erros tratados:
 *   - PATCH /clients/{id} com `IncompleteCredentialsError` (400) → toast.
 *     A validação Zod já bloqueia a maioria dos casos client-side.
 *   - Demais erros → toast destrutivo com `userMessage`.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
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
import { ScrollRegion } from '@/components/ui/scroll-region';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useClientCategories } from '@/hooks/use-client-categories';
import { useTestConnection, useUpdateClient } from '@/hooks/use-clients';
import { ApiError } from '@/lib/api/client';
import type { Client, UpdateClientPayload } from '@/lib/api/clients';
import { hasPermission } from '@/lib/authz';
import { updateClientSchema, type UpdateClientFormValues } from '@/lib/validation/clients';
import { useAuthStore } from '@/stores/auth';

import { ClientManagersSection } from './client-managers-section';
import { PasswordInput } from './password-input';
import { TestConnectionButton, type TestConnectionState } from './test-connection-button';

interface EditClientModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  client: Client | null;
}

export function EditClientModal({ open, onOpenChange, client }: EditClientModalProps) {
  // Sprint 5 (R4): quem pode editar dados do cliente sai da MATRIZ
  // (`lib/authz`), não de um `role === 'admin'` local. Antes o papel vinha por
  // prop tipada `'admin' | 'manager'` — com os papéis de cliente no contrato,
  // isso deixaria de compilar e, pior, um `client_manager` cairia no ramo
  // "não-admin" por acidente em vez de por regra.
  const currentUser = useAuthStore((s) => s.user);
  const isAdmin = hasPermission(currentUser, 'edit_client');

  const [showKey, setShowKey] = useState(false);
  const [showSecret, setShowSecret] = useState(false);
  const [testState, setTestState] = useState<TestConnectionState>({ kind: 'idle' });
  // Última dupla submetida ao test (sucesso OU falha). Manter num ref evita
  // colocar testState como dep do useEffect — se estivesse, setar `failure`
  // dispararia o effect que rebobina pra idle antes da UI mostrar a mensagem.
  const lastTestedRef = useRef<{ key: string; secret: string } | null>(null);

  const updateMutation = useUpdateClient(client?.id ?? '');
  const testMutation = useTestConnection();
  // Catálogo de categorias (86e34jd8m) — só busca com o modal aberto.
  const categoriesQuery = useClientCategories({ enabled: open });
  const categories = categoriesQuery.data ?? [];

  const form = useForm<UpdateClientFormValues>({
    resolver: zodResolver(updateClientSchema),
    defaultValues: {
      name: '',
      active: 'active',
      omie_app_key: '',
      omie_app_secret: '',
      category_id: 'none',
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
        category_id: client.category?.id ?? 'none',
      });
      setShowKey(false);
      setShowSecret(false);
      setTestState({ kind: 'idle' });
      lastTestedRef.current = null;
      updateMutation.reset();
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
      // Sempre enviado: 'none' vira `null` (limpa), uuid troca — tri-estado do backend.
      category_id: values.category_id && values.category_id !== 'none' ? values.category_id : null,
    };
    if (credsBothFilled) {
      updatePayload.omie_app_key = (values.omie_app_key ?? '').trim();
      updatePayload.omie_app_secret = (values.omie_app_secret ?? '').trim();
    }

    try {
      await updateMutation.mutateAsync(updatePayload);
      toast.success('Cliente atualizado.');
      onOpenChange(false);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.userMessage : 'Não foi possível salvar as alterações.';
      toast.error(msg);
    }
  }

  const isSubmitting = updateMutation.isPending;
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
      {/* `flex` vence o `grid` do componente-base via twMerge: header e rodapé
          fixos, o miolo rola (`ScrollRegion`) quando o modal passa da viewport. */}
      <DialogContent className="flex max-h-[calc(100dvh-2rem)] flex-col sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Editar Cliente</DialogTitle>
          <DialogDescription>
            Deixe os campos de credenciais vazios para manter os valores atuais.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="flex min-h-0 flex-1 flex-col gap-4"
            noValidate
          >
            <ScrollRegion label="Dados do cliente" className="-mx-1 min-h-0 flex-1 space-y-4 px-1">
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

              <FormField
                control={form.control}
                name="category_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Categoria</FormLabel>
                    <Select
                      value={field.value ?? 'none'}
                      onValueChange={field.onChange}
                      disabled={inputsDisabled || categoriesQuery.isLoading}
                    >
                      <FormControl>
                        <SelectTrigger aria-label="Categoria do cliente">
                          <SelectValue placeholder="Sem categoria" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value="none">Sem categoria</SelectItem>
                        {categories.map((c) => (
                          <SelectItem key={c.id} value={c.id}>
                            {c.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {isAdmin && client && (
                <ClientManagersSection client={client} disabled={inputsDisabled} />
              )}
            </ScrollRegion>

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
