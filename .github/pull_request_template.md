# Descrição

<!-- O que mudou e por quê. Contexto além do diff. -->

## Sessão do plano

<!-- Ex: S3 — Autenticação (BACK 1.1) -->

## Tipo de mudança

- [ ] `feat` — nova funcionalidade
- [ ] `fix` — correção de bug
- [ ] `refactor` — mudança sem alterar comportamento
- [ ] `test` — apenas testes
- [ ] `docs` — apenas documentação
- [ ] `chore` — tooling, deps, etc.

## Checklist

- [ ] Segui os padrões descritos em [CLAUDE.md](../CLAUDE.md)
- [ ] Nenhuma credencial / segredo em código ou log
- [ ] `make lint && make type-check && make test` passam localmente
- [ ] Testes cobrem o fluxo golden e pelo menos um caso de erro
- [ ] RBAC / autorização verificada em novos endpoints
- [ ] Se mudou schema: migration testada (`make db-downgrade && make db-migrate`)
- [ ] Se mudou contrato de API: tipos regenerados (`pnpm --filter @auditoria/shared-types generate`)
- [ ] [CLAUDE.md](../CLAUDE.md) atualizado se houve decisão global

## Como testar

<!-- Passos manuais para o revisor validar -->
