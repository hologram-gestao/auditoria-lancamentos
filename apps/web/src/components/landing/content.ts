/**
 * TODO o texto da landing pública e do aviso de privacidade (86e3fr9uf).
 *
 * A versão comentada, com a fonte de cada dor e a evidência de cada afirmação, é
 * `Docs/landing/COPY.md`. A revisão do Lucas vira edição AQUI: nenhum componente
 * da landing escreve texto de conteúdo por conta própria. Regras do texto: o nome
 * do produto vem SEMPRE de `PRODUCT_NAME` (a D2 caiu em 30/09/2026: a landing nomeia
 * o produto), quem trata o dado é a empresa (`COMPANY_*`), sem número de resultado,
 * sem travessão, sem "garantimos". Um teste (`__tests__/content.test.ts`) trava o
 * nome antigo, a sigla e o travessão.
 */
import { COMPANY_NAME, COMPANY_SHORT_NAME, PRODUCT_NAME } from '@/lib/brand';

/** Versão do texto de consentimento. Igual a `CONSENT_TEXT_VERSION` do backend. */
export const CONSENT_TEXT_VERSION = '2026-09-30';

export const CONTACT_ANCHOR = 'contato';

/**
 * Manual da plataforma, servido de `public/` (cópia de `Docs/manual/Manual-Hologram-OS.pdf`
 * da `main`). O `robots.txt` libera este caminho. Trocou o PDF, confira o `size`: um
 * teste compara com o arquivo.
 */
export const manual = {
  href: '/manual-hologram-os.pdf',
  label: 'Baixar o manual (PDF)',
  size: 'PDF, 3 MB',
  successLead: 'Enquanto respondemos, leia o manual',
} as const;

export const landingMeta = {
  title: `${PRODUCT_NAME}: o financeiro do seu cliente, conferido antes da contabilidade`,
  description: `O ${PRODUCT_NAME} cruza extrato, fatura e planilha com o que foi lançado, mostra o que não bate e deixa pronto o que segue para o sistema contábil.`,
} as const;

export const header = {
  brand: PRODUCT_NAME,
  navLabel: 'Acesso e contato',
  signIn: 'Entrar',
  contact: 'Entrar em contato',
} as const;

export const hero = {
  eyebrow: `${PRODUCT_NAME}, para escritórios de contabilidade, BPOs financeiros e empresas`,
  /** O título em três partes: o meio é a ÚNICA palavra em destaque (gradiente) do hero. */
  titleBefore: 'O financeiro do seu cliente, ',
  titleHighlight: 'conferido',
  titleAfter: ' antes de virar contabilidade.',
  subtitle: `O ${PRODUCT_NAME} cruza extrato, fatura e planilha com o que foi lançado, mostra o que não bate e deixa pronto o que segue para o sistema contábil. Sua equipe revisa as exceções, não o mês inteiro.`,
  primary: 'Entrar em contato',
  secondary: 'Entrar',
  /** Chips abaixo dos botões: o que entra e o que sai (`parse_service.py`, export, Sprint 13). */
  chipsLabel: 'Formatos',
  chips: ['Lê PDF, XLSX, XLS, CSV e Omie', 'Entrega Excel, Omie e o arquivo contábil'],
} as const;

/** Vinheta de produto: dado FICTÍCIO, valores redondos, nada que pareça cliente real. */
export const vignette = {
  label: 'Exemplo ilustrativo de uma conciliação sendo conferida',
  title: 'Conciliação',
  subtitle: 'Cliente exemplo · Conta corrente · Março',
  status: 'Em conferência',
  pending: 'Pendente',
  done: 'Conciliado',
  rows: [
    { date: '03/03', description: 'Tarifa bancária', amount: '-R$ 50,00' },
    { date: '05/03', description: 'Recebimento de cliente', amount: 'R$ 4.200,00' },
    { date: '10/03', description: 'Pagamento a fornecedor', amount: '-R$ 1.250,00' },
    { date: '15/03', description: 'Folha de pagamento', amount: '-R$ 8.000,00' },
    { date: '20/03', description: 'Aplicação financeira', amount: '-R$ 2.000,00' },
  ],
  footerLabel: 'Diferença de saldo',
  footerValue: 'R$ 0,00',
  /** Mini-card flutuante: uma anomalia apontada, para dar profundidade à vinheta. */
  flagBadge: 'Classificação suspeita',
  flagText: 'IOF lançado como juros',
} as const;

