# Soul Up — Entrega 4 de Python

Programa da equipe Nexus Elite para gerenciar desafios de sustentabilidade. A API
Flask persiste dados no Oracle, oferece autenticação e consome a API Java do projeto.
O programa `menu.py` fornece menus e submenus no terminal, com CRUD completo de
desafios e exportação das consultas para arquivos JSON.

## Requisitos e instalação

Instale Python 3.12 ou superior. O ambiente de produção usa a versão definida em
`render.yaml`. É necessário acesso de rede ao Oracle e um usuário com permissões
para criar tabelas e sequências no próprio esquema.

No PowerShell, dentro desta pasta:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Se a ativação do ambiente estiver desabilitada, use
`.\.venv\Scripts\python.exe` no lugar de `python` nos comandos seguintes.

## Configurar o banco e iniciar a API

`.env.example` documenta as variáveis necessárias; os programas **não carregam
arquivos .env automaticamente**. Defina as variáveis no terminal antes de iniciar
a API ou no painel do Render. Exemplo local:

```powershell
$env:ORACLE_HOST = 'oracle.fiap.com.br'
$env:ORACLE_PORT = '1521'
$env:ORACLE_SID = 'ORCL'
$env:ORACLE_USER = 'SEU_USUARIO'
$env:ORACLE_PASSWORD = [System.Net.NetworkCredential]::new('', (Read-Host 'Senha Oracle' -AsSecureString)).Password
$env:SOULUP_SENHA_FUNCIONARIOS = [System.Net.NetworkCredential]::new('', (Read-Host 'Defina a senha de funcionário, mínimo 8 caracteres' -AsSecureString)).Password
$env:SOULUP_ORIGENS = 'http://localhost:5173,https://soul-up-plum.vercel.app'
$env:SOULUP_API_URL = 'https://challeng-sprin4-java.onrender.com'
python init_db.py
python app.py
```

`init_db.py` cria apenas tabelas e sequências Python ausentes, com prefixo `SU_PY_`,
e não apaga dados existentes. Ele não realiza migrações de colunas em tabelas já
criadas. O Oracle utiliza SID `ORCL`, não `service_name`.

A API local fica em `http://127.0.0.1:8000`. `/saude` verifica o processo;
`/pronto` consulta o banco e verifica se a senha de funcionário foi configurada.
O servidor de desenvolvimento é para uso local. No Render, a inicialização e o
Gunicorn estão configurados em `render.yaml`.

## Usar o programa com menus

Com a API em execução, abra outro terminal nesta pasta:

```powershell
.\.venv\Scripts\python.exe menu.py --url http://127.0.0.1:8000
```

Para operar uma API publicada, informe sua URL direta:

```powershell
python menu.py --url https://soulup-api-python.onrender.com
```

O menu de terminal usa apenas a biblioteca padrão do Python. A API precisa ter
as dependências e o Oracle configurados, mesmo quando o menu roda em outra máquina.

| Menu principal | Opções |
|---|---|
| 1 — Entrar como funcionário | Autenticação com a senha definida no servidor |
| 2 — CRUD de desafios | Inserir, consultar todos, consultar por ID, alterar, excluir e exportar |
| 3 — Consultas | Usuários, grupos, ações e ranking Python; desafios e grupos da API Java externa |
| 4 — Exportações | Salvar usuários, grupos, desafios ou ações em JSON |
| 5 — Verificar banco | Consultar `/pronto` |
| 6 — Encerrar sessão | Revogar o token na API |
| 0 — Voltar/sair | Voltar do submenu ou encerrar o programa |

As operações de escrita e exportação exigem login de funcionário. Na exclusão,
digite `EXCLUIR` para confirmar. Um desafio com ações vinculadas deve ser
desativado na alteração, pois a API impede sua exclusão para preservar o histórico.
O estado ativo é informado com `s` ou `n`.

As exportações são arquivos JSON UTF-8 em `exportacoes/`, com nomes que incluem a
data e hora UTC. É possível escolher outra pasta com `--exportacoes CAMINHO`.
Senhas e tokens não fazem parte dos arquivos exportados. Exportações podem conter
nomes e e-mails e não são incluídas no ZIP da entrega.

O programa trata opções inválidas, números inválidos, limites de campos,
registros inexistentes, sessão expirada, indisponibilidade e conflitos. Uma
gravação com resposta incerta nunca é repetida automaticamente. Consulte os
registros antes de tentar novamente. Ao sair, o menu tenta revogar a sessão;
se não conseguir, informa a falha. Sessões também expiram no servidor em uma hora.

## Contrato HTTP do Python

As rotas abaixo são relativas à raiz da API, sem `/api`.

