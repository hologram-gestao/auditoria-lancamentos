/**
 * A etapa "Diga o que é cada coluna" da importação do plano contábil (86e3ydjz4).
 *
 * Espelho da regra do SERVIDOR (`ChartColumnMappingRequest`, em
 * `client_accounting_chart/schemas.py`): uma coluna por papel e um papel por coluna;
 * código e nome obrigatórios; tipo obrigatório, salvo com "tipo pela hierarquia", que
 * exige a classificação e não convive com uma coluna de tipo. O servidor revalida tudo
 * (400 genérico); aqui é para a pessoa saber antes de enviar.
 */
import { z } from 'zod';

/** Os papéis que a pessoa pode dar a uma coluna; `ignorar` não vai no pedido. */
export const MAPPING_ROLES = [
  'codigo',
  'classificacao',
  'tipo',
  'grau',
  'nome',
  'ignorar',
] as const;
export type MappingRole = (typeof MAPPING_ROLES)[number];
export type MappedRole = Exclude<MappingRole, 'ignorar'>;

export const MAPPING_ROLE_LABELS: Record<MappingRole, string> = {
  codigo: 'Código',
  classificacao: 'Classificação',
  tipo: 'Tipo (S ou A)',
  grau: 'Grau',
  nome: 'Nome',
  ignorar: 'Ignorar',
};

/** As formas que o servidor detecta numa coluna (`recognizer.ColumnForm`), em português. */
export const COLUMN_FORM_LABELS: Record<string, string> = {
  inteiro: 'números inteiros',
  classificacao: 'classificação (1.1.2…)',
  tipo_sa: 'S/A',
  natureza: 'Devedora/Credora',
  documento: 'CNPJ/CPF',
  texto: 'texto',
  vazia: 'vazia',
  mista: 'formas misturadas',
};

export function columnFormLabel(form: string): string {
  return COLUMN_FORM_LABELS[form] ?? form;
}

export const accountingChartMappingSchema = z
  .object({
    /** O papel de cada coluna, na ordem das colunas do servidor (índice = posição). */
    roles: z.array(z.enum(MAPPING_ROLES)),
    tipoPelaHierarquia: z.boolean(),
  })
  .superRefine((values, ctx) => {
    const used = new Map<MappedRole, number>();
    values.roles.forEach((role, index) => {
      if (role === 'ignorar') return;
      if (used.has(role)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['roles', index],
          message: `Só uma coluna pode ser ${MAPPING_ROLE_LABELS[role].toLowerCase()}.`,
        });
        return;
      }
      used.set(role, index);
    });
    for (const role of ['codigo', 'nome'] as const) {
      if (!used.has(role)) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['roles'],
          message: `Escolha a coluna de ${MAPPING_ROLE_LABELS[role].toLowerCase()}.`,
        });
      }
    }
    if (values.tipoPelaHierarquia) {
      if (!used.has('classificacao') || used.has('tipo')) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          path: ['tipoPelaHierarquia'],
          message:
            'Para marcar o tipo pela hierarquia, escolha a classificação e nenhuma coluna de tipo.',
        });
      }
    } else if (!used.has('tipo')) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['roles'],
        message:
          'Escolha a coluna de tipo (S ou A), ou marque que o arquivo não tem coluna de tipo.',
      });
    }
  });

export type AccountingChartMappingValues = z.infer<typeof accountingChartMappingSchema>;

/** O corpo do campo `mapeamento` (o contrato do servidor). */
export interface AccountingChartMappingPayload {
  colunas: Partial<Record<MappedRole, number>>;
  tipoPelaHierarquia: boolean;
}

export function toMappingPayload(
  values: AccountingChartMappingValues,
): AccountingChartMappingPayload {
  const colunas: Partial<Record<MappedRole, number>> = {};
  values.roles.forEach((role, index) => {
    if (role !== 'ignorar') colunas[role] = index;
  });
  return { colunas, tipoPelaHierarquia: values.tipoPelaHierarquia };
}

/** O papel sugerido pelo servidor, quando é um papel de conta; o resto começa ignorado. */
export function suggestedRole(papelSugerido: string | null): MappingRole {
  return MAPPING_ROLES.find((role) => role === papelSugerido && role !== 'ignorar') ?? 'ignorar';
}
