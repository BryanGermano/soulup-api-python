"""Gera e verifica o ZIP de entrega sem credenciais, bancos ou exportações."""
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


ARQUIVOS = (
    'app.py', 'database.py', 'init_db.py', 'menu.py',
    'test_api.py', 'test_menu.py', 'preparar_entrega.py',
    'requirements.txt', 'render.yaml', '.env.example', '.gitignore',
    'README.md', 'ENTREGA_PYTHON.txt', 'INTEGRACAO_FRONTEND.md',
)


def preparar(pasta=None):
    """Compacta os arquivos necessários e retorna o caminho do ZIP verificado."""
    base = Path(pasta) if pasta is not None else Path(__file__).resolve().parent
    ausentes = [name for name in ARQUIVOS if not (base / name).is_file()]
    if ausentes:
        raise FileNotFoundError('Arquivos obrigatórios ausentes: ' + ', '.join(ausentes))
    destino = base / 'output' / 'Soul-Up-Python-Entrega-4.zip'
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix('.tmp')
    try:
        with ZipFile(temporario, 'w', compression=ZIP_DEFLATED) as arquivo:
            for name in ARQUIVOS:
                arquivo.write(base / name, arcname='soulup-api-python/' + name)
        with ZipFile(temporario) as arquivo:
            esperado = {'soulup-api-python/' + name for name in ARQUIVOS}
            if set(arquivo.namelist()) != esperado or arquivo.testzip() is not None:
                raise ValueError('O pacote não passou na verificação de integridade.')
        temporario.replace(destino)
    finally:
        temporario.unlink(missing_ok=True)
    return destino


if __name__ == '__main__':
    try:
        caminho = preparar()
    except (OSError, ValueError) as erro:
        raise SystemExit(f'Não foi possível preparar a entrega: {erro}') from erro
    print('ZIP verificado:', caminho)
    print('Antes de enviar, confira o vídeo e as pendências em ENTREGA_PYTHON.txt.')
