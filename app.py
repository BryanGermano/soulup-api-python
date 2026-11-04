"""API Soul Up — NEXUS ELITE. Oracle + Flask; servidor público via Gunicorn."""
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from flask import Flask, request, jsonify, g
from sqlalchemy import select, update, delete, func
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from werkzeug.exceptions import HTTPException
from database import (engine_oracle, bloquear, contas, desafios, acoes, votos,
                      grupos, membros, sessoes, tentativas)

def agora():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Erro(Exception):
    def __init__(self, mensagem, status=400):
        self.mensagem, self.status = mensagem, status

def senha_hash(senha):
    if not isinstance(senha, str) or not 8 <= len(senha) <= 256:
        raise Erro('A senha deve ter entre 8 e 256 caracteres.')
    sal = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', senha.encode(), bytes.fromhex(sal), 600000).hex()
    return f'600000${sal}${digest}'

def senha_confere(senha, registro):
    try:
        rounds, salt, expected = registro.split('$')
        result = hashlib.pbkdf2_hmac('sha256', senha.encode(), bytes.fromhex(salt), int(rounds)).hex()
        return hmac.compare_digest(result, expected)
    except (ValueError, AttributeError, TypeError):
        return False

def corpo():
    if not request.is_json:
        raise Erro('Envie Content-Type: application/json.', 415)
    data = request.get_json()
    if not isinstance(data, dict):
        raise Erro('Envie um objeto JSON.')
    return data

def texto(data, key, minimum=1, maximum=2000, trim=True):
    value = data.get(key)
    if not isinstance(value, str):
        raise Erro(f'Informe {key}.')
    if trim:
        value = value.strip()
    if not minimum <= len(value) <= maximum:
        raise Erro(f'{key}: informe de {minimum} a {maximum} caracteres.')
    return value

def numero(data, key):
    value = data.get(key)
    if type(value) is not int or value < 1:
        raise Erro(f'{key} deve ser inteiro positivo.')
    return value

def booleano(data, key):
    if type(data.get(key)) is not bool:
        raise Erro(f'{key} deve ser true ou false.')
    return data[key]

