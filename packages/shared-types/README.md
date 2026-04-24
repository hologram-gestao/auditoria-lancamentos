# @auditoria/shared-types

Tipos TypeScript compartilhados entre frontend e backend.

A estratégia é **gerar tipos a partir do OpenAPI** exposto pelo FastAPI,
evitando manter definições duplicadas e garantindo que qualquer breaking
change na API seja detectada no compile-time do frontend.

## Uso

```bash
# Com a API rodando em http://localhost:8000
pnpm --filter @auditoria/shared-types generate
```

Isso gera `src/api.ts` com todos os schemas, paths, components e responses.

No frontend:

```ts
import type { components } from '@auditoria/shared-types/api';

type User = components['schemas']['UserResponse'];
```

## Quando atualizar

- Sempre que uma rota ou schema for adicionado/alterado no backend.
- Integrar essa geração ao CI para que PRs sem tipos atualizados falhem.
