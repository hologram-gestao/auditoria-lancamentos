/**
 * Classificação de uma linha da gaveta "Lançar no Omie" e o pedido que sai dela
 * (86e3n70q0, bloco C; a divisão em categorias fecha a subtask 86e3nb818).
 *
 * Duas formas, por linha:
 *   - `provisionar` (padrão, igual ao servidor): título no Contas a Pagar, com
 *     categoria única OU dividida, fornecedor e, opcional ou obrigatório,
 *     rateio por departamento;
 *   - `direto`: o `IncluirLancCC` da Sprint 7, só com a categoria.
 *
 * **Espelho das regras do servidor** (`omie_posting/schemas.py` e
 * `service.py::_category_split`/`_department_split`), não uma segunda regra: a tela
 * confere para a pessoa ver o problema na linha antes de enviar, e o servidor
 * recusa de qualquer forma. As tolerâncias são as dele: 0,01 na soma das
 * categorias contra o valor da compra, 0,01 ponto percentual no rateio.
 *
 * Dinheiro em CENTAVOS INTEIROS (string no campo, `number` só para somar) e
 * percentual em CENTÉSIMOS de ponto — nunca `parseFloat` de "R$" (§3.4). O que vai
 * ao servidor é o decimal em string (`"100.00"`, `"33.33"`).
 *
 * Nada aqui guarda CNPJ, razão social ou e-mail além do estado da gaveta: o pedido
 * os leva ao servidor, que os passa à Omie sem persistir (§4.5).
 */
import { z } from 'zod';

import { cnpjDigits } from '@/lib/cnpj';
import type {
  CategoriaRateio,
  DepartamentoRateio,
  OmieFornecedorItem,
  OmiePostingLineRequest,
  OmiePostingMode,
} from '@/lib/contracts';
import { centsToDecimalString } from '@/lib/money-input';

/** Tetos do servidor (`MAX_CATEGORIAS_POR_LINHA`, `MAX_DEPARTAMENTOS_POR_LINHA`). */
export const MAX_CATEGORY_PARTS = 10;
export const MIN_CATEGORY_PARTS = 2;
export const MAX_DEPARTMENT_PARTS = 30;
/** Tolerância das somas (`SOMA_TOLERANCIA`): 1 centavo / 0,01 ponto percentual. */
const TOLERANCE = 1;
/** 100% em centésimos de ponto percentual. */
const FULL_PERCENT = 10000;

/** Uma parte da compra numa categoria. `key` é a chave React estável da parte. */
export interface CategoryPart {
  key: string;
  codigo: string | null;
  /** Centavos crus (`lib/money-input.ts`), sem sinal. */
  cents: string;
}

/** Uma parte do rateio. `hundredths` é o percentual × 100 (`"3333"` = 33,33%). */
export interface DepartmentPart {
  key: string;
  codigo: string | null;
  hundredths: string;
}

/**
 * O fornecedor que a pessoa escolheu para a linha (só na provisão).
 *
 * - `none`: nada escolhido (sem padrão no cliente, a linha fica de fora);
 * - `default`: o fornecedor padrão do cliente (o servidor o usa sozinho);
 * - `found`: o CNPJ tem cadastro no Omie; vai o CNPJ e o servidor acha o mesmo;
 * - `new`: o CNPJ não tem cadastro; vão razão social e e-mail para cadastrar.
 */
export type SupplierValue =
  | { kind: 'none' }
  | { kind: 'default' }
  | { kind: 'found'; cnpj: string; supplier: OmieFornecedorItem }
  | { kind: 'new'; cnpj: string; razaoSocial: string; email: string };

export interface LineDraft {
  modo: OmiePostingMode;
  /** Dividir em categorias (só na provisão). */
  split: boolean;
  /** Categoria única. */
  categoria: string | null;
  parts: CategoryPart[];
  supplier: SupplierValue;
  /** Rateio por departamento; vazio = sem rateio. */
  departments: DepartmentPart[];
}

/** O que a linha precisa saber de fora dela para ser validada. */
export interface LineContext {
  /** Valor da compra como o servidor manda (`"-103.80"`). */
  amount: string;
  /** `clients.omie_department_required`. */
  departmentRequired: boolean;
  /** O cliente tem `omie_default_supplier_code`. */
  hasDefaultSupplier: boolean;
  /**
   * A intenção já passou do passo `fornecedor` (retomada): o fornecedor está no
   * Omie e o servidor não o pede de novo.
   */
  supplierSettled: boolean;
}

/** Motivos de a linha ficar FORA do lote, na ordem em que o servidor os confere. */
export type LineIssue =
  | 'sem_categoria'
  | 'categorias_incompletas'
  | 'categoria_repetida'
  | 'categorias_nao_fecham'
  | 'departamentos_incompletos'
  | 'departamento_repetido'
  | 'departamentos_nao_fecham'
  | 'departamento_pendente'
  | 'fornecedor_pendente'
  | 'fornecedor_incompleto';

let keySeq = 0;
/** Chave React de uma parte nova (nunca o índice: a lista é editável). */
export function newPartKey(): string {
  keySeq += 1;
  return `p${keySeq}`;
}

