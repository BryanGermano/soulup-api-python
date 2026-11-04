"""Menus de terminal para operar a API Python e exportar consultas em JSON.

Execute ``python menu.py --url http://127.0.0.1:8000`` com a API iniciada.
O cliente utiliza somente a biblioteca padrão e mantém o token em memória.
"""
import argparse
import getpass
import json
import os
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class ErroMenu(Exception):
    """Falha de entrada, conexão ou operação HTTP apresentada ao operador."""


class ClienteAPI:
    """Envia operações à mesma API utilizada pela aplicação web."""

    def __init__(self, url):
        """Recebe a URL HTTP(S) da API, sem usuário, senha ou parâmetros."""
        parts = urlsplit(url.strip())
        if (parts.scheme not in {'http', 'https'} or not parts.hostname
                or parts.username or parts.password or parts.query or parts.fragment):
            raise ErroMenu('Informe uma URL HTTP(S) válida para a API.')
        self.url = url.strip().rstrip('/')
        self.token = None

    def solicitar(self, metodo, rota, dados=None):
        """Retorna o JSON da resposta, ou None em respostas sem conteúdo.

        Gravações nunca são repetidas automaticamente: uma falha de rede pode
        ocorrer depois de o servidor confirmar a transação no banco.
        """
        headers = {'Accept': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        if dados is not None:
            headers['Content-Type'] = 'application/json'
        req = Request(self.url + rota, method=metodo, headers=headers,
                      data=None if dados is None else json.dumps(dados).encode('utf-8'))
        aviso = (' Consulte os registros antes de repetir a operação.'
                 if metodo != 'GET' else '')
        try:
            with urlopen(req, timeout=60) as response:
                raw = response.read()
        except HTTPError as erro:
            if erro.code == 401:
                self.token = None
            try:
                body = json.loads(erro.read())
                message = body.get('erro') if isinstance(body, dict) else None
            except (ValueError, OSError):
                message = None
            finally:
                erro.close()
            message = message if isinstance(message, str) else 'A API recusou a solicitação.'
            raise ErroMenu(f'HTTP {erro.code}: {message}' + aviso) from None
        except (URLError, OSError, HTTPException) as erro:
            raise ErroMenu('Não foi possível conectar à API.' + aviso) from erro
        try:
            return json.loads(raw) if raw else None
        except (ValueError, UnicodeError) as erro:
            raise ErroMenu('A API retornou uma resposta JSON inválida.' + aviso) from erro


def ler_texto(rotulo, minimo, maximo):
    """Lê texto do terminal, validando o tamanho antes de retornar a string."""
    value = input(rotulo).strip()
    if not minimo <= len(value) <= maximo:
        raise ErroMenu(f'Informe entre {minimo} e {maximo} caracteres.')
    return value


def ler_inteiro(rotulo, maximo=None):
    """Converte a entrada em inteiro positivo, com limite superior opcional."""
    raw = input(rotulo).strip()
    try:
        value = int(raw)
    except ValueError as erro:
        raise ErroMenu('Informe um número inteiro positivo.') from erro
    if value < 1 or (maximo is not None and value > maximo):
        raise ErroMenu('Número fora do intervalo permitido.')
    return value


class Menu:
    """Organiza autenticação, CRUD, consultas e exportações em submenus."""

    def __init__(self, cliente, pasta_exportacao='exportacoes'):
        """Recebe o cliente HTTP e o diretório dos arquivos JSON de saída."""
        self.cliente = cliente
        self.pasta_exportacao = Path(pasta_exportacao)

    def submenu(self, titulo, opcoes):
        """Repete um menu de funções até a opção zero, tratando erros de uso."""
        while True:
            print('\n' + titulo)
            for key, (label, _) in opcoes.items():
                print(f'{key} - {label}')
            print('0 - Voltar / sair')
            choice = input('Opção: ').strip()
            if choice == '0':
                return
            if choice not in opcoes:
                print('Opção inválida.')
                continue
            try:
                opcoes[choice][1]()
            except (ErroMenu, OSError) as erro:
                print(f'Erro: {erro}')

    def entrar(self):
        """Autentica funcionário sem exibir ou gravar sua senha no terminal."""
        if self.cliente.token:
            raise ErroMenu('Já existe uma sessão. Encerre-a antes de entrar novamente.')
        senha = getpass.getpass('Senha do funcionário: ')
        result = self.cliente.solicitar('POST', '/auth/funcionarios', {'senha': senha})
        if (not isinstance(result, dict) or result.get('perfil') != 'funcionario'
                or result.get('tipo') != 'Bearer'
                or not isinstance(result.get('token'), str) or not result['token']):
            raise ErroMenu('O servidor não confirmou a sessão de funcionário.')
        self.cliente.token = result['token']
        print('Funcionário autenticado.')

    def sair(self):
        """Revoga a sessão na API; falhas de rede permitem tentar novamente."""
        if self.cliente.token:
            self.cliente.solicitar('POST', '/auth/logout')
            self.cliente.token = None
            print('Sessão encerrada.')

    def exigir_sessao(self):
        """Exige autenticação antes de solicitar campos de operações restritas."""
        if not self.cliente.token:
            raise ErroMenu('Use a opção de entrar como funcionário primeiro.')

    def mostrar(self, rota):
        """Consulta e apresenta os registros como JSON legível em UTF-8."""
        data = self.cliente.solicitar('GET', rota)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        if data == []:
            print('Nenhum registro encontrado.')

    def dados_desafio(self):
        """Retorna um dicionário com descrição, categoria e pontos validados."""
        return {
            'descricao': ler_texto('Descrição (5 a 1000 caracteres): ', 5, 1000),
            'categoria': ler_texto('Categoria (3 a 100 caracteres): ', 3, 100),
            'pontos': ler_inteiro('Pontos (1 a 10000): ', 10000),
        }

    def inserir(self):
        """Insere um desafio; o ID é gerado pelo banco da API Python."""
        self.exigir_sessao()
        result = self.cliente.solicitar('POST', '/desafios', self.dados_desafio())
        print('Desafio cadastrado:', json.dumps(result, ensure_ascii=False))

    def alterar(self):
        """Consulta o desafio e substitui seus campos após validar a entrada."""
        self.exigir_sessao()
        identifier = ler_inteiro('ID do desafio: ')
        self.mostrar(f'/desafios/{identifier}')
        data = self.dados_desafio()
        active = input('Ativo? [s/n]: ').strip().lower()
        if active not in {'s', 'n'}:
            raise ErroMenu('Informe s ou n para o estado do desafio.')
        data['ativo'] = active == 's'
        self.cliente.solicitar('PUT', f'/desafios/{identifier}', data)
        print('Desafio alterado.')

    def excluir(self):
        """Exclui somente após consulta do registro e confirmação explícita."""
        self.exigir_sessao()
        identifier = ler_inteiro('ID do desafio: ')
        self.mostrar(f'/desafios/{identifier}')
        if input('Digite EXCLUIR para confirmar: ').strip() != 'EXCLUIR':
            print('Exclusão cancelada.')
            return
        self.cliente.solicitar('DELETE', f'/desafios/{identifier}')
        print('Desafio excluído.')

    def consultar(self):
        """Consulta um desafio por ID, tratando inexistentes no menu chamador."""
        identifier = ler_inteiro('ID do desafio: ')
        self.mostrar(f'/desafios/{identifier}')

    def desafios(self):
        """Oferece todas as quatro operações do CRUD e exportação JSON."""
        self.submenu('CRUD de desafios Python', {
            '1': ('Inserir', self.inserir),
            '2': ('Consultar todos', lambda: self.mostrar('/desafios')),
            '3': ('Consultar por ID', self.consultar),
            '4': ('Alterar', self.alterar),
            '5': ('Excluir', self.excluir),
            '6': ('Exportar consulta para JSON', lambda: self.exportar('desafios')),
        })

    def exportar(self, entidade):
        """Salva uma consulta em arquivo JSON UTF-8 novo, sem sobrescrever outros.

        A lista vem do endpoint de exportação autenticado. Não exporta tokens
        ou hashes de senha; o diretório é criado apenas depois de obter os dados.
        """
        self.exigir_sessao()
        if entidade not in {'usuarios', 'grupos', 'desafios', 'acoes'}:
            raise ErroMenu('Entidade de exportação inválida.')
        data = self.cliente.solicitar('GET', '/exportacoes/' + entidade)
        if not isinstance(data, list):
            raise ErroMenu('O servidor não retornou uma lista para exportação.')
        self.pasta_exportacao.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        path = self.pasta_exportacao / f'{entidade}-{stamp}.json'
        try:
            with path.open('x', encoding='utf-8') as arquivo:
                json.dump(data, arquivo, ensure_ascii=False, indent=2)
                arquivo.write('\n')
        except FileExistsError:
            raise ErroMenu('Já existe uma exportação com esse nome. Tente novamente.') from None
        except OSError:
            path.unlink(missing_ok=True)
            raise
        else:
            print('Arquivo JSON salvo em:', path.resolve())
        return path

    def consultas(self):
        """Acessa dados Python e consultas externas feitas pela própria API."""
        self.submenu('Consultas', {
            '1': ('Usuários Python (funcionário)', lambda: self.mostrar('/usuarios')),
            '2': ('Grupos Python', lambda: self.mostrar('/grupos')),
            '3': ('Ações Python (autenticado)', lambda: self.mostrar('/acoes')),
            '4': ('Ranking Python', lambda: self.mostrar('/ranking')),
            '5': ('Desafios da API Java externa', lambda: self.mostrar('/java/desafios')),
            '6': ('Grupos da API Java externa', lambda: self.mostrar('/java/grupos')),
        })

    def exportacoes(self):
        """Oferece exportações de todas as entidades disponíveis na API."""
        self.submenu('Exportações JSON (funcionário)', {
            str(i): (name, lambda name=name: self.exportar(name))
            for i, name in enumerate(('desafios', 'usuarios', 'grupos', 'acoes'), 1)
        })

    def executar(self):
        """Executa o menu principal e tenta revogar a sessão ao encerrar."""
        try:
            self.submenu('Soul Up - Python', {
                '1': ('Entrar como funcionário', self.entrar),
                '2': ('CRUD de desafios', self.desafios),
                '3': ('Consultas Python e API externa', self.consultas),
                '4': ('Exportar consultas para JSON', self.exportacoes),
                '5': ('Verificar banco e configuração', lambda: self.mostrar('/pronto')),
                '6': ('Encerrar sessão', self.sair),
            })
        except (EOFError, KeyboardInterrupt):
            print('\nEncerrando o menu.')
        finally:
            try:
                self.sair()
            except ErroMenu as erro:
                print(f'Não foi possível confirmar o logout: {erro}')
            print('Menu encerrado.')


def main():
    """Lê argumentos e inicia o cliente sem conectar diretamente ao banco."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=os.getenv('SOULUP_PYTHON_API_URL', 'http://127.0.0.1:8000'))
    parser.add_argument('--exportacoes', default='exportacoes', help='Pasta dos arquivos JSON')
    args = parser.parse_args()
    try:
        cliente = ClienteAPI(args.url)
    except (ErroMenu, ValueError) as erro:
        parser.error(str(erro))
    Menu(cliente, args.exportacoes).executar()


if __name__ == '__main__':
    main()
