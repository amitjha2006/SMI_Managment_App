import os
import secrets
import sys
from contextlib import suppress
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _base_dir() -> Path:
    # When packaged with PyInstaller, keep writable data next to the .exe
    # (bundled files live in read-only sys._MEIPASS instead).
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent.parent


def _data_dir() -> Path:
    return _base_dir() / "data"


# Minimum accepted length for a persisted signing secret. Shorter values are
# treated as missing and regenerated, so a truncated file cannot weaken auth.
_MIN_SECRET_LEN = 32


def _persisted_secret() -> str:
    """A per-install signing secret, generated once and reused after that.

    Why this exists: the secret used to be the literal string
    "dev-secret-change-in-production" in source control. Anyone holding a copy
    of the repo could mint a valid token for any username - including admin -
    and the app would accept it, so the login screen was decoration.

    Requirements this has to satisfy:
      * Stable across restarts, or every launch would silently sign everyone
        out and the "remember me" token in localStorage would be worthless.
      * Different on every machine, so a key lifted from one install is
        useless on another.
      * Not in the repo. It lives beside the database, in the data directory
        the installer already has to be able to write.

    A file that cannot be written (read-only install, locked-down share) must
    not stop the app from starting. It falls back to a process-lifetime random
    secret, which is weaker - it logs everyone out on exit - but it is far
    better than a published constant, and the failure is visible in the log
    rather than silent.
    """
    env_secret = os.environ.get("SECRET_KEY", "").strip()
    if env_secret:
        return env_secret

    key_file = _data_dir() / ".secret_key"
    try:
        if key_file.exists():
            existing = key_file.read_text(encoding="utf-8").strip()
            if len(existing) >= _MIN_SECRET_LEN:
                return existing
    except OSError:
        pass

    generated = secrets.token_urlsafe(48)
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(generated, encoding="utf-8")
        # Owner-only where the platform supports it. On Windows this is mostly
        # advisory, but it is still correct to ask and harmless when refused.
        with suppress(OSError):
            key_file.chmod(0o600)
    except OSError as exc:
        print(
            "[security] WARNING: could not persist the signing secret to "
            f"{key_file} ({exc}). Using a temporary secret for this run only; "
            "users will be signed out when the app closes."
        )
    return generated


class Settings(BaseSettings):
    APP_NAME: str = "Shresth Masala Industry Management"
    # Never ship debug on. Nothing currently reads this for FastAPI's own
    # debug flag, but leaving it True invites a future change that does.
    DEBUG: bool = False
    SECRET_KEY: str = _persisted_secret()
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7

    BASE_DIR: Path = _base_dir()
    DATA_DIR: Path = _data_dir()
    DB_PATH: Path = DATA_DIR / "masala_factory.db"
    DATABASE_URL: str = f"sqlite:///{DB_PATH}"

    FRONTEND_DIR: Path = BASE_DIR / "frontend"

    # The packaged app serves its own frontend from the same origin, so CORS
    # only matters for a separate dev server. Wildcard origins are not allowed
    # because credentials are enabled. The port is open on purpose: the
    # desktop app falls back to 8001..8005 when 8000 is taken, and pinning
    # the list to 8000 would make that fallback a CORS failure waiting to
    # happen. Loopback only, so this grants nothing to the network.
    CORS_ORIGINS: str = (
        "http://localhost:8000,http://127.0.0.1:8000,"
        "http://localhost:8001,http://127.0.0.1:8001,"
        "http://localhost:8002,http://127.0.0.1:8002,"
        "http://localhost:8003,http://127.0.0.1:8003,"
        "http://localhost:8004,http://127.0.0.1:8004,"
        "http://localhost:8005,http://127.0.0.1:8005"
    )

    @property
    def cors_origin_list(self) -> list:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    # Pydantic v2 replaced the nested `class Config` with `model_config`.
    # pydantic-settings 2.x reads SettingsConfigDict; the old spelling still
    # worked but raised PydanticDeprecatedSince20 on every import, including
    # in the packaged app, and is removed in Pydantic v3.
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True)


settings = Settings()
settings.DATA_DIR.mkdir(exist_ok=True)
(settings.DATA_DIR / "backups").mkdir(exist_ok=True)
