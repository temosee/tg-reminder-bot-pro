import logging

from cryptography.fernet import Fernet, InvalidToken

import config

logger = logging.getLogger(__name__)

_fernet = None

def _clean_key(raw: str) -> str:
    key = raw.strip().strip('"\'')
    # в переменную нередко вставляют строку из .env целиком
    if key.upper().startswith("ENCRYPTION_KEY="):
        key = key.split("=", 1)[1].strip().strip('"\'')
    return key

_key = _clean_key(config.ENCRYPTION_KEY)

if _key:
    try:
        _fernet = Fernet(_key.encode())
    except Exception:
        # падать нельзя: бот перестанет отвечать всем из-за одной переменной
        logger.error(
            "ENCRYPTION_KEY задан неверно — тексты пишутся открыто. "
            "Ожидается ключ Fernet из 44 символов, получено %d",
            len(_key),
        )
else:
    logger.warning("ENCRYPTION_KEY не задан — тексты хранятся в базе открыто")

def encrypt(text):
    if _fernet is None or text is None:
        return text
    return _fernet.encrypt(text.encode()).decode()

def decrypt(value):
    if _fernet is None or value is None:
        return value
    try:
        return _fernet.decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return value  # запись сделана до включения шифрования

def decrypt_field(rows, field: str):
    for row in rows:
        if row.get(field) is not None:
            row[field] = decrypt(row[field])
    return rows
