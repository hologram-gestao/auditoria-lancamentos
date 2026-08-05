/**
 * Selo "considerou o glossário do cliente" (Sprint 6 / R4 — FRONT 06.7).
 *
 * O dado vem do contrato: `SessionDetailPayload.qualification_used_glossary`,
 * escrito por `qualify_session` a partir do bloco REALMENTE injetado no prompt
 * (não é um booleano afirmado por caller, nem recalculado no front).
 *
 * **Por que aqui e não por linha.** O sinal é da SESSÃO — a análise inteira
 * rodou com ou sem o bloco de glossário. Repeti-lo em cada linha das abas seria
 * a mesma informação N vezes numa tabela virtualizada: ruído visual e nós a
 * mais para o leitor de tela, sem dizer nada de novo. Um selo no cabeçalho da
 * conciliação cobre as quatro abas de uma vez.
 *
 * **`false` não ocupa espaço.** Cliente sem glossário (e sessão antiga, que tem
 * o default `false`) não renderiza nada — sem placeholder, sem espaço morto,
 * sem "não considerou". A tela fica idêntica ao comportamento anterior à
 * Sprint 6, que é o critério de "sem regressão".
 *
 * Acessibilidade: o significado está no TEXTO, não na cor nem no ícone (o
 * `BookOpen` é `aria-hidden`). Cor só por token semântico (`info`).
 */
import { BookOpen } from 'lucide-react';

/**
 * @param used `qualification_used_glossary` do contrato. `false`/`undefined`
 *   (sessão antiga) → não renderiza.
 */
export function GlossarySeal({ used }: { used: boolean | undefined }) {
  if (used !== true) return null;
  return (
    <span className="bg-info-muted text-info ring-info/30 inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset">
      <BookOpen className="h-3.5 w-3.5" aria-hidden="true" />
      Considerou o glossário do cliente
    </span>
  );
}
