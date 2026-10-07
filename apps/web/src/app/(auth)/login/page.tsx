'use client';

/**
 * Tela de login — Doc §7.1.
 *
 * Comportamento:
 *   - Validação `onTouched` (86e2n39eg): o formato do e-mail é conferido quando a
 *     pessoa SAI do campo e, depois do primeiro erro, a cada tecla. Não valida
 *     durante a primeira digitação (acusar "E-mail inválido." no primeiro
 *     caractere pune quem ainda está escrevendo). O e-mail vai com `trim`.
 *   - Botão "Entrar" desabilitado só com email ou senha VAZIOS, nunca por formato
 *     inválido: botão travado sem explicação é pior de usar e de ler em leitor de
 *     tela; o certo é deixar clicar e mostrar o erro no campo. Spinner+"Entrando..." em flight.
 *   - Senha com toggle de visibilidade (ícone de olho).
 *   - Em sucesso: setUser no Zustand + redireciona para /clientes (server-side via router.replace).
 *   - Em erro: mensagem inline genérica; PT-BR. Só a mensagem fica vermelha, com ícone;
 *     o rótulo do campo segue na cor do texto (86e3h1h75).
 *   - Visual (86e3h1h75): card estreito com logomark e título dentro, botão no verde da
 *     marca, tema Hologram fixo e painel de marca vêm do layout do grupo `(auth)`. Os
 *     textos novos (subtítulo, placeholder) vêm de `components/landing/content.ts`.
 *   - Sem link de "esqueci senha" (decisão registrada: não há provedor de e-mail
 *     decidido, CLAUDE.md §10). Em vez do link, uma linha abaixo do formulário
 *     diz o que fazer (86e2u5140): falar com o administrador da conta, que pode
 *     redefinir a senha pela plataforma (86e3ewukz). O texto é GENÉRICO de
 *     propósito — não confirma cadastro nenhum (§3.9), então vale antes do erro.
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { AlertCircle, Loader2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { useForm } from 'react-hook-form';

import { login as copy } from '@/components/landing/content';
import { BrandMark } from '@/components/shared/brand-mark';
import { Button } from '@/components/ui/button';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { PasswordInput } from '@/components/ui/password-input';
import { login as loginRequest } from '@/lib/api/auth';
import { ApiError, NetworkError } from '@/lib/api/client';
import { PRODUCT_TITLE } from '@/lib/brand';
import { loginSchema, type LoginFormValues } from '@/lib/validation/auth';
import { useAuthStore } from '@/stores/auth';

/** Ícone das mensagens de erro: decorativo, a mensagem é o texto ao lado. */
const ERROR_ICON = <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />;

export default function LoginPage() {
  const router = useRouter();
  const setUser = useAuthStore((s) => s.setUser);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const form = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: '', password: '' },
    mode: 'onTouched',
  });

  const email = form.watch('email');
  const password = form.watch('password');
  const isSubmitting = form.formState.isSubmitting;
  const isDisabled = isSubmitting || email.length === 0 || password.length === 0;

  async function onSubmit(values: LoginFormValues) {
    setSubmitError(null);
    try {
      const user = await loginRequest(values);
      setUser(user);
      router.replace('/clientes');
    } catch (err) {
      if (err instanceof NetworkError) {
        setSubmitError(err.userMessage);
        return;
      }
      if (err instanceof ApiError) {
        // 401 (mensagem genérica, §3.9) e 429 caem no userMessage do backend: é o
        // servidor que sabe a janela do limite (5 minutos por e-mail, 86e3anx10).
        setSubmitError(err.userMessage);
        return;
      }
      setSubmitError('Ocorreu um erro inesperado. Tente novamente.');
    }
  }

  return (
    <div className="lp-auth-card bg-card w-full max-w-sm rounded-xl border p-8">
      <div className="mb-8">
        {/* Logomark e título DENTRO do card (86e3h1h75): num lugar só, em qualquer
            largura. `text-logo` é branco no tema Hologram fixo da página. */}
        <BrandMark className="text-logo h-8" />
        <h1 className="mt-5 text-2xl font-semibold tracking-tight">{PRODUCT_TITLE}</h1>
        <p className="text-muted-foreground mt-1 text-sm">{copy.subtitle}</p>
      </div>

      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-5" noValidate>
          <FormField
            control={form.control}
            name="email"
            render={({ field }) => (
              <FormItem>
                {/* O rótulo NÃO fica vermelho no erro: quem avisa é a mensagem, com
                    ícone. Rótulo e mensagem vermelhos gritavam duas vezes a mesma coisa. */}
                <FormLabel className="text-foreground">E-mail</FormLabel>
                <FormControl>
                  <Input
                    type="email"
                    autoComplete="email"
                    autoFocus
                    placeholder={copy.emailPlaceholder}
                    disabled={isSubmitting}
                    className="h-11"
                    {...field}
                  />
                </FormControl>
                <FormMessage icon={ERROR_ICON} />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name="password"
            render={({ field }) => (
              <FormItem>
                <FormLabel className="text-foreground">Senha</FormLabel>
                {/* O `<PasswordInput>` é filho DIRETO do `<FormControl>` de
                    propósito: o Slot do Radix entrega o `id` do `FormItem`
                    ao primeiro filho, e com uma `<div>` no meio o rótulo
                    "Senha" apontava para a div — o input ficava sem nome
                    acessível. */}
                <FormControl>
                  <PasswordInput
                    autoComplete="current-password"
                    disabled={isSubmitting}
                    className="h-11"
                    {...field}
                  />
                </FormControl>
                <FormMessage icon={ERROR_ICON} />
              </FormItem>
            )}
          />

          {/* O primário da página é o verde da marca (`variant="brand"`), como na
              landing. Desabilitado ele fica a 50 % (a base do `Button`), nunca um
              cinza sólido: era o elemento mais pesado da tela antes de a pessoa
              digitar qualquer coisa. */}
          <Button type="submit" variant="brand" size="lg" className="w-full" disabled={isDisabled}>
            {isSubmitting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                <span>Entrando...</span>
              </>
            ) : (
              'Entrar'
            )}
          </Button>

          {submitError !== null && (
            <p
              role="alert"
              aria-live="polite"
              className="text-destructive flex items-start gap-1.5 text-sm font-medium"
            >
              {ERROR_ICON}
              {submitError}
            </p>
          )}
        </form>
      </Form>
      {/* 86e2u5140: o caminho de recuperação vigente, visível ANTES do erro (o
          erro é genérico e não ajuda a pessoa a se localizar). Sem revelar se
          um e-mail existe: a instrução é a mesma para qualquer pessoa. */}
      <p className="text-muted-foreground mt-6 text-sm" data-testid="login-help">
        Esqueceu a senha? Fale com o administrador da sua conta: ele pode redefini-la para você.
      </p>
    </div>
  );
}
