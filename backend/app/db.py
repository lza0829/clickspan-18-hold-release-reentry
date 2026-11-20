import os
import sqlite3
from pathlib import Path

_DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DEFAULT_FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "fixtures"

# 模块级常量保留给既有引用；新代码请用下面的解析函数（读环境变量在调用时生效）
DATA_DIR = _DEFAULT_DATA_DIR
FIXTURES_DIR = _DEFAULT_FIXTURES_DIR
DB_PATH = DATA_DIR / "clickspan.db"


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", str(_DEFAULT_DATA_DIR)))


def fixtures_dir() -> Path:
    return Path(os.environ.get("FIXTURES_DIR", str(_DEFAULT_FIXTURES_DIR)))


def db_path() -> Path:
    return data_dir() / "clickspan.db"


def connect() -> sqlite3.Connection:
    data_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
