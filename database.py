"""Tabelas exclusivas do Python. Nenhuma tabela Java é modificada aqui."""
import os
import oracledb
from sqlalchemy import (create_engine, MetaData, Table, Column, Integer, String,
                        DateTime, ForeignKey, Sequence, UniqueConstraint, CheckConstraint)

metadata = MetaData()

def pk(name):
    return Column('id', Integer, Sequence('su_py_' + name + '_seq'), primary_key=True)

contas = Table('su_py_contas', metadata,
    pk('contas'), Column('nome', String(150), nullable=False),
    Column('email', String(254), nullable=False, unique=True),
    Column('senha', String(256), nullable=False), Column('api_id', Integer, unique=True),
    Column('api_solicitado', Integer))
desafios = Table('su_py_desafios', metadata,
    pk('desafios'), Column('descricao', String(1000), nullable=False),
    Column('categoria', String(100), nullable=False), Column('pontos', Integer, nullable=False),
    Column('ativo', Integer, nullable=False, server_default='1'),
    CheckConstraint('pontos > 0 AND pontos <= 10000'), CheckConstraint('ativo IN (0,1)'))
acoes = Table('su_py_acoes', metadata,
    pk('acoes'), Column('autor', ForeignKey(contas.c.id), nullable=False),
    Column('desafio', ForeignKey(desafios.c.id), nullable=False),
    Column('evidencia', String(2000), nullable=False), Column('pontos', Integer, nullable=False),
    Column('status', String(20), nullable=False, server_default='pendente'),
    Column('parecer', String(2000)), Column('criada_em', DateTime, nullable=False),
    UniqueConstraint('autor', 'desafio'), CheckConstraint("status IN ('pendente','aprovada','rejeitada')"))
votos = Table('su_py_votos', metadata,
    Column('acao', ForeignKey(acoes.c.id), primary_key=True),
    Column('usuario', ForeignKey(contas.c.id), primary_key=True))
grupos = Table('su_py_grupos', metadata,
    pk('grupos'), Column('nome', String(150), nullable=False, unique=True),
    Column('api_id', Integer, unique=True), Column('situacao', String(20), nullable=False, server_default='pendente'))
membros = Table('su_py_membros', metadata,
    Column('grupo', ForeignKey(grupos.c.id, ondelete='CASCADE'), primary_key=True),
    Column('api_usuario_id', Integer, primary_key=True),
    Column('nome', String(150), nullable=False), Column('email', String(254), nullable=False))
sessoes = Table('su_py_sessoes', metadata,
    Column('token_hash', String(64), primary_key=True),
    Column('usuario', ForeignKey(contas.c.id)), Column('perfil', String(20), nullable=False),
    Column('expira', DateTime, nullable=False))
controle = Table('su_py_controle', metadata,
    Column('chave', String(50), primary_key=True), Column('valor', String(200)))
tentativas = Table('su_py_tentativas', metadata,
    Column('chave', String(64), primary_key=True),
    Column('quantidade', Integer, nullable=False), Column('expira', DateTime, nullable=False))

def engine_oracle():
    # SID é passado explicitamente: ORCL na imagem do usuário não é service_name.
    def conectar():
        required = ['ORACLE_HOST', 'ORACLE_USER', 'ORACLE_PASSWORD', 'ORACLE_SID']
        if any(not os.getenv(k) for k in required):
            raise RuntimeError('Configure ORACLE_HOST, ORACLE_USER, ORACLE_PASSWORD e ORACLE_SID no Render.')
        params = oracledb.ConnectParams(host=os.environ['ORACLE_HOST'],
            port=int(os.getenv('ORACLE_PORT', '1521')), sid=os.environ['ORACLE_SID'],
            tcp_connect_timeout=10, retry_count=0)
        conn = oracledb.connect(user=os.environ['ORACLE_USER'],
            password=os.environ['ORACLE_PASSWORD'], params=params)
        conn.call_timeout = 20000
        return conn
    return create_engine('oracle+oracledb://', creator=conectar,
                         pool_size=3, max_overflow=2, pool_pre_ping=True, pool_recycle=300,
                         hide_parameters=True)

def inicializar(engine):
    metadata.create_all(engine)
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError
    try:
        with engine.begin() as c:
            if not c.execute(select(controle).where(controle.c.chave == 'gravacoes')).first():
                c.execute(controle.insert().values(chave='gravacoes', valor='1'))
    except IntegrityError:
        pass  # Outra inicialização concorrente inseriu a linha de coordenação.

def bloquear(c):
    # Compartilhado entre instâncias; serializa decisões e votos, não apenas threads.
    row = c.execute(controle.select().where(controle.c.chave == 'gravacoes').with_for_update()).first()
    if not row:
        raise RuntimeError('Execute init_db.py antes de iniciar a API.')
