/**
 * Vocabulário e conversões do MAPEAMENTO DE ENTRADA do arquivo (Sprint 14 / R1
 * · R5 — FRONT 14.5), num lugar só.
 *
 * Três coisas moram aqui, e nenhuma delas na tela:
 *   - os RÓTULOS PT-BR de cada enum do contrato (`Record` exaustivo: valor novo
 *     no backend derruba a compilação em vez de aparecer cru na tela);
 *   - os campos MAPEÁVEIS (data, descrição, valor, categoria, conta, documento)
 *     com rótulo e se são obrigatórios;
 *   - as conversões formulário ↔ contrato (`fromInputMapping` para editar o que
 *     está salvo, `toInputMappingRequest` para gravar). O formulário guarda
 *     `''` para "nenhuma coluna"; o contrato quer `null`/ausente — e o corpo é
 *     `extra="forbid"` no servidor, então a conversão só manda o que a
 *     convenção escolhida usa (os campos das outras convenções ficam de fora).
 *
 * Os nomes de coluna são ESTRUTURA do arquivo, nunca conteúdo de célula.
 */
import type {
  CategoryMode,
  CsvDelimiter,
  DecimalSeparator,
  InputDateFormat,
  InputEncoding,
  InputFileFormat,
  InputMapping,
  InputMappingRequest,
  SignConvention,
} from '@/lib/contracts';

// ---------------------------------------------------------------------------
// Rótulos dos enums
// ---------------------------------------------------------------------------

export const FILE_FORMAT_LABELS: Record<InputFileFormat, string> = {
  csv: 'CSV',
  xlsx: 'XLSX (Excel)',
};

export const CSV_DELIMITER_LABELS: Record<CsvDelimiter, string> = {
  ';': 'Ponto e vírgula ( ; )',
  ',': 'Vírgula ( , )',
  '|': 'Barra vertical ( | )',
};

export const ENCODING_LABELS: Record<InputEncoding, string> = {
  'utf-8': 'UTF-8',
  'utf-8-sig': 'UTF-8 com BOM (padrão do Excel)',
  'latin-1': 'Latin-1 (ISO-8859-1)',
  cp1252: 'Windows-1252',
};

export const DATE_FORMAT_LABELS: Record<InputDateFormat, string> = {
  'dd/mm/yyyy': 'dd/mm/aaaa (31/01/2026)',
  'dd-mm-yyyy': 'dd-mm-aaaa (31-01-2026)',
  'yyyy-mm-dd': 'aaaa-mm-dd (2026-01-31)',
  'dd/mm/yy': 'dd/mm/aa (31/01/26)',
};

export const DECIMAL_SEPARATOR_LABELS: Record<DecimalSeparator, string> = {
  ',': 'Vírgula (1.234,56)',
  '.': 'Ponto (1,234.56)',
};

export const SIGN_CONVENTION_LABELS: Record<SignConvention, string> = {
  valor_com_sinal: 'Valor com sinal',
  coluna_natureza: 'Coluna de natureza (D/C)',
  colunas_separadas: 'Colunas separadas de débito e crédito',
};

/** Uma frase por convenção — é o que ajuda a pessoa a escolher a certa. */
export const SIGN_CONVENTION_DESCRIPTIONS: Record<SignConvention, string> = {
  valor_com_sinal: 'A coluna de valor já vem negativa nas saídas e positiva nas entradas.',
  coluna_natureza:
    'Uma coluna diz se a linha é débito ou crédito (ex.: "D"/"C"); o valor vem sem sinal.',
  colunas_separadas: 'Débito e crédito vêm em duas colunas diferentes, ambas sem sinal.',
};

export const CATEGORY_MODE_LABELS: Record<CategoryMode, string> = {
  coluna_categoria: 'A coluna já é a categoria',
  classificacao_livre: 'Classificação livre: cada valor distinto vira uma categoria',
};