def java(method, path, data=None):
    base = os.getenv('SOULUP_API_URL', 'https://challeng-sprin4-java.onrender.com').rstrip('/')
    req = Request(base + path, method=method,
        data=None if data is None else json.dumps(data).encode(),
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
    try:
        with urlopen(req, timeout=20) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except (HTTPError, URLError, OSError, ValueError):
        raise Erro('Java indisponível ou resposta não confirmada. Consulte os registros antes de repetir uma gravação.', 502)

def listar_java(path):
    result = java('GET', path)
    if not isinstance(result, list) or any(not isinstance(x, dict) for x in result):
        raise Erro('Java retornou uma lista inválida.', 502)
    return result

def create_app(engine=None, staff_password=None):
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=65536, JSON_SORT_KEYS=False)
    db = engine if engine is not None else engine_oracle()
    app.extensions['db'] = db
    staff = staff_password if staff_password is not None else os.getenv('SOULUP_SENHA_FUNCIONARIOS', '')
    staff_hash = senha_hash(staff) if len(staff) >= 8 else None
    dummy_hash = senha_hash(secrets.token_urlsafe(32))
    origins = {x.strip().rstrip('/') for x in os.getenv('SOULUP_ORIGENS', 'http://localhost:5173').split(',') if x.strip()}

    @app.before_request
    def verificar_origem():
        origin = request.headers.get('Origin')
        if origin and origin not in origins:
            raise Erro('Origem não permitida. Configure SOULUP_ORIGENS com o endereço do site.', 403)

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        origin = request.headers.get('Origin')
        if origin in origins:
            response.headers['Access-Control-Allow-Origin'] = origin
            response.headers['Vary'] = 'Origin'
            response.headers['Access-Control-Allow-Headers'] = 'Authorization, Content-Type'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
            response.headers['Access-Control-Expose-Headers'] = 'Content-Disposition'
        return response

    @app.errorhandler(Erro)
    def expected(e):
        return jsonify(erro=e.mensagem), e.status

    @app.errorhandler(IntegrityError)
    def integrity(e):
        return jsonify(erro='Registro duplicado ou vínculo inválido.'), 409

    @app.errorhandler(SQLAlchemyError)
    def database_error(e):
        # Não imprime SQL, parâmetros, tokens, credenciais ou dados pessoais.
        return jsonify(erro='Banco temporariamente indisponível. Confira a conexão Oracle no Render.'), 503

    @app.errorhandler(HTTPException)
    def http_error(e):
        return jsonify(erro={404: 'Rota não encontrada.', 405: 'Método não permitido.',
            413: 'Limite de 64 KB excedido.'}.get(e.code, 'Requisição inválida.')), e.code

    @app.errorhandler(Exception)
    def unexpected(e):
        return jsonify(erro='Falha interna. Confira a configuração do serviço.'), 500

    def auth(role=None):
        def decorator(fn):
            @wraps(fn)
            def wrapper(*args, **kwargs):
                header = request.headers.get('Authorization', '')
                if not header.startswith('Bearer '):
                    raise Erro('Entre novamente; sessão ausente ou expirada.', 401)
                hashed = hashlib.sha256(header[7:].encode()).hexdigest()
                with db.connect() as c:
                    session = c.execute(select(sessoes).where(sessoes.c.token_hash == hashed,
                        sessoes.c.expira > agora())).mappings().first()
                if not session:
                    raise Erro('Entre novamente; sessão ausente ou expirada.', 401)
                if role and session['perfil'] != role:
                    raise Erro('Você não tem permissão para esta operação.', 403)
                g.session = session
                return fn(*args, **kwargs)
            return wrapper
        return decorator

    def rate_limit(key):
        key = hashlib.sha256(key.encode()).hexdigest()
        now = agora()
        limited = False
        with db.begin() as c:
            bloquear(c)
            c.execute(delete(tentativas).where(tentativas.c.expira <= now))
            row = c.execute(select(tentativas).where(tentativas.c.chave == key)).mappings().first()
            if row:
                limited = row['quantidade'] >= 30
                if not limited:
                    c.execute(update(tentativas).where(tentativas.c.chave == key).values(quantidade=row['quantidade'] + 1))
            else:
                c.execute(tentativas.insert().values(chave=key, quantidade=1, expira=now + timedelta(minutes=15)))
        if limited:
            raise Erro('Muitas tentativas. Aguarde até 15 minutos.', 429)

    def token(user=None):
        raw = secrets.token_urlsafe(32)
        profile = 'usuario' if user is not None else 'funcionario'
        with db.begin() as c:
            c.execute(delete(sessoes).where(sessoes.c.expira <= agora()))
            c.execute(sessoes.insert().values(token_hash=hashlib.sha256(raw.encode()).hexdigest(),
                usuario=user, perfil=profile, expira=agora() + timedelta(hours=1)))
        return jsonify(token=raw, tipo='Bearer', expira_em=3600, perfil=profile, usuario_id=user)

    def require_row(c, table, id):
        row = c.execute(select(table).where(table.c.id == id)).mappings().first()
        if not row:
            raise Erro('Registro não encontrado.', 404)
        return row

    @app.get('/')
    def root():
        return jsonify(servico='Soul Up Python', saude='/saude', prontidao='/pronto')

    @app.get('/saude')
    def health():
        return jsonify(status='ok', servico='Soul Up Python')

    @app.get('/pronto')
    def ready():
        with db.connect() as c:
            c.execute(select(contas.c.id).where(contas.c.id == -1)).all()
        if not staff_hash:
            raise Erro('Configure SOULUP_SENHA_FUNCIONARIOS no Render.', 503)
        return jsonify(status='ok', banco='conectado')

    @app.post('/auth/funcionarios')
    def staff_login():
        password = texto(corpo(), 'senha', 1, 256, trim=False)
        rate_limit('funcionarios')
        if not staff_hash:
            raise Erro('Acesso de funcionários não configurado no servidor.', 503)
        if not senha_confere(password, staff_hash):
            raise Erro('Senha incorreta.', 401)
        return token()

    @app.post('/auth/login')
    def login():
        data = corpo()
        email = texto(data, 'email', 3, 254).lower()
        password = texto(data, 'senha', 1, 256, trim=False)
        rate_limit('login:' + email)
        with db.connect() as c:
            user = c.execute(select(contas).where(contas.c.email == email)).mappings().first()
        valid = senha_confere(password, user['senha'] if user else dummy_hash)
        if not user or not valid:
            raise Erro('E-mail ou senha incorretos.', 401)
        return token(user['id'])

    @app.get('/auth/me')
    @auth()
    def me():
        if g.session['perfil'] == 'funcionario':
            return jsonify(perfil='funcionario')
        with db.connect() as c:
            user = require_row(c, contas, g.session['usuario'])
        return jsonify({k: user[k] for k in ('id', 'nome', 'email', 'api_id')})

    @app.post('/auth/logout')
    @auth()
    def logout():
        with db.begin() as c:
            c.execute(delete(sessoes).where(sessoes.c.token_hash == g.session['token_hash']))
        return '', 204

    @app.post('/usuarios')
    def register():
        data = corpo()
        name = texto(data, 'nome', 3, 150)
        email = texto(data, 'email', 3, 254).lower()
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
            raise Erro('E-mail inválido.')
        password = texto(data, 'senha', 8, 256, trim=False)
        if data.get('confirmacao_senha', password) != password:
            raise Erro('Senhas não coincidem.')
        password_hash = senha_hash(password)
        with db.begin() as c:
            bloquear(c)
            if c.execute(select(contas.c.id).where(contas.c.email == email)).first():
                raise Erro('E-mail já cadastrado no Python.', 409)
            remote = listar_java('/usuarios')
            if any(str(u.get('email', '')).lower() == email for u in remote):
                raise Erro('E-mail já existe no Java. A vinculação precisa de comprovação de identidade; use uma conta nova para cadastrar.', 409)
            reserved = c.execute(select(func.max(contas.c.api_solicitado))).scalar() or 0
            next_id = max([u['id'] for u in remote if type(u.get('id')) is int] + [reserved]) + 1
            id = c.execute(contas.insert().values(nome=name, email=email, senha=password_hash,
                api_solicitado=next_id)).inserted_primary_key[0]
        # O cadastro Python é confirmado antes de enviar ao Java: falhas não perdem a senha.
        confirmed = False
        try:
            java('POST', '/usuarios', {'id': next_id, 'nome': name, 'email': email})
            matches = [u for u in listar_java('/usuarios') if u.get('id') == next_id and
                       str(u.get('email', '')).lower() == email and u.get('nome') == name]
            if len(matches) == 1:
                with db.begin() as c:
                    c.execute(update(contas).where(contas.c.id == id).values(api_id=next_id))
                confirmed = True
        except Erro:
            pass
        return jsonify(id=id, nome=name, email=email, api_id=next_id if confirmed else None,
            api_confirmado=confirmed, mensagem='Cadastro confirmado.' if confirmed else
            'Conta Python salva; integração Java pendente. Não repita o cadastro.'), 201

    @app.get('/usuarios')
    @auth('funcionario')
    def users():
        with db.connect() as c:
            rows = c.execute(select(contas.c.id, contas.c.nome, contas.c.email, contas.c.api_id)).mappings().all()
        return jsonify([dict(r) for r in rows])

    @app.get('/pessoas')
    def people():
        query = request.args.get('busca', '').strip().casefold()
        return jsonify([{k: u.get(k) for k in ('id', 'nome', 'email')} for u in listar_java('/usuarios')
            if not query or query in {str(u.get('nome', '')).casefold(), str(u.get('email', '')).casefold()}])

    @app.get('/java/<resource>')
    def java_read(resource):
        if resource not in {'grupos', 'desafios'}:
            raise Erro('Rota não encontrada.', 404)
        return jsonify(listar_java('/' + resource))

    def group_list():
        with db.connect() as c:
            result = [dict(r) for r in c.execute(select(grupos).order_by(grupos.c.id)).mappings()]
            for group in result:
                group['membros'] = [dict(r) for r in c.execute(select(membros.c.api_usuario_id,
                    membros.c.nome, membros.c.email).where(membros.c.grupo == group['id'])).mappings()]
        return result

    def selected_people(data):
        ids = data.get('membros')
        if not isinstance(ids, list) or len(ids) > 100 or any(type(x) is not int or x < 1 for x in ids):
            raise Erro('membros deve ser uma lista de até 100 IDs inteiros do Java.')
        ids = set(ids)
        if not ids:
            return []
        selected = [u for u in listar_java('/usuarios') if u.get('id') in ids]
        if {u.get('id') for u in selected} != ids:
            raise Erro('Há pessoas não encontradas no Java.')
        for person in selected:
            texto(person, 'nome', 1, 150)
            texto(person, 'email', 1, 254)
        return selected

    def add_members(c, id, selected):
        existing = set(c.execute(select(membros.c.api_usuario_id).where(membros.c.grupo == id)).scalars())
        for u in selected:
            if u['id'] not in existing:
                c.execute(membros.insert().values(grupo=id, api_usuario_id=u['id'], nome=u['nome'], email=u['email']))
                existing.add(u['id'])

    @app.get('/grupos')
    def groups_get():
        return jsonify(group_list())

    @app.post('/grupos')
    def group_create():
        data = corpo()
        name = texto(data, 'nome', 3, 150)
        selected = selected_people(data)
        before = listar_java('/grupos')
        if any(str(x.get('nome', '')).casefold() == name.casefold() for x in before):
            raise Erro('Já existe grupo com esse nome no Java.', 409)
        with db.begin() as c:
            bloquear(c)
            id = c.execute(grupos.insert().values(nome=name)).inserted_primary_key[0]
            add_members(c, id, selected)
        remote_id = None
        try:
            java('POST', '/grupos', {'nome': name})
            old_ids = {x.get('id') for x in before}
            matches = [x for x in listar_java('/grupos') if x.get('nome') == name and
                       type(x.get('id')) is int and x['id'] > 0 and x['id'] not in old_ids]
            if len(matches) == 1:
                remote_id = matches[0]['id']
                with db.begin() as c:
                    c.execute(update(grupos).where(grupos.c.id == id).values(api_id=remote_id, situacao='confirmado'))
        except Erro:
            pass
        return jsonify(id=id, nome=name, api_id=remote_id, api_confirmado=remote_id is not None,
            mensagem='Grupo salvo.' if remote_id else 'Grupo salvo no Python; Java pendente. Não repita.'), 201

    @app.post('/grupos/<int:id>/membros')
    @auth('funcionario')
    def member_add(id):
        selected = selected_people(corpo())
        with db.begin() as c:
            bloquear(c)
            require_row(c, grupos, id)
            add_members(c, id, selected)
        return jsonify(mensagem='Membros adicionados.')

    @app.delete('/grupos/<int:id>/membros/<int:user>')
    @auth('funcionario')
    def member_remove(id, user):
        with db.begin() as c:
            if not c.execute(delete(membros).where(membros.c.grupo == id, membros.c.api_usuario_id == user)).rowcount:
                raise Erro('Membro não encontrado.', 404)
        return '', 204

    def challenge_data(data):
        points = numero(data, 'pontos')
        if points > 10000:
            raise Erro('O máximo é 10000 pontos.')
        return dict(descricao=texto(data, 'descricao', 5, 1000), categoria=texto(data, 'categoria', 3, 100), pontos=points)

    @app.get('/desafios')
    def challenges_get():
        with db.connect() as c:
            return jsonify([dict(r) for r in c.execute(select(desafios).order_by(desafios.c.id)).mappings()])

    @app.get('/desafios/<int:id>')
    def challenge_get(id):
        with db.connect() as c:
            return jsonify(dict(require_row(c, desafios, id)))

    @app.post('/desafios')
    @auth('funcionario')
    def challenge_create():
        values = challenge_data(corpo())
        with db.begin() as c:
            id = c.execute(desafios.insert().values(**values)).inserted_primary_key[0]
        return jsonify(id=id), 201

    @app.put('/desafios/<int:id>')
    @auth('funcionario')
    def challenge_edit(id):
        data = corpo()
        values = challenge_data(data)
        values['ativo'] = int(booleano(data, 'ativo'))
        with db.begin() as c:
            bloquear(c)
            require_row(c, desafios, id)
            c.execute(update(desafios).where(desafios.c.id == id).values(**values))
        return jsonify(id=id)

    @app.delete('/desafios/<int:id>')
    @auth('funcionario')
    def challenge_delete(id):
        with db.begin() as c:
            bloquear(c)
            require_row(c, desafios, id)
            if c.execute(select(acoes.c.id).where(acoes.c.desafio == id)).first():
                raise Erro('Desafio com ações não pode ser excluído; desative-o.', 409)
            c.execute(delete(desafios).where(desafios.c.id == id))
        return '', 204

    def action_list():
        with db.connect() as c:
            rows = c.execute(select(acoes, contas.c.nome, desafios.c.descricao).join(contas,
                contas.c.id == acoes.c.autor).join(desafios, desafios.c.id == acoes.c.desafio).order_by(acoes.c.id)).mappings()
            result = []
            for row in rows:
                item = dict(row)
                item['criada_em'] = item['criada_em'].isoformat() + 'Z'
                result.append(item)
            return result

    @app.get('/acoes')
    @auth()
    def actions_get():
        return jsonify(action_list())

    @app.post('/acoes')
    @auth('usuario')
    def action_create():
        data = corpo()
        challenge = numero(data, 'desafio_id')
        evidence = texto(data, 'evidencia', 5, 2000)
        with db.begin() as c:
            bloquear(c)
            row = require_row(c, desafios, challenge)
            if not row['ativo']:
                raise Erro('Desafio inativo.')
            id = c.execute(acoes.insert().values(autor=g.session['usuario'], desafio=challenge,
                evidencia=evidence, pontos=row['pontos'], criada_em=agora())).inserted_primary_key[0]
        return jsonify(id=id), 201

    @app.post('/acoes/<int:id>/validacoes')
    @auth('usuario')
    def vote(id):
        with db.begin() as c:
            bloquear(c)
            row = require_row(c, acoes, id)
            if row['status'] != 'pendente' or row['autor'] == g.session['usuario']:
                raise Erro('Você só pode validar ações pendentes de outras pessoas.')
            c.execute(votos.insert().values(acao=id, usuario=g.session['usuario']))
            count = c.execute(select(func.count()).select_from(votos).where(votos.c.acao == id)).scalar_one()
            if count >= 2:
                c.execute(update(acoes).where(acoes.c.id == id).values(status='aprovada', parecer='Duas validações da comunidade'))
        return jsonify(mensagem='Validação registrada.')

    @app.patch('/acoes/<int:id>/analise')
    @auth('funcionario')
    def analyze(id):
        data = corpo()
        approve = booleano(data, 'aprovar')
        opinion = texto(data, 'parecer', 5, 2000)
        with db.begin() as c:
            bloquear(c)
            row = require_row(c, acoes, id)
            if row['status'] != 'pendente':
                raise Erro('Ação já analisada.', 409)
            c.execute(update(acoes).where(acoes.c.id == id).values(status='aprovada' if approve else 'rejeitada', parecer=opinion))
        return jsonify(mensagem='Análise registrada.')

    def ranking_list():
        with db.connect() as c:
            result = {r.id: {'id': r.id, 'nome': r.nome, 'pontos': 0} for r in c.execute(select(contas.c.id, contas.c.nome))}
            for r in c.execute(select(acoes.c.autor, acoes.c.pontos).where(acoes.c.status == 'aprovada')):
                result[r.autor]['pontos'] += r.pontos
            for r in c.execute(select(votos.c.usuario).join(acoes, acoes.c.id == votos.c.acao).where(acoes.c.status == 'aprovada')):
                result[r.usuario]['pontos'] += 10
        return sorted(result.values(), key=lambda r: (-r['pontos'], r['nome']))

    @app.get('/ranking')
    def ranking():
        return jsonify(ranking_list())

    @app.get('/exportacoes/<entity>')
    @auth('funcionario')
    def export(entity):
        if entity == 'grupos':
            data = group_list()
        elif entity == 'acoes':
            data = action_list()
        elif entity in {'usuarios', 'desafios'}:
            query = select(contas.c.id, contas.c.nome, contas.c.email, contas.c.api_id) if entity == 'usuarios' else select(desafios)
            with db.connect() as c:
                data = [dict(r) for r in c.execute(query).mappings()]
        else:
            raise Erro('Exportação inválida.', 404)
        response = jsonify(data)
        response.headers['Content-Disposition'] = f'attachment; filename="{entity}.json"'
        return response

    return app

app = create_app()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.getenv('PORT', '8000')), debug=False)
