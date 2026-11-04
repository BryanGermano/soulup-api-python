"""Fluxos reais de terminal -> HTTP -> API -> banco local isolado."""
import contextlib
import io
import json
import tempfile
import threading
import unittest
from datetime import timedelta
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError
from zipfile import ZipFile

from sqlalchemy import create_engine, event, select, update
from werkzeug.serving import WSGIRequestHandler, make_server

import app as module
from database import desafios, inicializar, sessoes
from menu import ClienteAPI, ErroMenu, Menu
from preparar_entrega import ARQUIVOS, preparar


class SemLog(WSGIRequestHandler):
    def log_request(self, code='-', size='-'):
        pass


class MenuTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.engine = create_engine('sqlite:///' + str(self.base / 'test.db'))

        @event.listens_for(self.engine, 'connect')
        def ativar_chaves_estrangeiras(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')

        inicializar(self.engine)
        self.app = module.create_app(self.engine, 'senha-teste-123')
        self.server = make_server('127.0.0.1', 0, self.app, request_handler=SemLog)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.cliente = ClienteAPI(f'http://127.0.0.1:{self.server.server_port}')
        self.menu = Menu(self.cliente, self.base / 'exportacoes')

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.engine.dispose()
        self.tmp.cleanup()

    def entrar(self):
        with patch('menu.getpass.getpass', return_value='senha-teste-123'):
            self.menu.entrar()

    def test_menu_crud_exportacao_e_logout_por_http(self):
        entries = [
            '99', '2', '1', '0',  # Opção inválida e escrita sem autenticação.
            '1', '2',             # Login e submenu CRUD.
            '1', 'Separar resíduos por cinco dias', 'Reciclagem', '100',
            '2', '3', '1',        # Listagem e consulta por ID.
            '4', '1', 'Plantar uma árvore no bairro', 'Plantio', '250', 's',
            '6',                  # Exportar o resultado alterado.
            '5', '1', 'cancelar',  # Exclusão exige confirmação.
            '3', '1',
            '5', '1', 'EXCLUIR',
            '3', '1',             # Registro excluído -> 404 tratado.
            '2', '0', '0',        # Lista vazia, volta e encerra com logout.
        ]
        output = io.StringIO()
        with (patch('builtins.input', side_effect=entries) as entrada,
              patch('menu.getpass.getpass', return_value='senha-teste-123'),
              contextlib.redirect_stdout(output)):
            self.menu.executar()
        self.assertEqual(entrada.call_count, len(entries))
        for message in ('Opção inválida', 'primeiro', 'Desafio cadastrado',
                        'Desafio alterado', 'Exclusão cancelada', 'Desafio excluído',
                        'HTTP 404', 'Nenhum registro', 'Sessão encerrada'):
            self.assertIn(message, output.getvalue())
        files = list((self.base / 'exportacoes').glob('*.json'))
        self.assertEqual(len(files), 1)
        exported = json.loads(files[0].read_text(encoding='utf-8'))
        self.assertEqual(exported[0]['descricao'], 'Plantar uma árvore no bairro')
        self.assertEqual(exported[0]['pontos'], 250)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(desafios)).all(), [])
            self.assertEqual(connection.execute(select(sessoes)).all(), [])

    def test_entrada_invalida_nao_grava_e_exclusao_inexistente_e_tratada(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.entrar()
        for entradas in (['curta', 'Reciclagem', '0'],
                         ['curta', 'Reciclagem', '10001'],
                         ['curta', 'Reciclagem', 'abc'],
                         ['x']):
            with patch('builtins.input', side_effect=entradas), self.assertRaises(ErroMenu):
                self.menu.inserir()
        with patch('builtins.input', return_value='999'), self.assertRaisesRegex(ErroMenu, 'HTTP 404'):
            self.menu.excluir()
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(desafios)).all(), [])

    def test_expiracao_da_sessao_e_exportacao_nao_autorizada(self):
        with self.assertRaises(ErroMenu):
            self.menu.exportar('usuarios')
        self.assertFalse((self.base / 'exportacoes').exists())
        with contextlib.redirect_stdout(io.StringIO()):
            self.entrar()
        with self.engine.begin() as connection:
            connection.execute(update(sessoes).values(expira=module.agora() - timedelta(seconds=1)))
        with self.assertRaisesRegex(ErroMenu, 'HTTP 401'):
            self.menu.exportar('usuarios')
        self.assertIsNone(self.cliente.token)
        self.assertFalse((self.base / 'exportacoes').exists())

    def test_consulta_api_externa_e_indisponibilidade(self):
        with patch('app.java', return_value=[{'id': 7, 'titulo': 'Desafio externo'}]):
            result = self.cliente.solicitar('GET', '/java/desafios')
        self.assertEqual(result[0]['id'], 7)
        with patch('app.java', side_effect=module.Erro('Java indisponível', 502)):
            with self.assertRaisesRegex(ErroMenu, 'HTTP 502'):
                self.cliente.solicitar('GET', '/java/desafios')

    def test_interrupcao_do_terminal_revoga_sessao(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.entrar()
            with patch('builtins.input', side_effect=EOFError):
                self.menu.executar()
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(sessoes)).all(), [])


class ClienteEPacoteTest(unittest.TestCase):
    def test_gravacao_com_falha_de_rede_nao_e_repetida(self):
        cliente = ClienteAPI('http://127.0.0.1:8000')
        for falha in (URLError('offline'), IncompleteRead(b'parcial')):
            with patch('menu.urlopen', side_effect=falha) as remote:
                with self.assertRaisesRegex(ErroMenu, 'antes de repetir'):
                    cliente.solicitar('POST', '/desafios', {'pontos': 100})
            self.assertEqual(remote.call_count, 1)

    def test_zip_contem_codigo_e_documentacao_sem_dados_locais(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            for name in ARQUIVOS:
                (base / name).write_text('arquivo de teste', encoding='utf-8')
            (base / '.env').write_text('segredo', encoding='utf-8')
            (base / 'exportacoes').mkdir()
            (base / 'exportacoes' / 'usuarios.json').write_text('[]')
            path = preparar(base)
            with ZipFile(path) as package:
                self.assertIsNone(package.testzip())
                self.assertEqual(len(package.namelist()), len(ARQUIVOS))
                self.assertNotIn('soulup-api-python/.env', package.namelist())
                self.assertFalse(any('/exportacoes/' in name for name in package.namelist()))


if __name__ == '__main__':
    unittest.main()
