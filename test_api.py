"""Testes locais sem credenciais: SQLite só é injetado pelos testes; Java simulado."""
import hashlib
import tempfile
import unittest
from datetime import timedelta
from unittest.mock import patch
from sqlalchemy import create_engine, select, update
from sqlalchemy.schema import CreateTable, CreateSequence
from sqlalchemy.dialects import oracle
import app as module
from database import inicializar, contas, sessoes, metadata, acoes

class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine('sqlite:///' + self.tmp.name + '/test.db')
        inicializar(self.engine)
        self.app = module.create_app(self.engine, 'senha-teste-123')
        self.client = self.app.test_client()
        self.remote_users = []
        self.remote_groups = []
        self.mock = patch('app.java', side_effect=self.java)
        self.mock.start()

    def tearDown(self):
        self.mock.stop()
        self.engine.dispose()
        self.tmp.cleanup()

    def java(self, method, path, data=None):
        target = self.remote_users if path == '/usuarios' else self.remote_groups
        if method == 'GET':
            return list(target)
        item = dict(data)
        if path == '/grupos': item['id'] = len(target) + 1
        target.append(item)
        return {'id': 0} if path == '/grupos' else item

    def employee(self):
        r = self.client.post('/auth/funcionarios', json={'senha': 'senha-teste-123'})
        self.assertEqual(r.status_code, 200)
        return {'Authorization': 'Bearer ' + r.json['token']}

    def user(self, index):
        data = {'nome': 'Pessoa ' + str(index), 'email': f'u{index}@example.com', 'senha': 'senha-teste-123'}
        r = self.client.post('/usuarios', json=data)
        self.assertEqual(r.status_code, 201, r.json)
        self.assertTrue(r.json['api_confirmado'])
        login = self.client.post('/auth/login', json=data)
        self.assertEqual(login.status_code, 200)
        return r.json['id'], {'Authorization': 'Bearer ' + login.json['token']}

    def challenge(self, staff):
        r = self.client.post('/desafios', json={'descricao': 'Separar papel reciclavel', 'categoria': 'Reciclagem', 'pontos': 100}, headers=staff)
        self.assertEqual(r.status_code, 201, r.json)
        return r.json['id']

    def test_health_cors_and_fail_closed_permissions(self):
        self.assertEqual(self.client.get('/pronto').status_code, 200)
        self.assertEqual(self.client.get('/saude').status_code, 200)
        self.assertEqual(self.client.get('/usuarios').status_code, 401)
        self.assertEqual(self.client.post('/desafios', json={}).status_code, 401)
        self.assertEqual(self.client.get('/saude', headers={'Origin': 'https://nao-autorizado.test'}).status_code, 403)
        r = self.client.options('/auth/login', headers={'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'POST'})
        self.assertEqual(r.headers['Access-Control-Allow-Origin'], 'http://localhost:5173')
        self.assertEqual(self.client.post('/auth/login', data='x' * 70000, content_type='application/json').status_code, 413)

    def test_user_credentials_confirmation_and_java_password_never_sent(self):
        self.assertEqual(self.client.post('/usuarios', json={'nome': 'Pessoa', 'email': 'u@x.com', 'senha': '12345678', 'confirmacao_senha': 'diferente'}).status_code, 400)
        id, headers = self.user(1)
        self.assertNotIn('senha', self.remote_users[0])
        me = self.client.get('/auth/me', headers=headers)
        self.assertEqual(me.json['id'], id)
        self.assertNotIn('senha', me.json)
        self.assertEqual(self.client.post('/auth/login', json={'email': 'u1@example.com', 'senha': 'errada'}).status_code, 401)
        self.assertEqual(self.client.post('/desafios', json={}, headers=headers).status_code, 403)
        with self.engine.connect() as c:
            password = c.execute(select(contas.c.senha)).scalar_one()
        self.assertTrue(module.senha_confere('senha-teste-123', password))
        self.assertNotEqual(password, 'senha-teste-123')

    def test_sessions_survive_restart_expire_and_logout_revokes(self):
        headers = self.employee()
        second = module.create_app(self.engine, 'senha-teste-123').test_client()
        self.assertEqual(second.get('/usuarios', headers=headers).status_code, 200)
        self.assertEqual(second.post('/auth/logout', headers=headers).status_code, 204)
        self.assertEqual(self.client.get('/usuarios', headers=headers).status_code, 401)
        headers = self.employee()
        with self.engine.begin() as c:
            c.execute(update(sessoes).values(expira=module.agora() - timedelta(seconds=1)))
        self.assertEqual(self.client.get('/usuarios', headers=headers).status_code, 401)

    def test_staff_login_rate_limit_and_no_default_password(self):
        absent = module.create_app(self.engine, '').test_client()
        self.assertEqual(absent.post('/auth/funcionarios', json={'senha': 'qualquer'}).status_code, 503)
        for i in range(29):
            self.assertEqual(self.client.post('/auth/funcionarios', json={'senha': 'errada'}).status_code, 401)
        self.assertEqual(self.client.post('/auth/funcionarios', json={'senha': 'senha-teste-123'}).status_code, 429)

    def test_challenge_crud_and_export(self):
        staff = self.employee()
        id = self.challenge(staff)
        data = {'descricao': 'Nova descricao', 'categoria': 'Plantio', 'pontos': 200, 'ativo': False}
        self.assertEqual(self.client.put(f'/desafios/{id}', json=data, headers=staff).status_code, 200)
        self.assertEqual(self.client.get(f'/desafios/{id}').json['ativo'], 0)
        exported = self.client.get('/exportacoes/desafios', headers=staff)
        self.assertIn('attachment', exported.headers['Content-Disposition'])
        self.assertEqual(exported.json[0]['pontos'], 200)
        self.assertEqual(self.client.delete(f'/desafios/{id}', headers=staff).status_code, 204)
        self.assertEqual(self.client.get(f'/desafios/{id}').status_code, 404)

    def test_actions_votes_snapshot_and_ranking(self):
        staff = self.employee()
        challenge = self.challenge(staff)
        id, author = self.user(1)
        _, voter1 = self.user(2)
        _, voter2 = self.user(3)
        data = {'desafio_id': challenge, 'evidencia': 'Separei papel e plastico'}
        r = self.client.post('/acoes', json=data, headers=author)
        self.assertEqual(r.status_code, 201)
        action = r.json['id']
        self.assertEqual(self.client.post('/acoes', json=data, headers=author).status_code, 409)
        self.assertEqual(self.client.delete(f'/desafios/{challenge}', headers=staff).status_code, 409)
        self.assertEqual(self.client.post(f'/acoes/{action}/validacoes', json={}, headers=author).status_code, 400)
        self.assertEqual(self.client.post(f'/acoes/{action}/validacoes', json={}, headers=voter1).status_code, 200)
        self.assertEqual(self.client.post(f'/acoes/{action}/validacoes', json={}, headers=voter1).status_code, 409)
        self.assertEqual(self.client.post(f'/acoes/{action}/validacoes', json={}, headers=voter2).status_code, 200)
        self.assertEqual(self.client.get('/acoes', headers=staff).json[0]['status'], 'aprovada')
        self.assertEqual([r['pontos'] for r in self.client.get('/ranking').json], [100, 10, 10])
        self.assertEqual(self.client.patch(f'/acoes/{action}/analise', json={'aprovar': False, 'parecer': 'rejeitada'}, headers=staff).status_code, 409)

    def test_staff_analysis_and_history_points(self):
        staff = self.employee()
        challenge = self.challenge(staff)
        _, author = self.user(1)
        action = self.client.post('/acoes', json={'desafio_id': challenge, 'evidencia': 'Acao realizada'}, headers=author).json['id']
        self.client.put(f'/desafios/{challenge}', json={'descricao': 'Nova descricao', 'categoria': 'Plantio', 'pontos': 500, 'ativo': True}, headers=staff)
        self.assertEqual(self.client.patch(f'/acoes/{action}/analise', json={'aprovar': True, 'parecer': 'Evidencia aprovada'}, headers=staff).status_code, 200)
        self.assertEqual(self.client.get('/ranking').json[0]['pontos'], 100)

    def test_groups_without_password_and_member_authorization(self):
        self.remote_users = [{'id': 88, 'nome': 'Pessoa Remota', 'email': 'remota@example.com'}]
        r = self.client.post('/grupos', json={'nome': 'Grupo Um', 'membros': [88]})
        self.assertEqual(r.status_code, 201, r.json)
        self.assertTrue(r.json['api_confirmado'])
        group = r.json['id']
        self.assertEqual(self.client.get('/grupos').json[0]['membros'][0]['api_usuario_id'], 88)
        self.assertEqual(self.client.delete(f'/grupos/{group}/membros/88').status_code, 401)
        self.assertEqual(self.client.delete(f'/grupos/{group}/membros/88', headers=self.employee()).status_code, 204)
        self.assertEqual(self.client.get('/grupos').json[0]['membros'], [])

    def test_java_timeout_keeps_account_and_does_not_repeat_post(self):
        def fail(method, path, data=None):
            if method == 'GET': return []
            raise module.Erro('Falha Java', 502)
        with patch('app.java', side_effect=fail) as remote:
            r = self.client.post('/usuarios', json={'nome': 'Conta Pendente', 'email': 'pendente@example.com', 'senha': '12345678'})
            self.assertEqual(r.status_code, 201)
            self.assertFalse(r.json['api_confirmado'])
            self.assertEqual(sum(x.args[0] == 'POST' for x in remote.call_args_list), 1)
        self.assertEqual(self.client.post('/auth/login', json={'email': 'pendente@example.com', 'senha': '12345678'}).status_code, 200)

    def test_schema_compiles_for_oracle_and_initialization_preserves_data(self):
        self.user(1)
        inicializar(self.engine)
        with self.engine.connect() as c:
            self.assertEqual(len(c.execute(select(contas)).all()), 1)
        for table in metadata.sorted_tables:
            ddl = str(CreateTable(table).compile(dialect=oracle.dialect()))
            self.assertIn('su_py_', ddl)
            for col in table.columns:
                if hasattr(col.default, 'name'):
                    self.assertIn('CREATE SEQUENCE', str(CreateSequence(col.default).compile(dialect=oracle.dialect())))

if __name__ == '__main__':
    unittest.main()
