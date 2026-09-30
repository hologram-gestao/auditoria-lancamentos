'use client';

/**
 * Formulário de contato da landing (86e3fr9vz), no `#contato`.
 *
 *   - react-hook-form + zod (`lib/validation/lead.ts`, mesmos limites do backend);
 *   - honeypot `website`: fora da tela (não `display:none`, que bot ignora), fora da
 *     árvore acessível (`aria-hidden`), fora do Tab (`tabIndex={-1}`), sem
 *     preenchimento automático. Vai sempre no payload; o servidor responde igual;
 *   - estados: enviando (botão desabilitado + spinner + `aria-busy`), sucesso (a
 *     confirmação entra na região `role="status"`, que já existe desde o início
 *     para o leitor de tela anunciar, e recebe o foco), erro (alerta inline com
 *     `role="alert"`, botão reabilitado).
 */
import { zodResolver } from '@hookform/resolvers/zod';
import { Loader2 } from 'lucide-react';
import { useRef, useState } from 'react';
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
import { Textarea } from '@/components/ui/textarea';
import { useSubmitLead } from '@/hooks/use-leads';
import { ApiError, NetworkError } from '@/lib/api/client';
import {
  LEAD_FORM_DEFAULTS,
  LEAD_MESSAGE_MAX,
  leadSchema,
  type LeadFormValues,
} from '@/lib/validation/lead';

import { contact } from './content';
import { ManualDownload } from './manual-download';

function errorMessageFor(err: unknown): string {
  if (err instanceof NetworkError) return err.userMessage;
  if (err instanceof ApiError) {
    if (err.status === 429) return contact.errors.rateLimited;
    if (err.status === 400) return err.userMessage;
  }
  return contact.errors.generic;
}

function OptionalMark() {
  return <span className="text-muted-foreground font-normal"> {contact.fields.optional}</span>;
}

export function ContactForm() {
  const form = useForm<LeadFormValues>({
    resolver: zodResolver(leadSchema),
    defaultValues: LEAD_FORM_DEFAULTS,
    mode: 'onTouched',
  });
  const mutation = useSubmitLead();
  const [sent, setSent] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const statusRef = useRef<HTMLDivElement>(null);

  async function onSubmit(values: LeadFormValues) {
    setSubmitError(null);
    try {
      await mutation.mutateAsync({
        name: values.name,
        email: values.email,
        company: values.company || null,
        whatsapp: values.whatsapp || null,
        message: values.message || null,
        consent: true,
        website: values.website,
      });
      setSent(true);
      // O botão que tinha o foco sai da tela; o foco vai para a confirmação.
      requestAnimationFrame(() => statusRef.current?.focus());
    } catch (err) {
      setSubmitError(errorMessageFor(err));
    }
  }

  const pending = mutation.isPending;

  return (
    <div>
      <div
        ref={statusRef}
        role="status"
        tabIndex={-1}
        className="focus-visible:ring-ring rounded-lg focus-visible:outline-none focus-visible:ring-2"
      >
        {sent && (
          <div className="flex flex-col items-center gap-4 py-10 text-center">
            <svg
              aria-hidden="true"
              viewBox="0 0 56 56"
              className="lp-check text-success h-14 w-14"
              fill="none"
              stroke="currentColor"
              strokeWidth={3}
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <circle cx="28" cy="28" r="25" />
              <path d="M17 29l7 7 15-16" />
            </svg>
            <p className="text-xl font-semibold">{contact.successTitle}</p>
            <p className="text-muted-foreground">{contact.successText}</p>
            <div className="mt-2">
              <ManualDownload id="manual-confirmacao" variant="link" />
            </div>
          </div>
        )}
      </div>

      {!sent && (
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(onSubmit)}
            noValidate
            aria-busy={pending}
            className="relative grid gap-5"
          >
            <div className="grid gap-5 sm:grid-cols-2">
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{contact.fields.name}</FormLabel>
                    <FormControl>
                      <Input autoComplete="name" aria-required="true" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>{contact.fields.email}</FormLabel>
                    <FormControl>
                      <Input
                        type="email"
                        inputMode="email"
                        autoComplete="email"
                        aria-required="true"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="company"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {contact.fields.company}
                      <OptionalMark />
                    </FormLabel>
                    <FormControl>
                      <Input autoComplete="organization" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="whatsapp"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>
                      {contact.fields.whatsapp}
                      <OptionalMark />
                    </FormLabel>
                    <FormControl>
                      <Input type="tel" inputMode="tel" autoComplete="tel" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <FormField
              control={form.control}
              name="message"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>
                    {contact.fields.message}
                    <OptionalMark />
                  </FormLabel>
                  <FormControl>
                    <Textarea rows={4} maxLength={LEAD_MESSAGE_MAX} {...field} />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="consent"
              render={({ field }) => (
                <FormItem className="space-y-2">
                  <div className="flex items-start gap-3">
                    <FormControl>
                      <input
                        type="checkbox"
                        aria-required="true"
                        className="accent-primary focus-visible:ring-ring mt-0.5 h-4 w-4 shrink-0 cursor-pointer rounded-sm focus-visible:outline-none focus-visible:ring-2"
                        checked={field.value}
                        onChange={(event) => field.onChange(event.target.checked)}
                        onBlur={field.onBlur}
                        name={field.name}
                        ref={field.ref}
                      />
                    </FormControl>
                    <FormLabel className="text-muted-foreground text-sm font-normal leading-relaxed">
                      {contact.consentBefore}
                      <a
                        href="/privacidade"
                        target="_blank"
                        rel="noopener"
                        className="text-foreground underline underline-offset-4"
                      >
                        {contact.consentLink}
                        <span className="sr-only"> {contact.consentLinkHint}</span>
                      </a>
                      {contact.consentAfter}
                    </FormLabel>
                  </div>
                  <FormMessage />
                </FormItem>
              )}
            />

            {/* Honeypot: ver o cabeçalho do arquivo. */}
            <div
              aria-hidden="true"
              className="absolute -left-[9999px] top-0 h-px w-px overflow-hidden"
            >
              <label htmlFor="lead-website">Site</label>
              <input
                id="lead-website"
                type="text"
                tabIndex={-1}
                autoComplete="off"
                {...form.register('website')}
              />
            </div>

            {submitError && (
              <p
                role="alert"
                className="border-destructive/40 bg-destructive-muted text-destructive rounded-md border px-3 py-2 text-sm"
              >
                {submitError}
              </p>
            )}

            <div>
              <Button
                type="submit"
                size="lg"
                disabled={pending}
                className="lp-glow-button w-full sm:w-auto"
              >
                {pending && <Loader2 className="animate-spin" aria-hidden="true" />}
                {pending ? contact.submitting : contact.submit}
              </Button>
            </div>
          </form>
        </Form>
      )}
    </div>
  );
}
