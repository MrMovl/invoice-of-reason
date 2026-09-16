"""Runtime configuration, read from environment variables and the sender TOML file."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from .pdf import Sender


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    backup_dir: Path
    sender_file: Path
    retention_years: int
    backup_keep: int

    @property
    def db_path(self) -> Path:
        return self.data_dir / "invoices.sqlite3"

    @property
    def archive_dir(self) -> Path:
        return self.data_dir / "archive"


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("INVOICES_DATA_DIR", "data")).resolve()
    return Settings(
        data_dir=data_dir,
        backup_dir=Path(
            os.environ.get("INVOICES_BACKUP_DIR", str(data_dir.parent / "backups"))
        ).resolve(),
        sender_file=Path(
            os.environ.get("INVOICES_SENDER_FILE", "config/sender.toml")
        ).resolve(),
        retention_years=int(os.environ.get("INVOICES_RETENTION_YEARS", "10")),
        backup_keep=int(os.environ.get("INVOICES_BACKUP_KEEP", "30")),
    )


def load_sender(path: Path) -> Sender:
    if not path.is_file():
        raise ConfigError(
            f"Absenderdaten fehlen: {path} existiert nicht "
            "(Vorlage: config/sender.example.toml)."
        )
    with path.open("rb") as f:
        raw = tomllib.load(f).get("sender", {})
    names = [f.name for f in fields(Sender)]
    missing = [n for n in names if not str(raw.get(n, "")).strip()]
    if missing:
        raise ConfigError(f"{path}: fehlende Felder in [sender]: {', '.join(missing)}")
    return Sender(**{n: str(raw[n]).strip() for n in names})