export const audience = {
  eyebrow: 'Para quem',
  title: 'Feito para quem fecha o financeiro de outras empresas',
  items: [
    {
      icon: 'calculator',
      title: 'Escritório de contabilidade',
      text: 'Saiba se o cliente está pronto para a integração contábil antes de importar. O que não bate aparece na conferência, não no balancete.',
    },
    {
      icon: 'briefcase',
      title: 'BPO financeiro',
      text: 'Concilie cada conta, mês a mês, com revisão por etapas, relatório em Excel e as compras do cartão lançadas no Omie.',
    },
    {
      icon: 'building',
      title: 'Empresa',
      text: 'Com ERP ou só com planilha, seu financeiro chega conferido ao contador, com os títulos vencidos e as exceções explicados.',
    },
  ],
} as const;

export const pains = {
  eyebrow: 'Dores e respostas',
  title: 'O trabalho que ninguém vê, e que decide o fechamento',
  painLabel: 'O problema',
  answerLabel: `Como o ${PRODUCT_NAME} responde`,
  items: [
    {
      pain: 'O fechamento não roda todo mês. Fica para depois, e o erro de janeiro só aparece quando o balanço aperta.',
      answer:
        'Cada conta de cada mês vira uma conciliação própria, com o que já foi conferido e o que falta. Dá para fechar mês a mês sem montar planilha.',
    },
    {
      pain: 'Dado ruim entra na contabilidade: imobilizado lançado como despesa, ajuste de saldo inventado, tarifas somadas errado. Corrigir depois custa mais que lançar certo.',
      answer:
        'Cada movimentação é comparada com o que foi lançado, por valor e data, com regra fixa. O que não bate vira anomalia com tipo e motivo. A IA aponta o que parece incoerente; quem decide é a sua equipe.',
    },
    {
      pain: 'Categorizar, fazer o de-para e montar o lançamento contábil vira PROCV, cliente a cliente.',
      answer:
        'Cada cliente tem o próprio de-para da categoria para a conta contábil, com o plano de contas dele e o histórico padrão. Débito e crédito saem do sinal da movimentação, e o arquivo sai no layout que o sistema contábil importa, hoje em validação com escritórios parceiros.',
    },
    {
      pain: 'A maioria dos clientes não tem ERP. Chega planilha e extrato em todo formato, pelo WhatsApp.',
      answer:
        'Cliente com Omie é conectado direto. Cliente sem sistema manda a planilha do mês, lida por um mapeamento configurado uma vez. Extrato e fatura em PDF ou planilha são lidos por IA, e você confere uma amostra antes de seguir.',
    },
  ],
} as const;

export const how = {
  eyebrow: 'Como funciona',
  title: 'Do arquivo ao lançamento, em quatro passos',
  steps: [
    {
      title: 'Envie o arquivo ou conecte a origem',
      text: `Extrato, fatura de cartão ou planilha do cliente. Se ele usa Omie, o ${PRODUCT_NAME} busca os lançamentos por conta própria.`,
    },
    {
      title: `O ${PRODUCT_NAME} lê e cruza`,
      text: 'A IA extrai as movimentações do arquivo. O cruzamento com os lançamentos segue regra fixa: até um centavo de diferença no valor e até três dias na data.',
    },
    {
      title: 'Sua equipe revisa o que ficou de fora',
      text: 'Divergências, lançamentos sem par e anomalias aparecem separados, com espaço para a nota de resolução de cada um.',
    },
    {
      title: 'Sai o relatório e o lançamento',
      text: 'Relatório da conciliação em Excel, compras do cartão lançadas no Omie e o arquivo contábil no layout do seu sistema.',
    },
  ],
} as const;

