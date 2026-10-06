"""不含明文的模型鉴权身份；扩展鉴权变化不能复用其他账户的能力证据。"""

import hashlib
import json


def model_credential_identity(config) -> str:
    key = config.apikey or ""
    extension = getattr(config, "runtime_credentials_cipher", None)
    value = json.dumps([key, extension], separators=(",", ":")) if extension else key
    return hashlib.sha256(value.encode()).hexdigest()
