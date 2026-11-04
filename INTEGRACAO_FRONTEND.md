# Integração entre o frontend e o Python

Conferência do código local em 04/11/2026. O frontend consultado está na pasta
`Challenge Corrigido/challenge-front-end-fiap`. Este relatório descreve os contratos
observados e não certifica o site publicado. Nenhum arquivo do frontend foi alterado.

## O que continua compatível

O servidor Node do site chama `POST /auth/login` e `POST /auth/funcionarios` no
Python, recebe o Bearer, verifica `GET /auth/me` e revoga a sessão com
`POST /auth/logout`. O cadastro público usa `POST /usuarios`. O menu novo utiliza
as mesmas rotas, sem mudar seus contratos.

Não é necessária alteração no frontend para usar o programa de terminal ou manter
esse fluxo de autenticação. É necessário conferir o deploy e as variáveis de
ambiente reais: `SOULUP_PYTHON_API_URL` no servidor do site e `SOULUP_ORIGENS` no
Python. No contrato atual, o cadastro Python depende também da consulta Java.

**Correção de configuração necessária no Render:** durante a conferência, o
Python publicado recusou `Origin: https://soul-up-plum.vercel.app` com HTTP 403.
Configure `SOULUP_ORIGENS` com
`http://localhost:5173,https://soul-up-plum.vercel.app` e reaplique a configuração
do serviço. O padrão do código e `render.yaml` foram atualizados com essas origens;
uma variável já definida no painel continua prevalecendo sobre o padrão do código.
O bloqueio pode afetar requisições do navegador mesmo quando passam por proxy.
Essa configuração é do backend e não exige trocar os formulários de login.

## Quando o frontend precisa mudar

**Se o objetivo é mostrar no site os desafios criados ou alterados pelo menu,
o frontend precisa de ajustes.** A situação observada é:

| Informação | Frontend atual | API Python |
| --- | --- | --- |
| Cadastro/login | Python por Node | `/usuarios`, `/auth/*` |
| Catálogo/CRUD | Java: `/api/java/desafios` | `/desafios`, `/desafios/{id}` |
| Evidências e análises | `localStorage` | `/acoes`, `/acoes/{id}/analise` |
| Ranking demonstrativo | Calculado no navegador | `/ranking` |

O programa de terminal opera as tabelas Python. Os desafios Java não são
automaticamente copiados para elas. Não reutilize IDs Java como IDs Python.

## Ajustes concretos para compartilhar o CRUD Python

1. **Serviço de desafios:** em `src/services/api.ts`, separar os tipos e as funções
   Java dos tipos/funções Python. Consultar o Python com `GET /desafios` e
   `GET /desafios/{id}`. O modelo Python contém `id`, `descricao`, `categoria`,
   `pontos` e `ativo` (0/1); não contém `titulo`.
2. **Formulários de gestão:** criar com `POST /desafios`; editar com
   `PUT /desafios/{id}`, incluindo `ativo: true/false`; excluir com
   `DELETE /desafios/{id}`. O Java atual usa PUT na coleção e DELETE por query
   string, portanto trocar apenas a URL-base não é suficiente. Após criar,
   consultar o registro pelo `id` retornado pelo Python.
3. **Autorização no servidor do site:** encaminhar o Bearer de funcionário nas
   escritas e exportações Python. Hoje os cookies HttpOnly têm caminhos limitados
   a `/api/auth` e `/api/acesso-funcionario`; eles não autenticam automaticamente
   uma chamada genérica a `/api/desafios`. Criar handlers sob o prefixo de cookie
   correspondente, com rotas permitidas, verificação de origem nas escritas e
   encaminhamento do token pelo servidor. Não devolver o token ao JavaScript.
4. **Exportação:** oferecer download autenticado de
   `GET /exportacoes/desafios` pelo servidor do site, preservando o tipo JSON
   e o cabeçalho `Content-Disposition`.
5. **Estado e erros:** atualizar a lista após confirmação; tratar 401 (sessão),
   403 (perfil), 404 (inexistente), 409 (vínculos/conflitos) e falhas de serviço.
   O Python retorna o texto no campo `erro`. Impedir a exclusão de desafios com
   ações e permitir sua desativação. Não repetir uma escrita após timeout.

Para persistir também evidências, pareceres e ranking, é preciso substituir as
funções locais em `src/services/challenges.ts` pelas rotas Python, usando o token
de usuário para publicar/validar ações e o de funcionário para análise. A API atual
aceita evidência textual de até 2000 caracteres; não recebe fotos em base64. Fotos
exigem um contrato de upload/armazenamento próprio antes de ligar esse fluxo.

## Conferência manual após ajustes

- Criar um desafio pelo menu e conferir a listagem no site usando o ID Python.
- Alterar o registro no site e consultar pelo menu; verificar campos e estado.
- Exportar JSON pelo terminal e pelo site e comparar a consulta.
- Excluir um desafio de demonstração sem ações e confirmar ausência nas duas telas.
- Conferir que usuário comum não consegue escrever como funcionário.
- Recarregar a página para confirmar persistência, e conferir logout e expiração.

Para a Entrega 4 de Python, o enunciado também aceita informações relevantes ao
projeto. O programa mantém essa relação com a Soul Up mesmo sem migrar o catálogo
Java do site. A demonstração deve descrever corretamente qual serviço guarda cada
informação.

## Verificações realizadas em 04/11/2026

- 17 testes locais passaram, usando SQLite temporário e Java simulado.
- `/pronto` do Python publicado respondeu HTTP 200 com `banco: conectado`.
  Isso confirma a consulta de prontidão, mas não demonstra um CRUD real completo.
- A função Python `listar_java('/desafios')` consultou a API Java publicada e
  recebeu uma lista válida com seis registros, sem alterações remotas.
- `/api/auth/me` do site sem sessão respondeu HTTP 401, como esperado.
- A origem pública do site foi recusada pelo Python com HTTP 403. Aplicar a
  configuração de origens e repetir a verificação após publicar as alterações.
- As primeiras consultas ao Python/site excederam o tempo de resposta; a segunda
  tentativa de leitura respondeu. Não houve repetição de gravações remotas.
