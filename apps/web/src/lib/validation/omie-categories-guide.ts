/**
 * Guia para configurar as categorias na tela da Omie (86e3qhht4).
 *
 * A tela de categorias da Omie pede, por categoria, a conta contábil e um entre
 * quatro comportamentos. A plataforma guarda só a conta (o de-para no destino
 * `conta_contabil`); o comportamento é um PADRÃO escolhido ao gerar o guia e
 * repetido em toda linha de "Configurar".
 *
 * Os rótulos são cópia literal de `BEHAVIOR_LABELS` em
 * `apps/api/app/modules/client_mapping/omie_guide.py` (o que vai na planilha): o
 * teste `test_omie_guide.py` do backend lê este arquivo e confere cada um.
 */
import { z } from 'zod';

import type { OmieCategoryBehavior } from '@/lib/contracts';

export const OMIE_CATEGORY_BEHAVIORS = [
  'provisao_e_baixa',
  'baixa_conta_resultado',
  'baixa_conta_terceiro',
  'nao_gerar',
] as const;

export const omieCategoryBehaviorSchema = z.enum(OMIE_CATEGORY_BEHAVIORS);

type BehaviorFormValue = z.infer<typeof omieCategoryBehaviorSchema>;

/**
 * Trava de contrato bidirecional: comportamento novo (ou renomeado) no backend
 * derruba a compilação aqui, e um que o formulário oferece sem o servidor aceitar
 * também.
 */
type AssertSameUnion<A, B> = [A] extends [B] ? ([B] extends [A] ? true : never) : never;
const behaviorUnionMatchesContract: AssertSameUnion<BehaviorFormValue, OmieCategoryBehavior> = true;
void behaviorUnionMatchesContract;

export const OMIE_CATEGORY_BEHAVIOR_LABELS: Record<OmieCategoryBehavior, string> = {
  provisao_e_baixa: 'Gerar provisão e baixa',
  baixa_conta_resultado: 'Gerar só a baixa, pela conta de resultado',
  baixa_conta_terceiro: 'Gerar só a baixa, pela conta do cliente ou fornecedor',
  nao_gerar: 'Não gerar lançamento',
};

export const DEFAULT_OMIE_CATEGORY_BEHAVIOR: OmieCategoryBehavior = 'provisao_e_baixa';

export const omieCategoriesGuideSchema = z.object({
  behavior: omieCategoryBehaviorSchema,
});

export type OmieCategoriesGuideFormValues = z.infer<typeof omieCategoriesGuideSchema>;