| Operação | Rota e contrato |
|---|---|
| Autenticar funcionário | `POST /auth/funcionarios`, JSON `senha` |
| Autenticar usuário | `POST /auth/login`, JSON `email`, `senha` |
| Conferir ou encerrar sessão | `GET /auth/me`, `POST /auth/logout` |
| Cadastrar conta | `POST /usuarios`, JSON `nome`, `email`, `senha`, `confirmacao_senha` opcional |
| Consultar contas Python | `GET /usuarios`, funcionário |
| Consultar desafios Python | `GET /desafios`, `GET /desafios/{id}` |
| Inserir desafio | `POST /desafios`, JSON `descricao`, `categoria`, `pontos`; funcionário |
| Alterar desafio | `PUT /desafios/{id}`, mesmos campos e `ativo` booleano; funcionário |
| Excluir desafio | `DELETE /desafios/{id}`; funcionário |
| Exportar consulta | `GET /exportacoes/{entidade}`; funcionário; entidades: `usuarios`, `grupos`, `desafios`, `acoes` |
| Consultar API externa | `GET /java/desafios`, `GET /java/grupos`, `GET /pessoas` |
| Ações e ranking Python | `GET /acoes` autenticado; `GET /ranking` público |

Na criação e alteração de desafios, `descricao` aceita 5–1000 caracteres,
`categoria` 3–100 e `pontos` um inteiro entre 1 e 10000. O Python não exige `titulo`.
A criação retorna `{"id": ...}`. Listagem e detalhe retornam os campos persistidos,
incluindo `ativo` como 0 ou 1. A exclusão retorna HTTP 204 sem JSON.

O login retorna `token`, `tipo: "Bearer"`, `perfil` e `expira_em`. Requisições
restritas devem enviar `Authorization: Bearer TOKEN`. O Python não cria cookies de
sessão. Erros usam JSON com o campo `erro` e código HTTP correspondente.

O cadastro de conta consulta primeiro os usuários Java. Se essa consulta falhar,
o cadastro não é concluído. Após salvar a conta Python, uma falha na gravação Java
retorna `api_confirmado: false`; não repita o cadastro. IDs Python e Java são
distintos, e só se considera o vínculo confirmado quando a consulta externa o
comprova. Os testes usam uma API Java simulada e não certificam o contrato publicado.

## Frontend

O frontend revisado usa Python para cadastro e autenticação por meio de seu servidor
Node. O token permanece em cookie HttpOnly no site e é enviado como Bearer pelo
servidor. Esses endpoints continuam compatíveis.

O catálogo e o CRUD de desafios do site atual usam **Java**, enquanto o menu de
terminal opera os desafios **Python**. Evidências e ranking demonstrativos do site
ficam no navegador. Portanto, alterações no CRUD Python não aparecem automaticamente
nessas telas. Consulte [INTEGRACAO_FRONTEND.md](INTEGRACAO_FRONTEND.md) para os ajustes
necessários caso essas telas devam compartilhar os dados persistidos no Python.

## Testes

```powershell
python -m unittest discover -v
```

Os testes usam bancos SQLite temporários; não precisam de credenciais Oracle e não
alteram dados remotos. Verificam a API, regras de autenticação e permissões,
CRUD, exportação, ações, pontos e sessões. Também exercitam o menu por HTTP real
local até o banco, incluindo erros, cancelamento de exclusão, download para arquivo
e logout. O código SQL também é compilado para o dialeto Oracle nos testes.

Antes da entrega, valide separadamente o Oracle e os serviços publicados. Testes
locais não certificam a conexão real, a versão de produção ou a disponibilidade Java.

## Estrutura

```text
app.py                  API, validação e regras do projeto
database.py             Conexão Oracle e tabelas SQLAlchemy
init_db.py              Inicialização não destrutiva das tabelas
menu.py                 Programa interativo com menus e submenus
test_api.py             Testes da API
test_menu.py            Testes de terminal, HTTP e pacote
preparar_entrega.py      Gerador e verificador do ZIP
requirements.txt        Dependências da API
render.yaml             Configuração de publicação
.env.example            Referência das variáveis de ambiente
ENTREGA_PYTHON.txt       Integrantes, roteiro e pendências da entrega
INTEGRACAO_FRONTEND.md   Contratos e ajustes para compartilhar dados com o site
```

## Preparar a entrega

O documento [ENTREGA_PYTHON.txt](ENTREGA_PYTHON.txt) contém os participantes,
requisitos e roteiro do vídeo de até cinco minutos. **O link do vídeo ainda deve
ser preenchido pela equipe.**

Depois de revisar o documento e executar os testes:

```powershell
python preparar_entrega.py
```

O pacote é salvo em `output/Soul-Up-Python-Entrega-4.zip`. O gerador usa uma lista
explícita de arquivos e verifica conteúdo e integridade. Não inclui `.env`,
credenciais, `.git`, ambiente virtual, caches, bancos locais ou exportações.
Gere o ZIP novamente após inserir o link do vídeo ou alterar o código.