export function emptyDraft(modo: OmiePostingMode): LineDraft {
  return {
    modo,
    split: false,
    categoria: null,
    parts: [],
    supplier: { kind: 'none' },
    departments: [],
  };
}

/** `"-103.80"` → `10380` (valor absoluto, em centavos). Só aritmética de string. */
export function amountToCents(amount: string): number {
  const match = /^-?(\d+)(?:[.,](\d{1,2}))?$/.exec(amount.trim());
  if (match === null) return 0;
  const whole = match[1] ?? '0';
  const fraction = (match[2] ?? '').padEnd(2, '0');
  return Number(whole) * 100 + Number(fraction);
}

/** Centavos crus do campo → inteiro (`""` → 0). */
export function partCents(cents: string): number {
  const digits = cents.replace(/\D/g, '');
  return digits === '' ? 0 : Number(digits);
}

/** Duas partes iniciais da divisão: a categoria atual com o valor inteiro, e uma vazia. */
export function initialCategoryParts(categoria: string | null, amount: string): CategoryPart[] {
  return [
    { key: newPartKey(), codigo: categoria, cents: String(amountToCents(amount)) },
    { key: newPartKey(), codigo: null, cents: '' },
  ];
}

/** Uma parte inicial do rateio: 100%. */
export function initialDepartmentParts(): DepartmentPart[] {
  return [{ key: newPartKey(), codigo: null, hundredths: String(FULL_PERCENT) }];
}

/** Quanto falta (positivo) ou sobra (negativo) para as partes fecharem a compra. */
export function categoryRemainderCents(parts: CategoryPart[], amount: string): number {
  return amountToCents(amount) - parts.reduce((acc, p) => acc + partCents(p.cents), 0);
}

/** Quanto falta (positivo) ou sobra (negativo) para o rateio somar 100%. */
export function departmentRemainderHundredths(parts: DepartmentPart[]): number {
  return FULL_PERCENT - parts.reduce((acc, p) => acc + partCents(p.hundredths), 0);
}

/**
 * Valor de uma parte do rateio, SÓ para exibição: o servidor calcula o valor de
 * verdade (`_department_split`, com o centavo que sobra na última parte).
 */
export function departmentPartCents(hundredths: string, amount: string): number {
  return Math.round((amountToCents(amount) * partCents(hundredths)) / FULL_PERCENT);
}

function issue(ctx: z.RefinementCtx, code: LineIssue): void {
  ctx.addIssue({ code: z.ZodIssueCode.custom, message: code, params: { issue: code } });
}

/**
 * A regra da linha em zod. Cada recusa é um `LineIssue`; a primeira decide o
 * aviso da linha. Linha `direto` só precisa da categoria única (o servidor recusa
 * o lote INTEIRO com 400 se ela levar campo da provisão — por isso o builder nem
 * os monta).
 */
export function lineDraftSchema(context: LineContext) {
  const total = amountToCents(context.amount);
  return z
    .object({
      modo: z.enum(['provisionar', 'direto']),
      split: z.boolean(),
      categoria: z.string().nullable(),
      parts: z.array(
        z.object({ key: z.string(), codigo: z.string().nullable(), cents: z.string() }),
      ),
      supplier: z.custom<SupplierValue>(),
      departments: z.array(
        z.object({ key: z.string(), codigo: z.string().nullable(), hundredths: z.string() }),
      ),
    })
    .superRefine((draft, ctx) => {
      const usesSplit = draft.modo === 'provisionar' && draft.split;
      if (!usesSplit) {
        if (draft.categoria === null || draft.categoria.trim() === '') issue(ctx, 'sem_categoria');
      } else {
        const parts = draft.parts;
        if (
          parts.length < MIN_CATEGORY_PARTS ||
          parts.length > MAX_CATEGORY_PARTS ||
          parts.some((p) => p.codigo === null || p.codigo === '' || partCents(p.cents) <= 0)
        ) {
          issue(ctx, 'categorias_incompletas');
        } else if (new Set(parts.map((p) => p.codigo)).size !== parts.length) {
          issue(ctx, 'categoria_repetida');
        } else if (
          Math.abs(total - parts.reduce((a, p) => a + partCents(p.cents), 0)) > TOLERANCE
        ) {
          issue(ctx, 'categorias_nao_fecham');
        }
      }
      if (draft.modo === 'direto') return;

      const deps = draft.departments;
      if (deps.length > 0) {
        if (
          deps.length > MAX_DEPARTMENT_PARTS ||
          deps.some(
            (d) =>
              d.codigo === null ||
              d.codigo === '' ||
              partCents(d.hundredths) <= 0 ||
              partCents(d.hundredths) > FULL_PERCENT,
          )
        ) {
          issue(ctx, 'departamentos_incompletos');
        } else if (new Set(deps.map((d) => d.codigo)).size !== deps.length) {
          issue(ctx, 'departamento_repetido');
        } else if (Math.abs(departmentRemainderHundredths(deps)) > TOLERANCE) {
          issue(ctx, 'departamentos_nao_fecham');
        }
      } else if (context.departmentRequired) {
        issue(ctx, 'departamento_pendente');
      }

      if (context.supplierSettled) return;
      const supplier = draft.supplier;
      if (supplier.kind === 'none') {
        issue(ctx, 'fornecedor_pendente');
      } else if (supplier.kind === 'default' && !context.hasDefaultSupplier) {
        issue(ctx, 'fornecedor_pendente');
      } else if (
        supplier.kind === 'new' &&
        (supplier.razaoSocial.trim() === '' || !/^[^@\s]+@[^@\s]+$/.test(supplier.email.trim()))
      ) {
        issue(ctx, 'fornecedor_incompleto');
      }
    });
}

