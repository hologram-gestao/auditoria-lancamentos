'use client';

/**
 * Tela de login — Doc §7.1.
 *
 * Comportamento:
 *   - Botão "Entrar" desabilitado se email ou senha vazios; troca para spinner+"Entrando..." em flight.
 *   - Senha com toggle de visibilidade (ícone de olho).
 *   - Em sucesso: setUser no Zustand + redireciona para /clientes (server-side via router.replace).
 *   - Em erro: mensagem inline genérica; PT-BR.
 *   - Sem link de "esqueci senha" (admin reseta).
 */

import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { useForm } from 'react-hook-form';

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
import { loginSchema, type LoginFormValues } from '@/lib/validation/auth';
import { useAuthStore } from '@/stores/auth';

export default function LoginPage() {
  const router = useRouter();
  const setUser = useAuthStore((s) => s.setUser);
  const [submitError, setSubmitError] = useState<string | null>(null);

  const form = useForm<LoginFormValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: '', password: '' },
    mode: 'onSubmit',
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
        if (err.status === 429) {
          setSubmitError('Muitas tentativas. Aguarde 1 minuto antes de tentar novamente.');
          return;
        }
        // 401 (e qualquer outro 4xx do login) cai no userMessage genérico do backend.
        setSubmitError(err.userMessage);
        return;
      }
      setSubmitError('Ocorreu um erro inesperado. Tente novamente.');
    }
  }

  return (
    <div className="w-full max-w-md">
      <div className="mb-8 text-center">
        <h1 className="text-2xl font-semibold tracking-tight">
          Sistema de Auditoria de Lançamentos
        </h1>
        <p className="text-muted-foreground mt-2 text-sm">Entre com seu acesso da Hologram.</p>
      </div>

      <div className="bg-card rounded-lg border p-6 shadow-sm">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-5" noValidate>
            <FormField
              control={form.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>E-mail</FormLabel>
                  <FormControl>
                    <Input
                      type="email"
                      autoComplete="email"
                      autoFocus
                      placeholder="seu.email@hologram.com.br"
                      disabled={isSubmitting}
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="password"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Senha</FormLabel>
                  {/* O `<PasswordInput>` é filho DIRETO do `<FormControl>` de
                      propósito: o Slot do Radix entrega o `id` do `FormItem`
                      ao primeiro filho, e com uma `<div>` no meio o rótulo
                      "Senha" apontava para a div — o input ficava sem nome
                      acessível. */}
                  <FormControl>
                    <PasswordInput
                      autoComplete="current-password"
                      disabled={isSubmitting}
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <Button type="submit" className="w-full" disabled={isDisabled}>
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
              <p role="alert" aria-live="polite" className="text-destructive text-center text-sm">
                {submitError}
              </p>
            )}
          </form>
        </Form>
      </div>
    </div>
  );
}
