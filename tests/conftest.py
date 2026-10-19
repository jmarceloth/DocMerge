"""
Configurações e Fixtures Globais para Testes Pytest
DocMerge - Isolamento total de banco de dados e diretório de timbrado em diretório temporário
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

os.environ["SECRET_KEY"] = "teste-chave-secreta-forte-e-aleatoria-docmerge-1234567890"
os.environ["GOOGLE_CLIENT_ID"] = "mock-docmerge-client-id.apps.googleusercontent.com"
os.environ["GOOGLE_CLIENT_SECRET"] = "mock-docmerge-client-secret-999"
os.environ["BASE_URL"] = "http://testserver"
os.environ["ENVIRONMENT"] = "testing"
os.environ["SESSION_MAX_AGE_HOURS"] = "8"
os.environ["INITIAL_OWNER_EMAIL"] = "jmarceloth@gmail.com"

temp_test_dir = tempfile.mkdtemp(prefix="docmerge_test_data_")
os.environ["DATA_DIR"] = temp_test_dir
os.environ["DATABASE_PATH"] = str(Path(temp_test_dir) / "test_global.db")
os.environ["UPLOADS_DIR"] = str(Path(temp_test_dir) / "uploads")
os.environ["LETTERHEAD_PATH"] = str(Path(temp_test_dir) / "uploads" / "default_letterhead.pdf")