/** `null` = a linha pode ir no lote; senão, o motivo de ficar de fora. */
export function lineIssue(draft: LineDraft, context: LineContext): LineIssue | null {
  const result = lineDraftSchema(context).safeParse(draft);
  if (result.success) return null;
  const first = result.error.issues[0];
  const code = (first as { params?: { issue?: LineIssue } } | undefined)?.params?.issue;
  return code ?? 'sem_categoria';
}

/** O aviso da linha que fica de fora. Diz por que E que ela não será lançada. */
export const LINE_ISSUE_MESSAGE: Record<LineIssue, string> = {
  sem_categoria: 'Sem categoria: esta compra não será lançada.',
  categorias_incompletas:
    'Escolha a categoria e o valor de cada parte (de 2 a 10 partes): esta compra não será lançada assim.',
  categoria_repetida: 'A mesma categoria aparece em duas partes: junte-as numa só.',
  categorias_nao_fecham:
    'A soma das partes não fecha com o valor da compra: esta compra não será lançada assim.',
  departamentos_incompletos:
    'Escolha o departamento e o percentual de cada parte do rateio: esta compra não será lançada assim.',
  departamento_repetido: 'O mesmo departamento aparece em duas partes do rateio.',
  departamentos_nao_fecham:
    'Os percentuais do rateio precisam somar 100%: esta compra não será lançada assim.',
  departamento_pendente:
    'Este cliente exige rateio por departamento: escolha os departamentos. Sem eles esta compra fica de fora (departamento pendente).',
  fornecedor_pendente:
    'Sem fornecedor: busque o CNPJ ou use o fornecedor padrão. Sem ele esta compra fica de fora (fornecedor pendente).',
  fornecedor_incompleto:
    'CNPJ sem cadastro no Omie: informe razão social e e-mail para cadastrar o fornecedor.',
};

/** Resumo curto para o rodapé ("N compras ficam de fora (motivo)"). */
export const LINE_ISSUE_SHORT: Record<LineIssue, string> = {
  sem_categoria: 'sem categoria',
  categorias_incompletas: 'divisão incompleta',
  categoria_repetida: 'categoria repetida',
  categorias_nao_fecham: 'divisão não fecha',
  departamentos_incompletos: 'rateio incompleto',
  departamento_repetido: 'departamento repetido',
  departamentos_nao_fecham: 'rateio não fecha',
  departamento_pendente: 'sem departamento',
  fornecedor_pendente: 'sem fornecedor',
  fornecedor_incompleto: 'fornecedor incompleto',
};

function decimal(cents: string): string {
  return centsToDecimalString(String(partCents(cents))) ?? '0.00';
}

/**
 * O pedido de UMA linha, a partir de um rascunho VÁLIDO (`lineIssue === null`).
 *
 * Deriva tudo do `modo`: uma linha `direto` sai SÓ com `file_entry_id`,
 * `cod_categoria` e `modo`, mesmo que a pessoa tenha preenchido fornecedor ou
 * rateio antes de trocar de forma — o servidor responderia 400 ao lote inteiro.
 */
export function buildPostingLine(
  fileEntryId: string,
  draft: LineDraft,
  context: Pick<LineContext, 'supplierSettled'>,
): OmiePostingLineRequest {
  if (draft.modo === 'direto') {
    return { file_entry_id: fileEntryId, cod_categoria: draft.categoria ?? '', modo: 'direto' };
  }
  const line: OmiePostingLineRequest = { file_entry_id: fileEntryId, modo: 'provisionar' };
  if (draft.split) {
    line.categorias = draft.parts.map(
      (p): CategoriaRateio => ({ codigo: p.codigo ?? '', valor: decimal(p.cents) }),
    );
  } else {
    line.cod_categoria = draft.categoria ?? '';
  }
  if (!context.supplierSettled) {
    const supplier = draft.supplier;
    if (supplier.kind === 'found') {
      line.cnpj_fornecedor = cnpjDigits(supplier.cnpj);
    } else if (supplier.kind === 'new') {
      line.cnpj_fornecedor = cnpjDigits(supplier.cnpj);
      line.razao_social = supplier.razaoSocial.trim();
      line.email = supplier.email.trim();
    }
    // `default`: nada — o servidor usa o fornecedor padrão do cliente.
  }
  if (draft.departments.length > 0) {
    line.departamentos = draft.departments.map(
      (d): DepartamentoRateio => ({ codigo: d.codigo ?? '', percentual: decimal(d.hundredths) }),
    );
  }
  return line;
}
