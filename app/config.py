"""應用程式設定：敏感資料（API Key）只放在 .env。"""

import re
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    elsevier_api_key: str = ""
    elsevier_insttoken: str = ""
    data_dir: Path = Path("data")
    host: str = "127.0.0.1"
    port: int = 8000
    env_path: Path = ENV_FILE

    @property
    def elsevier_api_keys(self) -> list[str]:
        """ELSEVIER_API_KEY 可放多組，以逗號分隔；額度用完時依序換下一組。"""
        return parse_api_keys(self.elsevier_api_key)


def parse_api_keys(raw: str) -> list[str]:
    """以逗號、空白或換行分隔多組 API Key，去除重複並保留順序。"""
    return list(dict.fromkeys(k for k in re.split(r"[\s,]+", raw or "") if k))


def save_env(path: Path, api_key: str, insttoken: str) -> None:
    """改寫 .env 中的 API Key 與 insttoken，保留其他設定。"""
    values = {"ELSEVIER_API_KEY": api_key, "ELSEVIER_INSTTOKEN": insttoken}
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in values:
            out.append(f"{key}={values.pop(key)}")
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in values.items()]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
