import logging

from cryptography.fernet import Fernet, InvalidToken

import config

logger = logging.getLogger(__name__)

_fernet = None

if config.ENCRYPTION_KEY:
    try:
        _fernet = Fernet(config.ENCRYPTION_KEY.encode())
    except Exception as e:
        raise RuntimeError(
            "ENCRYPTION_KEY задан неверно. Сгенерировать новый: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        ) from e
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