/** Os enums como listas, para os seletores — na ordem do contrato. */
export const FILE_FORMATS = Object.keys(FILE_FORMAT_LABELS) as InputFileFormat[];
export const CSV_DELIMITERS = Object.keys(CSV_DELIMITER_LABELS) as CsvDelimiter[];
export const ENCODINGS = Object.keys(ENCODING_LABELS) as InputEncoding[];
export const DATE_FORMATS = Object.keys(DATE_FORMAT_LABELS) as InputDateFormat[];
export const DECIMAL_SEPARATORS = Object.keys(DECIMAL_SEPARATOR_LABELS) as DecimalSeparator[];
export const SIGN_CONVENTIONS = Object.keys(SIGN_CONVENTION_LABELS) as SignConvention[];
export const CATEGORY_MODES = Object.keys(CATEGORY_MODE_LABELS) as CategoryMode[];

// ---------------------------------------------------------------------------
// Campos mapeáveis
// ---------------------------------------------------------------------------

/** As colunas do contrato que apontam para uma coluna do ARQUIVO. */
export type MappingColumnField =
  | 'dateColumn'
  | 'descriptionColumn'
  | 'amountColumn'
  | 'categoryColumn'
  | 'accountColumn'
  | 'documentColumn'
  | 'natureColumn'
  | 'debitColumn'
  | 'creditColumn';

export const MAPPING_FIELD_LABELS: Record<MappingColumnField, string> = {
  dateColumn: 'Data',
  descriptionColumn: 'Descrição',
  amountColumn: 'Valor',
  categoryColumn: 'Categoria',
  accountColumn: 'Conta',
  documentColumn: 'Documento',
  natureColumn: 'Natureza (débito/crédito)',
  debitColumn: 'Débito',
  creditColumn: 'Crédito',
};

/**
 * Os campos que o RESUMO lista (campo ← coluna), na ordem de leitura, com os
 * que a convenção de sinal escolhida efetivamente usa.
 */
export function mappedColumns(
  mapping: InputMapping,
): ReadonlyArray<{ field: MappingColumnField; column: string }> {
  const pairs: Array<{ field: MappingColumnField; column: string | null | undefined }> = [
    { field: 'dateColumn', column: mapping.dateColumn },
    { field: 'descriptionColumn', column: mapping.descriptionColumn },
  ];
  if (mapping.signConvention === 'colunas_separadas') {
    pairs.push(
      { field: 'debitColumn', column: mapping.debitColumn },
      { field: 'creditColumn', column: mapping.creditColumn },
    );
  } else {
    pairs.push({ field: 'amountColumn', column: mapping.amountColumn });
  }
  if (mapping.signConvention === 'coluna_natureza') {
    pairs.push({ field: 'natureColumn', column: mapping.natureColumn });
  }
  pairs.push(
    { field: 'categoryColumn', column: mapping.categoryColumn },
    { field: 'accountColumn', column: mapping.accountColumn },
    { field: 'documentColumn', column: mapping.documentColumn },
  );
  return pairs.filter(
    (pair): pair is { field: MappingColumnField; column: string } =>
      typeof pair.column === 'string' && pair.column !== '',
  );
}

// ---------------------------------------------------------------------------
// Formulário ↔ contrato
// ---------------------------------------------------------------------------

/**
 * O estado do formulário: tudo string, `''` = "não escolhido". Os enums ficam
 * como `'' | Enum` porque a convenção de sinal NUNCA tem padrão — o PRD proíbe
 * inferir sinal, e um padrão pré-selecionado seria inferência disfarçada.
 */
export interface InputMappingFormValues {
  fileFormat: InputFileFormat;
  csvDelimiter: CsvDelimiter | '';
  encoding: InputEncoding | '';
  dateColumn: string;
  descriptionColumn: string;
  amountColumn: string;
  categoryColumn: string;
  categoryMode: CategoryMode;
  accountColumn: string;
  documentColumn: string;
  dateFormat: InputDateFormat;
  decimalSeparator: DecimalSeparator;
  signConvention: SignConvention | '';
  natureColumn: string;
  debitValue: string;
  creditValue: string;
  debitColumn: string;
  creditColumn: string;
}

/**
 * Valores iniciais para um mapeamento NOVO. O formato vem da inspeção
 * (detectado pelo contêiner do arquivo); para CSV os padrões do servidor
 * (`;` e `utf-8-sig`) já vêm marcados — são os da leitura sem mapeamento.
 * Convenção de sinal vazia de propósito.
 */
