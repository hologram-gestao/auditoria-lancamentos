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
import { Eye, EyeOff, Loader2 } from 'lucide-react';
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
import { login as loginRequest } from '@/lib/api/auth';
import { ApiError, NetworkError } from '@/lib/api/client';
import { loginSchema, type LoginFormValues } from '@/lib/validation/auth';
import { useAuthStore } from '@/stores/auth';

export default function LoginPage() {
  const router = useRouter();
  const setUser = useAuthStore((s) => s.setUser);
  const [showPassword, setShowPassword] = useState(false);
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
                  <FormControl>
                    <div className="relative">
                      <Input
                        type={showPassword ? 'text' : 'password'}
                        autoComplete="current-password"
                        disabled={isSubmitting}
                        className="pr-10"
                        {...field}
                      />
                      <button
                        type="button"
                        onClick={() => setShowPassword((v) => !v)}
                        aria-label={showPassword ? 'Ocultar senha' : 'Mostrar senha'}
                        aria-pressed={showPassword}
                        className="text-muted-foreground hover:text-foreground focus-visible:ring-ring absolute inset-y-0 right-0 flex items-center rounded-md pr-3 focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                        tabIndex={0}
                      >
                        {showPassword ? (
                          <EyeOff className="h-4 w-4" aria-hidden="true" />
                        ) : (
                          <Eye className="h-4 w-4" aria-hidden="true" />
                        )}
                      </button>
                    </div>
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
