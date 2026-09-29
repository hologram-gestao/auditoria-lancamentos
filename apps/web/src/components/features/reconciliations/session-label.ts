/**
 * Nome da conta de uma conciliação, para o título "Conta · Mês" do detalhe
 * (`session-detail-screen`, 86e2u513w). O breadcrumb do `ClientShell` também
 * usava daqui; saiu em 86e3fr9q3. A conta vem da lista sincronizada do
 * cliente (`ClientDetail.accounts`); sessão cuja conta saiu da lista (ex.:
 * removida no Omie) ainda ganha rótulo — o `#id` é o que o usuário consegue
 * conferir do outro lado.
 */
interface AccountRef {
  omie_conta_id: number;
  name: string;
}

export function accountNameFor(accounts: readonly AccountRef[], omieContaId: number): string {
  const account = accounts.find((a) => a.omie_conta_id === omieContaId);
  return account?.name ?? `Conta #${omieContaId}`;
}
