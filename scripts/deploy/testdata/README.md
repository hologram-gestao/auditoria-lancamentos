# Fixture do replay dos manifestos do Cloud Run

Manifestos **inventados** no formato do `gcloud run {services,jobs} describe --format=export`,
com o id (`liberdade-assessoria`), o número (`625547925581`), as SAs, o `cloudsql-instances`,
a KEK, as duas formas de URL antiga (`*-i7oep34hva-rj.a.run.app` e
`*-625547925581.southamerica-east1.run.app`) e um env `REDIS_URL`. Nenhum valor real: os
digests, uids, nonces e e-mails são fictícios, e os secrets aparecem só por NOME.

Lidos por `replay_cloud_run_manifests.sh --from-dir scripts/deploy/testdata`. O teste é
`bash scripts/deploy/test_replay_cloud_run_manifests.sh`.
