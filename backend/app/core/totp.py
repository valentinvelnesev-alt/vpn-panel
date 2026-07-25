import pyotp

ISSUER = "VPN Panel"


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, login: str) -> str:
    """URI для QR-кода в Google Authenticator / 1Password и т. п."""
    return pyotp.TOTP(secret).provisioning_uri(name=login, issuer_name=ISSUER)


def verify_totp(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    # valid_window=1 — принимаем соседние окна, чтобы расхождение часов
    # на телефоне и сервере не мешало входу.
    return pyotp.TOTP(secret).verify(code.strip(), valid_window=1)