export const security = {
  eyebrow: 'Segurança e privacidade',
  title: 'O dado do seu cliente continua dele',
  items: [
    {
      icon: 'key',
      title: 'Uma chave por cliente',
      text: 'Os dados sensíveis de cada cliente são cifrados com uma chave própria. O conteúdo de um cliente não abre com a chave de outro.',
    },
    {
      icon: 'file',
      title: 'O arquivo original não fica guardado',
      text: 'O arquivo é lido por IA para extrair as movimentações e não fica armazenado pela plataforma. Na revisão, a IA também apoia sua equipe apontando o que parece incoerente; quem decide é sempre uma pessoa.',
    },
    {
      icon: 'users',
      title: 'Cada um vê só o que é seu',
      text: 'O escritório alcança só os próprios clientes, e o cliente final só a própria empresa. Exportações e tentativas de acesso negadas ficam registradas.',
    },
    {
      icon: 'lock',
      title: 'Encerrou, acabou',
      text: 'Quando um cliente sai, a chave dele é destruída e o conteúdo cifrado deixa de poder ser lido.',
    },
  ],
  privacyLink: 'Leia o aviso de privacidade',
} as const;

export const contact = {
  eyebrow: 'Contato',
  title: 'Fale com a gente',
  lead: 'Conte um pouco do seu cenário. Respondemos pelo e-mail informado.',
  fields: {
    name: 'Nome',
    email: 'E-mail',
    company: 'Empresa ou escritório',
    whatsapp: 'WhatsApp',
    message: 'Mensagem',
    optional: '(opcional)',
  },
  consentBefore:
    'Autorizo a Hologram a usar estes dados para entrar em contato comigo sobre a plataforma, conforme o ',
  consentLink: 'aviso de privacidade',
  consentAfter: '.',
  consentLinkHint: '(abre em nova aba)',
  submit: 'Enviar',
  submitting: 'Enviando…',
  successTitle: 'Recebemos sua mensagem.',
  successText: 'Vamos responder pelo e-mail informado.',
  errors: {
    name: 'Informe seu nome.',
    email: 'Informe um e-mail válido.',
    tooLong: (max: number) => `Use até ${max} caracteres.`,
    whatsapp: 'Use só números, espaço, +, parênteses e hífen.',
    consent: 'Marque a autorização para enviarmos.',
    rateLimited: 'Muitas mensagens em pouco tempo. Tente de novo em um minuto.',
    generic: 'Não foi possível enviar agora. Tente de novo em instantes.',
  },
} as const;

export const footer = {
  company: COMPANY_NAME,
  product: PRODUCT_NAME,
  signIn: 'Entrar',
  privacy: 'Aviso de privacidade',
  navLabel: 'Links do rodapé',
} as const;

export const privacy = {
  metaTitle: `Aviso de privacidade · ${COMPANY_SHORT_NAME}`,
  metaDescription: 'Como a Hologram usa os dados enviados pelo formulário de contato da landing.',
  title: 'Aviso de privacidade',
  intro: `Este aviso vale para o formulário de contato do ${PRODUCT_NAME}. Ele não trata dos dados que os clientes da plataforma processam nela.`,
  updated: 'Versão de 30 de setembro de 2026.',
  sections: [
    {
      title: 'Quais dados coletamos',
      text: 'Nome e e-mail, obrigatórios. Empresa ou escritório, WhatsApp e mensagem, se você preencher. Guardamos também a data do seu consentimento e a versão do texto que você aceitou.',
    },
    {
      title: 'O que não coletamos',
      text: 'O contato é gravado sem o seu endereço IP e sem dados do seu navegador, e o formulário não usa cookies de rastreamento. Os registros técnicos de acesso do servidor, comuns a qualquer site, ficam por tempo limitado e não são ligados ao seu contato.',
    },
    {
      title: 'Para quê',
      text: 'Só para responder ao seu contato e conversar sobre a plataforma. Não vendemos, não emprestamos e não usamos esses dados para outra finalidade.',
    },
    {
      title: 'Base legal',
      text: 'O seu consentimento, dado ao marcar a autorização antes de enviar (Lei Geral de Proteção de Dados, art. 7º, I).',
    },
    {
      title: 'Quem lê',
      text: `A equipe da ${COMPANY_NAME}. O aviso de cada contato chega a um canal interno da equipe.`,
    },
    {
      title: 'Por quanto tempo',
      text: 'Até você pedir a exclusão, ou por até 12 meses sem nova conversa, o que vier primeiro.',
    },
    {
      title: 'Seus direitos',
      text: 'Você pode pedir acesso, correção ou exclusão dos seus dados, e retirar o consentimento, a qualquer momento. Envie o pedido pelo mesmo formulário, informando o e-mail usado no contato.',
    },
  ],
  back: 'Voltar para a página inicial',
} as const;
