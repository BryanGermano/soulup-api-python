"""Cria apenas tabelas SU_PY_* ausentes; nunca apaga registros existentes."""
import os
import re
import sys
from database import engine_oracle, inicializar

if __name__ == '__main__':
    if len(os.getenv('SOULUP_SENHA_FUNCIONARIOS', '')) < 8:
        sys.exit('Configure SOULUP_SENHA_FUNCIONARIOS com pelo menos 8 caracteres.')
    try:
        engine = engine_oracle()
        inicializar(engine)
        engine.dispose()
        print('Oracle conectado. Tabelas SU_PY_* verificadas. Tabelas Java preservadas.')
    except Exception as erro:
        codigo = re.search(r'(?:ORA|DPY)-\d+', str(erro))
        print('Falha ao preparar Oracle. Código: ' + (codigo.group() if codigo else type(erro).__name__))
        print('Confira variáveis, acesso externo à FIAP e permissão CREATE TABLE/CREATE SEQUENCE. Nenhuma senha foi exibida.')
        sys.exit(1)