export function defaultInputMappingFormValues(
  fileFormat: InputFileFormat = 'xlsx',
): InputMappingFormValues {
  return {
    fileFormat,
    csvDelimiter: fileFormat === 'csv' ? ';' : '',
    encoding: fileFormat === 'csv' ? 'utf-8-sig' : '',
    dateColumn: '',
    descriptionColumn: '',
    amountColumn: '',
    categoryColumn: '',
    categoryMode: 'coluna_categoria',
    accountColumn: '',
    documentColumn: '',
    dateFormat: 'dd/mm/yyyy',
    decimalSeparator: ',',
    signConvention: '',
    natureColumn: '',
    debitValue: '',
    creditValue: '',
    debitColumn: '',
    creditColumn: '',
  };
}

/** O mapeamento salvo, pronto para edição. */
export function fromInputMapping(mapping: InputMapping): InputMappingFormValues {
  return {
    fileFormat: mapping.fileFormat,
    csvDelimiter: mapping.csvDelimiter ?? '',
    encoding: mapping.encoding ?? '',
    dateColumn: mapping.dateColumn,
    descriptionColumn: mapping.descriptionColumn,
    amountColumn: mapping.amountColumn ?? '',
    categoryColumn: mapping.categoryColumn ?? '',
    categoryMode: mapping.categoryMode,
    accountColumn: mapping.accountColumn ?? '',
    documentColumn: mapping.documentColumn ?? '',
    dateFormat: mapping.dateFormat,
    decimalSeparator: mapping.decimalSeparator,
    signConvention: mapping.signConvention,
    natureColumn: mapping.natureColumn ?? '',
    debitValue: mapping.debitValue ?? '',
    creditValue: mapping.creditValue ?? '',
    debitColumn: mapping.debitColumn ?? '',
    creditColumn: mapping.creditColumn ?? '',
  };
}

function orNull(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
}

/**
 * Do formulário VALIDADO para o corpo do `PUT`. Só os campos da convenção
 * escolhida vão (os das outras ficam `null`): o servidor recusa
 * `valor_com_sinal` com `natureColumn` preenchida, e o formulário pode ter
 * guardado esse valor de quando a pessoa experimentou a outra convenção. CSV
 * leva delimitador e codificação; XLSX manda os dois `null`.
 *
 * Pré-condição: `signConvention !== ''` (o schema Zod garante). Sem ela, LANÇA:
 * sinal presumido é o que o PRD proíbe, e cair num padrão aqui faria o arquivo
 * ser lido com uma convenção que ninguém declarou.
 */
export function toInputMappingRequest(values: InputMappingFormValues): InputMappingRequest {
  const signConvention = values.signConvention;
  if (signConvention === '') {
    throw new Error('Convenção de sinal não declarada: o mapeamento não pode ser gravado sem ela.');
  }
  const isCsv = values.fileFormat === 'csv';
  const usesAmount = signConvention !== 'colunas_separadas';
  const usesNature = signConvention === 'coluna_natureza';
  const usesSeparate = signConvention === 'colunas_separadas';
  return {
    fileFormat: values.fileFormat,
    csvDelimiter: isCsv && values.csvDelimiter !== '' ? values.csvDelimiter : null,
    encoding: isCsv && values.encoding !== '' ? values.encoding : null,
    dateColumn: values.dateColumn.trim(),
    descriptionColumn: values.descriptionColumn.trim(),
    amountColumn: usesAmount ? orNull(values.amountColumn) : null,
    categoryColumn: orNull(values.categoryColumn),
    categoryMode: values.categoryMode,
    accountColumn: orNull(values.accountColumn),
    documentColumn: orNull(values.documentColumn),
    dateFormat: values.dateFormat,
    decimalSeparator: values.decimalSeparator,
    signConvention,
    natureColumn: usesNature ? orNull(values.natureColumn) : null,
    debitValue: usesNature ? orNull(values.debitValue) : null,
    creditValue: usesNature ? orNull(values.creditValue) : null,
    debitColumn: usesSeparate ? orNull(values.debitColumn) : null,
    creditColumn: usesSeparate ? orNull(values.creditColumn) : null,
  };
}
