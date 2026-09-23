'use client';

/**
 * Modal "Novo Cliente" — Doc §9.2, revisto na Sprint 9 (R4).
 *
 * **O cliente deixou de ser um par de credenciais com nome.** Até aqui o
 * Salvar ficava preso ao "Testar conexão", e era esse gate que recusava 4 em
 * cada 5 clientes de um escritório contábil — a maioria não usa o Omie, e
 * muitos não usam sistema nenhum. Agora:
 *
 *   1. nome (+ organização/categoria) bastam: **Salvar habilitado sem teste**,
 *      e o cliente nasce em "sem origem conectada";
 *   2. a credencial vive numa seção OPCIONAL, "Conectar uma origem agora";
 *   3. preencheu QUALQUER um dos dois campos → o gate do "Testar conexão"
 *      volta a valer, como sempre valeu;
 *   4. editar key/secret depois do teste invalida o sucesso e exige novo teste.
 *
 * O gate do front continua existindo, mas **não é mais a única barreira**: o
 * servidor verifica a credencial contra o provedor antes de persistir, e
 * `POST /clients` com credencial inválida não cria nem cliente nem conexão.
 *
 * Erros tratados:
 *   - Falha do test-connection → `ok=false` (200) → mensagem inline (não joga no toast).
 *   - Falha de transporte/rede no teste → `ApiError` → mensagem inline também.
 *   - Falha no `createClient` → toast destrutivo com `userMessage` do backend.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useForm, useWatch } from 'react-hook-form';
import { toast } from 'sonner';

import {
  OrganizationLoadError,
  organizationOptionLabel,
  useOrganizationOptions,
} from '@/components/features/organizations/organization-select';
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
import { useCreateClient, useTestConnection } from '@/hooks/use-clients';
import { ApiError } from '@/lib/api/client';
import { hasPermission, isPlatformScoped } from '@/lib/authz';
import { makeCreateClientSchema, type CreateClientFormValues } from '@/lib/validation/clients';
import { useAuthStore } from '@/stores/auth';

import { PasswordInput } from './password-input';
import { TestConnectionButton, type TestConnectionState } from './test-connection-button';

interface CreateClientModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CreateClientModal({ open, onOpenChange }: CreateClientModalProps) {
  const [showKey, setShowKey] = useState(false);
  const [showSecret, setShowSecret] = useState(false);
  const [testState, setTestState] = useState<TestConnectionState>({ kind: 'idle' });
  // Última dupla submetida ao test (sucesso OU falha). Ao editar key/secret
  // o useEffect compara contra esse ref e volta a idle. Manter num ref evita
  // o testState como dependência do effect — se ele estivesse, setar `failure`
  // dispararia o effect que rebobina pra idle antes da UI renderizar a mensagem.
  const lastTestedRef = useRef<{ key: string; secret: string } | null>(null);

  const createMutation = useCreateClient();
  const testMutation = useTestConnection();
  // Catálogo de categorias (86e34jd8m) — só busca com o modal aberto.
  const categoriesQuery = useClientCategories({ enabled: open });

  // Onde o cliente NASCE (86e36ed1d): a plataforma escolhe e é obrigada a
  // escolher; o staff nem vê o campo, porque o backend usa a organização da
  // LINHA dele e recusa payload divergente com 403.
  const currentUser = useAuthStore((s) => s.user);
  const isPlatform = isPlatformScoped(currentUser);
  // R5: conectar origem é UMA permissão, aqui e na tela do cliente.
  const canManageConnections = hasPermission(currentUser, 'manage_client_connections');
  const {
    organizations,
    isLoading: organizationsLoading,
    isError: organizationsError,
  } = useOrganizationOptions({
    enabled: isPlatform && open,
    activeOnly: true,
  });
  const schema = useMemo(
    () => makeCreateClientSchema({ requireOrganization: isPlatform }),
    [isPlatform],
  );

  const form = useForm<CreateClientFormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: '',
      omie_app_key: '',
      omie_app_secret: '',
      category_id: 'none',
      organization_id: '',
    },
    mode: 'onSubmit',
  });

  const watchedKey = useWatch({ control: form.control, name: 'omie_app_key' });
  const watchedSecret = useWatch({ control: form.control, name: 'omie_app_secret' });
  const watchedOrganization = useWatch({ control: form.control, name: 'organization_id' });

  // O catálogo é POR organização (86e36ecqz) e a plataforma recebe o de TODAS.
  // A categoria precisa ser DA organização de destino: `_assert_category_exists`
  // valida no catálogo dela e devolve 400 se for de outra. Oferecer a lista
  // inteira seria mostrar opção que o servidor recusa (§4.9) — e, com duas
  // organizações tendo "Varejo", duas opções idênticas e indistinguíveis.
  const visibleCategories = useMemo(() => {
    const all = categoriesQuery.data ?? [];
    return isPlatform ? all.filter((c) => c.organization_id === watchedOrganization) : all;
  }, [categoriesQuery.data, isPlatform, watchedOrganization]);

  // Trocar a organização invalida a categoria escolhida: limpar no MESMO
  // momento, senão o POST sai com um par que o backend recusa.
  useEffect(() => {
    if (!isPlatform) return;
    form.setValue('category_id', 'none');
    // `form` é estável; reagir só à troca de organização.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watchedOrganization, isPlatform]);

  // Reset completo quando o modal fecha — não vaza credenciais entre aberturas.
  useEffect(() => {
    if (!open) {
      form.reset();
      setShowKey(false);
      setShowSecret(false);
      setTestState({ kind: 'idle' });
      lastTestedRef.current = null;
      createMutation.reset();
      testMutation.reset();
    }
    // form/mutations são estáveis; rodar só quando o modal abrir/fechar.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Volta a `idle` quando o usuário edita key/secret APÓS um teste (success
  // ou failure). Reage só a mudanças nos campos — testState NÃO é dependência.
  useEffect(() => {
    if (lastTestedRef.current === null) return;
    const { key, secret } = lastTestedRef.current;
    if (watchedKey !== key || watchedSecret !== secret) {
      lastTestedRef.current = null;
      setTestState({ kind: 'idle' });
    }
  }, [watchedKey, watchedSecret]);

  async function handleTest() {
    const key = form.getValues('omie_app_key').trim();
    const secret = form.getValues('omie_app_secret').trim();
    if (!key || !secret) {
      // Defesa: o botão fica disabled, mas se chegou aqui evita request inútil.
      return;
    }
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

  async function onSubmit(values: CreateClientFormValues) {
    const key = (values.omie_app_key ?? '').trim();
    const secret = (values.omie_app_secret ?? '').trim();
    const wantsOrigin = key.length > 0 || secret.length > 0;
    if (wantsOrigin && testState.kind !== 'success') {
      // UX guard — não deveria atingir esse caminho com o botão disabled, mas
      // o submit por Enter passa por aqui.
      toast.error('Teste a conexão antes de salvar as credenciais.');
      return;
    }
    try {
      const categoryId =
        values.category_id && values.category_id !== 'none' ? values.category_id : undefined;
      await createMutation.mutateAsync({
        name: values.name,
        // Omitidas quando não há origem: mandar `""` faria o backend tratar
        // como credencial presente (e `min_length=1` recusaria com 422).
        ...(wantsOrigin ? { omie_app_key: key, omie_app_secret: secret } : {}),
        ...(categoryId ? { category_id: categoryId } : {}),
        // Só a plataforma manda o campo: o staff omitindo é o que faz o
        // backend usar a organização da própria linha.
        ...(values.organization_id ? { organization_id: values.organization_id } : {}),
      });
      toast.success(
        wantsOrigin
          ? 'Cliente criado com a origem conectada.'
          : 'Cliente criado. Conecte uma origem quando houver uma para ligar.',
      );
      onOpenChange(false);
    } catch (err) {
      const msg = err instanceof ApiError ? err.userMessage : 'Não foi possível criar o cliente.';
      toast.error(msg);
    }
  }

  const isSubmitting = createMutation.isPending;
  const isTesting = testState.kind === 'testing';
  const inputsDisabled = isSubmitting || isTesting;

  const canTest =
    !inputsDisabled &&
    (watchedKey ?? '').trim().length > 0 &&
    (watchedSecret ?? '').trim().length > 0;

  // S9 (R4): o gate do teste só vale quando há ALGUMA credencial no formulário.
  // Com os dois campos vazios o cliente nasce sem origem, e o Salvar libera.
  const credentialsTouched =
    (watchedKey ?? '').trim().length > 0 || (watchedSecret ?? '').trim().length > 0;

  const canSubmit =
    !isSubmitting &&
    !isTesting &&
    form.getValues('name').trim().length > 0 &&
    (!credentialsTouched || testState.kind === 'success');

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* `flex` vence o `grid` do componente-base via twMerge: header e rodapé
          fixos, o miolo rola (`ScrollRegion`). Sem isto, em 390px a seção de
          origem empurraria o "Salvar" para fora da viewport — o defeito que o
          gate de a11y NÃO mede (CLAUDE.md §7). */}
      <DialogContent className="flex max-h-[calc(100dvh-2rem)] flex-col sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Novo Cliente</DialogTitle>
          <DialogDescription>
            Conectar uma origem é opcional. Se você conectar, as credenciais são criptografadas e
            nunca persistem em texto plano.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            className="flex min-h-0 flex-1 flex-col gap-4"
            noValidate
          >
            <ScrollRegion
              label="Dados do novo cliente"
              className="-mx-1 min-h-0 flex-1 space-y-4 px-1"
            >
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Nome do cliente</FormLabel>
                    <FormControl>
                      <Input
                        autoComplete="off"
                        autoFocus
                        disabled={inputsDisabled}
                        placeholder="Como a sua organização se refere ao cliente"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* Só a plataforma escolhe onde o cliente nasce (86e36ed1d). O staff
                não vê o campo: a organização dele vem da LINHA, no servidor. */}
              {isPlatform && (
                <FormField
                  control={form.control}
                  name="organization_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Organização</FormLabel>
                      <Select
                        value={field.value ?? ''}
                        onValueChange={field.onChange}
                        disabled={inputsDisabled || organizationsLoading}
                      >
                        <FormControl>
                          <SelectTrigger aria-label="Organização do cliente">
                            <SelectValue placeholder="Selecione a organização" />
                          </SelectTrigger>
                        </FormControl>
                        <SelectContent>
                          {organizations.map((o) => (
                            <SelectItem key={o.id} value={o.id}>
                              {organizationOptionLabel(o)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      {organizationsError && <OrganizationLoadError />}
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}

              <FormField
                control={form.control}
                name="category_id"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Categoria (opcional)</FormLabel>
                    <Select
                      value={field.value ?? 'none'}
                      onValueChange={field.onChange}
                      disabled={
                        inputsDisabled ||
                        categoriesQuery.isLoading ||
                        // A plataforma escolhe a organização ANTES: sem ela não há
                        // catálogo de onde escolher.
                        (isPlatform && !watchedOrganization)
                      }
                    >
                      <FormControl>
                        <SelectTrigger aria-label="Categoria do cliente">
                          <SelectValue placeholder="Sem categoria" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        <SelectItem value="none">Sem categoria</SelectItem>
                        {visibleCategories.map((c) => (
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

              {/* Origem é OPCIONAL (R4) e fica visualmente destacada como um
                bloco à parte, para o cadastro não parecer incompleto sem ela.
                Some para quem não tem `manage_client_connections`: conectar
                origem é a mesma permissão aqui e na tela do cliente (R5). */}
              {canManageConnections && (
                <fieldset className="space-y-4 rounded-lg border p-4">
                  <legend className="px-1 text-sm font-medium">Conectar uma origem agora</legend>
                  <p className="text-muted-foreground text-sm">
                    Opcional. Sem credencial o cliente é criado do mesmo jeito e abre em &quot;sem
                    origem conectada&quot; — dá para conectar depois, na tela dele.
                  </p>

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
                            {...field}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />

                  <TestConnectionButton
                    state={testState}
                    disabled={!canTest}
                    onClick={handleTest}
                  />

                  {credentialsTouched && testState.kind !== 'success' && (
                    <p className="text-muted-foreground text-sm">
                      Com credencial preenchida, o teste é obrigatório antes de salvar.
                    </p>
                  )}
                </fieldset>
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
