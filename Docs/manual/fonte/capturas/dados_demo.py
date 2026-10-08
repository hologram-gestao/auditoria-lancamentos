"""Dados de demonstração para as figuras do manual (task 86e3gqfmj), pela API local.

API em 127.0.0.1:8031, banco isolado e NOVO (migrations + seed_dev; na 1.2, `adl_manual_v12`):
o dado cifrado só se lê com as chaves que o gravaram. Nomes fictícios desde a criação.
Preparação contábil copiada do cenário da validação da Sprint 13.
"""

from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path
from typing import Any

import httpx

BASE = "http://127.0.0.1:8031/api/v1"
RAIZ = Path(__file__).resolve().parents[4]
SAMPLE = RAIZ / "apps/api/tests/fixtures/accounting_sample/cliente_exemplo_2026_08"
EXTRATO = (SAMPLE / "extrato_cliente.csv").read_bytes()
PLANO = (SAMPLE / "plano_contabil.csv").read_bytes()
DECISOES = list(
    csv.DictReader(io.StringIO((SAMPLE / "decisoes_depara.csv").read_text()), delimiter=";")
)
SEED_PASS = "dev-only-change-me"  # noqa: S105 (default público de dev do seed_dev.py)
USER_PASS = "Manual-demo-2026"  # noqa: S105 (usuário descartável do banco local)
HOLOGRAM = "0706eeb5-9718-4d03-bcda-ef615789e6ac"
COMP = "2026-08"
MAPPING = {
    "fileFormat": "csv", "csvDelimiter": ";", "encoding": "utf-8",
    "dateColumn": "Data", "descriptionColumn": "Descricao", "amountColumn": "Valor",
    "categoryColumn": "Descricao", "categoryMode": "classificacao_livre",
    "dateFormat": "dd/mm/yyyy", "decimalSeparator": ",", "signConvention": "valor_com_sinal",
}
ids: dict[str, str] = {}


def ok(r: httpx.Response, what: str, *codes: int) -> dict[str, Any]:
    good = r.status_code in (codes or (200, 201))
    print(("  OK     " if good else "  FALHOU ") + f"{what} -> {r.status_code}", flush=True)
    if not good:
        print("         " + r.text[:400])
        raise SystemExit(1)
    time.sleep(0.4)  # 86e3fxqqa: a resposta podia sair antes do commit
    try:
        j = r.json()
    except ValueError:
        return {}
    return j.get("data", j) if isinstance(j, dict) else {"_list": j}


def login(email: str, password: str) -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=120)
    r = c.post("/auth/login", json={"email": email, "password": password})
    ok(r, f"login {email}")
    c.headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in c.cookies.items())
    c.cookies.clear()
    return c


plat = login("platform@hologram.com.br", SEED_PASS)

print("\n[1] Padaria Aurora Ltda: Omie simulado, contas, carteira e contexto")
r = plat.post("/clients", json={
    "name": "Padaria Aurora Ltda", "organization_id": HOLOGRAM,
    "omie_app_key": "FAKE_DEMO_OMIE_aurora_key", "omie_app_secret": "FAKE_DEMO_OMIE_aurora_secret",
})
padaria = ok(r, "cria a Padaria com a origem Omie")["id"]
ids["padaria"] = padaria
ok(plat.patch(f"/clients/{padaria}/sync-accounts"), "sincroniza contas")
ok(plat.post(f"/clients/{padaria}/titles/sync"), "sincroniza carteira")
titles = plat.get(f"/clients/{padaria}/titles", params={"pageSize": 100}).json()["data"]
by_amount = {str(t["amount"]): t for t in titles}
print("  títulos:", sorted(by_amount))
ctx = {
    "3200.00": ("acordo_de_pagamento", "Acordo de pagamento em três parcelas a partir de outubro."),
    "890.00": ("nota_a_cancelar", "Nota emitida em duplicidade; fornecedor vai cancelar."),
}
for amount, (kind, text) in ctx.items():
    t = by_amount.get(amount) or by_amount.get(amount.rstrip("0").rstrip("."))
    if t is None:
        raise SystemExit(f"título de {amount} não encontrado")
    ok(plat.post(f"/clients/{padaria}/titles/{t['id']}/context", json={"type": kind, "text": text}),
       f"contexto {kind} no título de {amount}")

print("\n[2] Comercial Horizonte Ltda: origem por arquivo, plano contábil, de-para, materialização")
r = plat.post("/clients", json={"name": "Comercial Horizonte Ltda", "organization_id": HOLOGRAM})
horizonte = ok(r, "cria o Comercial Horizonte")["id"]
ids["horizonte"] = horizonte
ok(plat.post(f"/clients/{horizonte}/connections", json={"provider_type": "arquivo"}), "conexão arquivo")
ok(plat.put(f"/clients/{horizonte}/input-mapping", json=MAPPING), "mapeamento de entrada")
r = plat.post(f"/clients/{horizonte}/file-origin/process",
              files={"file": ("extrato_agosto.csv", EXTRATO, "text/csv")},
              data={"competence": COMP, "declaredTotal": "5183,67"})
ok(r, "envia o extrato de 2026-08")
ok(plat.post(f"/clients/{horizonte}/accounting-chart/import",
             files={"file": ("plano_contabil.csv", PLANO, "text/csv")}), "importa o plano contábil")
acc = {a["code"]: a for a in plat.get(f"/clients/{horizonte}/accounting-chart",
                                      params={"pageSize": 100}).json()["data"]}
ok(plat.put(f"/clients/{horizonte}/source-accounts", json={
    "sourceType": "arquivo", "sourceAccountId": None, "accountingAccountId": acc["649"]["id"]}),
   "conta do banco 649")
mp = f"/clients/{horizonte}/mapping/conta_contabil"
cats = plat.get(mp, params={"situation": "sem_decisao", "pageSize": 100}).json()["data"]
codes = {i["categoryName"]: i["categoryCode"] for i in cats}
decisions = [{"categoryCode": codes[x["categoria_origem"]], "sourceType": "arquivo",
              "decision": "alvo", "accountingAccountId": acc[x["conta_contabil"]]["id"],
              "history": x["historico_padrao"]} for x in DECISOES]
ok(plat.post(f"{mp}/decisions/batch", json={
    "effectiveFrom": COMP, "confirmRetroactive": True, "decisions": decisions}),
   f"{len(decisions)} decisões")
pv = plat.get(f"{mp}/preview", params={"competence": COMP}).json()["data"]
ok(plat.post(f"{mp}/materializations", json={"competence": COMP, "previewToken": pv["previewToken"]}),
   "materializa 2026-08")

print("\n[3] layouts e gerações")
lay = ok(plat.post("/export-layouts/from-template", json={
    "templateKey": "dominio_lancamentos_csv", "name": "Layout Domínio padrão",
    "organizationId": HOLOGRAM}), "layout padrão")
ids["layout"] = lay["id"]
definition = plat.get(f"/export-layouts/{lay['id']}").json()["data"]["versions"][0]["definition"]
var = ok(plat.post("/export-layouts", json={
    "name": "Layout Domínio (variação)", "targetSystem": "Domínio", "organizationId": HOLOGRAM,
    "definition": definition}), "layout variação")
d2 = json.loads(json.dumps(definition))
d2["hasHeader"] = True
ok(plat.post(f"/export-layouts/{var['id']}/versions", json={"definition": d2}),
   "variação ganha a versão 2 (com cabeçalho)")

ana = ok(plat.post("/users", json={
    "name": "Ana Souza", "email": "ana.souza@exemplo.com.br", "password": USER_PASS,
    "role": "manager", "organization_id": HOLOGRAM}), "gerente Ana Souza")
ok(plat.post(f"/clients/{horizonte}/managers", json={"user_id": ana["id"]}), "Ana na carteira")
ok(plat.post(f"/clients/{padaria}/managers", json={"user_id": ana["id"]}), "Ana na Padaria")

af = f"/clients/{horizonte}/accounting-files"
ok(plat.post(af, json={"layoutId": lay["id"], "competence": COMP}), "geração 1 (plataforma)")
ok(plat.post(af, json={"layoutId": var["id"], "competence": COMP}), "geração 2 (variação)")
ana_c = login("ana.souza@exemplo.com.br", USER_PASS)
ok(ana_c.post(af, json={"layoutId": lay["id"], "competence": COMP}), "geração 3 (Ana)")

print("\nIDS", json.dumps(ids))
