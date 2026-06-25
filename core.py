"""
ccswith.core - CC-Switch Python 核心模块
实现路径/目录工具、服务商配置、多 CLI 配置适配（含 Hermes Agent）、备份回滚、导入导出、
Hermes config.yaml 读写、OpenAI 兼容 HTTP 代理。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import ssl
import sys
import traceback
import tempfile
import threading
import time
import urllib.error
import urllib.request
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urljoin

logger = logging.getLogger("ccswith")


# ---------------------------- 路径/目录工具 ----------------------------

def is_temp_directory(path: str | os.PathLike) -> bool:
    """判断给定路径是否落在系统临时目录内。"""
    try:
        t = str(Path(path).resolve())
        tmp = str(Path(tempfile.gettempdir()).resolve())
        if t.lower().startswith(tmp.lower()):
            return True
        lower = t.lower()
        if "appdata" in lower and "local" in lower and (
            ("temp" in lower) or ("_onefile" in lower) or ("nuitka" in lower)
        ):
            return True
    except Exception:
        return False
    return False


def _argv0_exe_path() -> Optional[Path]:
    try:
        if sys.argv and sys.argv[0] and sys.argv[0].lower().endswith(".exe"):
            p = Path(sys.argv[0]).resolve()
            if p.exists():
                return p
        exe = sys.executable
        if exe.lower().endswith(".exe"):
            return Path(exe).resolve()
    except Exception:
        return None
    return None


def _is_nuitka_exe() -> bool:
    try:
        if getattr(sys, "frozen", False):
            return False
        names = [n.lower() for n in sys.modules.keys() if isinstance(n, str)]
        if "nuitka" in names or any("nuitka" in n for n in names):
            return True
    except Exception:
        return False
    return False


def get_program_dir() -> Path:
    """三重判断获取程序所在目录。"""
    try:
        if getattr(sys, "frozen", False):
            return Path(sys.executable).parent.resolve()
        if _is_nuitka_exe():
            p = _argv0_exe_path()
            if p:
                return p.parent.resolve()
        if sys.argv and sys.argv[0]:
            p = Path(sys.argv[0]).resolve()
            if p.exists():
                return p.parent if p.is_dir() else p.parent.resolve()
            return Path(os.getcwd()).resolve()
        return Path(__file__).resolve().parent
    except Exception:
        return Path(os.getcwd()).resolve()


def get_working_dir() -> Path:
    try:
        cwd = Path(os.getcwd()).resolve()
        if not is_temp_directory(cwd):
            return cwd
    except Exception:
        pass
    try:
        docs = Path.home() / "Documents"
        if docs.exists():
            return docs
    except Exception:
        pass
    try:
        dsk = Path.home() / "Desktop"
        if dsk.exists():
            return dsk
    except Exception:
        pass
    return Path.home().resolve()


def get_safe_output_base() -> Path:
    try:
        if getattr(sys, "frozen", False) or _is_nuitka_exe():
            return get_working_dir()
        base = get_program_dir()
        if is_temp_directory(base):
            return get_working_dir()
        return base
    except Exception:
        return get_working_dir()


def get_config_dir() -> Path:
    return get_program_dir() / "config"


def get_backup_dir() -> Path:
    return get_program_dir() / "config_backups"


def ensure_dir(path: Path) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return path


# ---------------------------- 服务商数据模型 ----------------------------

@dataclass
class Provider:
    """AI 服务商配置。"""

    alias: str
    display_name: str
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    api_format: str = "openai"
    enabled: bool = True
    note: str = ""
    created_at: str = ""
    updated_at: str = ""
    rate_limit: int = 0
    deduplicate: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Provider":
        alias = data.get("alias") or data.get("name") or "provider"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return cls(
            alias=alias,
            display_name=data.get("display_name") or data.get("display") or alias,
            base_url=data.get("base_url", ""),
            api_key=data.get("api_key", ""),
            model=data.get("model", ""),
            api_format=data.get("api_format", "openai"),
            enabled=bool(data.get("enabled", True)),
            note=data.get("note", ""),
            created_at=data.get("created_at") or now,
            updated_at=data.get("updated_at") or now,
            rate_limit=int(data.get("rate_limit", 0)),
            deduplicate=bool(data.get("deduplicate", True)),
        )


class ProviderManager:
    """服务商配置增删改查 + 当前激活切换。

    统一配置文件：<config_dir>/config.yaml，结构：
        current_provider: deepseek-chat
        providers:
          deepseek-chat:
            alias: deepseek-chat
            display_name: DeepSeek
            base_url: ...
            ...
        ui_settings: { ... }
        hermes: { proxy_url: ..., virtual_model: ... }
        proxy:  { host: ..., port: ... }

    为了平滑升级，启动时如果发现旧的 providers.json / current_provider.json 存在
    但 config.yaml 不存在，会自动迁移并把旧 JSON 文件备份为 .bak。
    """

    CONFIG_FILE = "config.yaml"
    LEGACY_PROVIDERS_FILE = "providers.json"
    LEGACY_CURRENT_FILE = "current_provider.json"

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = ensure_dir(config_dir or get_config_dir())
        self.config_file = self.config_dir / self.CONFIG_FILE
        self.env_file = self.config_dir / ".env"
        self.providers: Dict[str, Provider] = {}
        self.current_alias: Optional[str] = None
        self.active_models: Dict[str, str] = {}  # alias -> 当前选中的单个模型ID
        self._raw: Dict[str, Any] = {}
        self.load()

    # ----------------------- 底层 yaml 读写 -----------------------
    def _read_config(self) -> Dict[str, Any]:
        if not self.config_file.exists():
            return {}
        try:
            data = yaml_load(self.config_file)
        except Exception as e:
            logger.warning("config.yaml 读取失败: %s", e)
            return {}
        if isinstance(data, dict):
            self._inject_env_keys(data)
            return data
        return {}

    def _write_config(self) -> None:
        try:
            data = deepcopy(self._raw)
            self._strip_api_keys(data)
            yaml_text = yaml_dump(data)
            with open(self.config_file, "w", encoding="utf-8") as f:
                f.write(yaml_text if yaml_text.endswith("\n") else yaml_text + "\n")
        except Exception as e:
            logger.error("保存 config.yaml 失败: %s", e)
            raise

    @staticmethod
    def _strip_api_keys(data: Any) -> None:
        """递归移除 dict 中的 api_key 字段（原地修改）。"""
        if isinstance(data, dict):
            data.pop("api_key", None)
            for v in data.values():
                ProviderManager._strip_api_keys(v)
        elif isinstance(data, list):
            for v in data:
                ProviderManager._strip_api_keys(v)

    # ----------------------- .env 密钥管理 -----------------------
    def _load_env(self) -> Dict[str, str]:
        """加载 config/.env 文件，返回 {KEY: VALUE} 字典。"""
        if not self.env_file.exists():
            return {}
        result: Dict[str, str] = {}
        try:
            with open(self.env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    result[k.strip()] = v.strip()
        except Exception:
            pass
        return result

    def _inject_env_keys(self, data: Dict[str, Any]) -> None:
        """把 .env 中的 API keys 注入到 data（原地修改）。"""
        env = self._load_env()
        if not env:
            return
        providers = data.get("providers")
        if isinstance(providers, dict):
            for alias, p in providers.items():
                if not isinstance(p, dict):
                    continue
                # 尝试精确匹配，再尝试规范化名（大写 + 特殊字符转 _）
                val = env.get(alias) or env.get(re.sub(r'[^a-zA-Z0-9_]', '_', alias).upper())
                if val:
                    p["api_key"] = val
        proxy = data.get("proxy")
        if isinstance(proxy, dict):
            for k in ("PROXY", "PROXY_API_KEY", "PROXY_KEY"):
                if k in env:
                    proxy["api_key"] = env[k]
                    break

    def _save_env_key(self, identifier: str, value: str) -> None:
        """保存/更新一个 key 到 .env 文件。identifier 是别名或 PROXY。"""
        env = self._load_env()
        env[identifier] = value
        lines = [f"{k}={v}\n" for k, v in sorted(env.items())]
        try:
            with open(self.env_file, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception as e:
            logger.warning("保存 .env 失败: %s", e)

    # ----------------------- 迁移 -----------------------
    def _migrate_from_legacy_json(self) -> bool:
        """如果旧的 JSON 文件存在，读入 providers + current，写进 config.yaml，
        再把旧文件重命名为 .bak。返回是否发生了迁移。"""
        providers_json = self.config_dir / self.LEGACY_PROVIDERS_FILE
        current_json = self.config_dir / self.LEGACY_CURRENT_FILE
        if self.config_file.exists():
            return False  # 新格式已存在，不迁移
        if not providers_json.exists() and not current_json.exists():
            return False

        migrated = False
        providers_data: Dict[str, Any] = {}
        current_data: Dict[str, Any] = {}

        if providers_json.exists():
            try:
                with open(providers_json, "r", encoding="utf-8") as f:
                    providers_data = json.load(f)
                if isinstance(providers_data, dict):
                    self._raw["providers"] = providers_data
                    migrated = True
            except Exception as e:
                logger.warning("迁移 providers.json 失败: %s", e)

        if current_json.exists():
            try:
                with open(current_json, "r", encoding="utf-8") as f:
                    current_data = json.load(f)
                if isinstance(current_data, dict) and current_data.get("alias"):
                    self._raw["current_provider"] = current_data["alias"]
                    migrated = True
            except Exception as e:
                logger.warning("迁移 current_provider.json 失败: %s", e)

        if migrated:
            self._write_config()
            # 旧文件备份
            for old in (providers_json, current_json):
                if old.exists():
                    try:
                        backup = old.with_suffix(old.suffix + ".bak")
                        if backup.exists():
                            try:
                                backup.unlink()
                            except Exception:
                                pass
                        old.rename(backup)
                        logger.info("已将旧配置备份为 %s", backup)
                    except Exception as e:
                        logger.warning("旧配置文件重命名备份失败: %s", e)
        return migrated

    # ----------------------- 加载 -----------------------
    def load(self) -> None:
        self.providers.clear()
        self._raw = self._read_config()
        if not self._raw:
            # 没任何配置，尝试从旧 JSON 迁移
            self._migrate_from_legacy_json()
            self._raw = self._read_config()

        providers_section = self._raw.get("providers") if isinstance(self._raw, dict) else None
        if isinstance(providers_section, dict):
            for alias, obj in providers_section.items():
                if not isinstance(obj, dict):
                    continue
                try:
                    self.providers[alias] = Provider.from_dict(dict(obj, alias=alias))
                except Exception as e:
                    logger.warning("跳过损坏的服务商 %s: %s", alias, e)

        cur = None
        if isinstance(self._raw, dict):
            cur = self._raw.get("current_provider")
        if cur and cur in self.providers:
            self.current_alias = cur
        elif self.providers:
            # 找第一个启用的
            for alias, p in self.providers.items():
                if p.enabled:
                    self.current_alias = alias
                    break
            else:
                self.current_alias = next(iter(self.providers))
        else:
            self.current_alias = None

    # ----------------------- 保存 -----------------------
    def save(self) -> None:
        # 构造 providers 分区，同时把 api_key 存到 .env
        providers_section: Dict[str, Any] = {}
        for alias, p in self.providers.items():
            pd = {k: v for k, v in p.to_dict().items() if k != "alias"}
            if pd.get("api_key"):
                self._save_env_key(alias, pd["api_key"])
            providers_section[alias] = pd
        self._raw["providers"] = providers_section
        if self.current_alias:
            self._raw["current_provider"] = self.current_alias
        elif "current_provider" in self._raw:
            del self._raw["current_provider"]
        self._write_config()

    # ----------------------- 当前选中模型管理 -----------------------
    def set_active_model(self, alias: str, model: str) -> None:
        """记录用户在下拉框中选中的模型 ID（单个）。"""
        self.active_models[alias] = model

    def get_active_model(self, alias: str) -> str:
        """获取当前选中的模型 ID；若无记录则从逗号分隔列表中取第一个。"""
        if alias in self.active_models:
            return self.active_models[alias]
        provider = self.providers.get(alias)
        if provider and provider.model:
            parts = [x.strip() for x in provider.model.split(",") if x.strip()]
            if parts:
                return parts[0]
        return ""

    # ----------------------- 服务商增删改查 -----------------------
    def add_or_update(self, provider: Provider) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if provider.alias in self.providers:
            provider.created_at = self.providers[provider.alias].created_at
        else:
            if not provider.created_at:
                provider.created_at = now
        provider.updated_at = now
        if not self.providers:
            self.current_alias = provider.alias
        self.providers[provider.alias] = provider
        self.save()

    def remove(self, alias: str) -> bool:
        if alias not in self.providers:
            return False
        del self.providers[alias]
        if self.current_alias == alias:
            self.current_alias = next(iter(self.providers), None)
        self.save()
        return True

    def toggle_enabled(self, alias: str) -> Optional[bool]:
        if alias not in self.providers:
            return None
        p = self.providers[alias]
        p.enabled = not p.enabled
        self.save()
        return p.enabled

    def list_all(self) -> List[Provider]:
        return sorted(self.providers.values(), key=lambda p: p.alias.lower())

    def get(self, alias: str) -> Optional[Provider]:
        return self.providers.get(alias)

    def set_current(self, alias: str) -> bool:
        if alias in self.providers:
            self.current_alias = alias
            self.save()
            return True
        return False

    def get_current(self) -> Optional[Provider]:
        if self.current_alias:
            return self.providers.get(self.current_alias)
        return None

    # ----------------------- UI 设置读写（同一 config.yaml） -----------------------
    def get_ui_settings(self) -> Dict[str, Any]:
        section = self._raw.get("ui_settings") if isinstance(self._raw, dict) else None
        return dict(section) if isinstance(section, dict) else {}

    def set_ui_settings(self, settings: Dict[str, Any]) -> None:
        self._raw["ui_settings"] = dict(settings)
        self._write_config()

    # ----------------------- Hermes / 代理设置 -----------------------
    def get_hermes_settings(self) -> Dict[str, Any]:
        section = self._raw.get("hermes") if isinstance(self._raw, dict) else None
        return dict(section) if isinstance(section, dict) else {}

    def set_hermes_settings(self, settings: Dict[str, Any]) -> None:
        self._raw["hermes"] = dict(settings)
        self._write_config()

    def get_proxy_settings(self) -> Dict[str, Any]:
        section = self._raw.get("proxy") if isinstance(self._raw, dict) else None
        result = dict(section) if isinstance(section, dict) else {}
        # 从 .env 补回 api_key（config.yaml 不存 key）
        if not result.get("api_key"):
            env = self._load_env()
            for k in ("PROXY", "PROXY_API_KEY", "PROXY_KEY"):
                if k in env:
                    result["api_key"] = env[k]
                    break
        return result

    def set_proxy_settings(self, settings: Dict[str, Any]) -> None:
        settings = dict(settings)
        # 需要保存的 api_key 写入 .env，不再进 config.yaml
        key = settings.pop("api_key", None)
        if key:
            self._save_env_key("PROXY", key)
        self._raw["proxy"] = settings
        self._write_config()

    # ----------------------- 导入/导出 JSON（服务商部分） -----------------------
    def export_json(self, out_path: Path) -> int:
        ensure_dir(out_path.parent)
        data = {a: p.to_dict() for a, p in self.providers.items()}
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return len(self.providers)

    def import_json(self, in_path: Path, merge: bool = True) -> int:
        with open(in_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not merge:
            self.providers.clear()
        count = 0
        items = raw if isinstance(raw, dict) else {}
        for alias, obj in items.items():
            try:
                self.providers[alias] = Provider.from_dict(dict(obj, alias=alias))
                count += 1
            except Exception:
                continue
        if not self.current_alias and self.providers:
            self.current_alias = next(iter(self.providers))
        self.save()
        return count


# ---------------------------- CLI 配置适配 ----------------------------

CLI_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "claude_code": {
        "display": "Claude Code (.env / settings.json)",
        "paths": {
            "env": ["{HOME}/.claude/.env", "{USERPROFILE}/.claude/.env"],
            "json": ["{HOME}/.claude/settings.json", "{USERPROFILE}/.claude/settings.json"],
        },
        "format_rules": {
            "env": {
                "ANTHROPIC_API_KEY": "api_key",
                "ANTHROPIC_BASE_URL": "base_url",
                "ANTHROPIC_MODEL": "model",
            },
            "json": [
                ("apiKey", "api_key"),
                ("baseURL", "base_url"),
                ("model", "model"),
            ],
        },
    },
    "codex": {
        "display": "Codex CLI (config.toml / config.json)",
        "paths": {
            "toml": ["{HOME}/.codex/config.toml", "{USERPROFILE}/.codex/config.toml"],
            "json": ["{HOME}/.codex/config.json", "{USERPROFILE}/.codex/config.json"],
        },
        "format_rules": {
            # Codex 0.116+ 的 key 名（全小写，蛇形）
            "toml": {
                "model_provider": "model_provider",  # 动态选 openai/anthropic/...
                "api_key": "api_key",
                "base_url": "base_url",
                "model": "model",
            },
            "json": [
                ("apiKey", "api_key"),
                ("baseURL", "base_url"),
                ("model", "model"),
            ],
        },
    },
    "gemini": {
        "display": "Gemini CLI (settings.json)",
        "paths": {
            "json": ["{HOME}/.config/gemini/settings.json", "{USERPROFILE}/.config/gemini/settings.json"],
            "env": ["{HOME}/.gemini/.env", "{USERPROFILE}/.gemini/.env"],
        },
        "format_rules": {
            "json": [
                ("apiKey", "api_key"),
                ("baseURL", "base_url"),
                ("model", "model"),
            ],
            "env": {
                "GEMINI_API_KEY": "api_key",
                "GEMINI_BASE_URL": "base_url",
                "GEMINI_MODEL": "model",
            },
        },
    },
    "hermes": {
        "display": "Hermes Agent (config.yaml / .env)",
        "paths": {
            "yaml": ["{HERMES_HOME}/config.yaml", "{USERPROFILE}/AppData/Local/hermes/config.yaml", "{HOME}/.hermes/config.yaml"],
            "env": ["{HERMES_HOME}/.env", "{USERPROFILE}/AppData/Local/hermes/.env", "{HOME}/.hermes/.env"],
        },
    },
}


def _resolve_templates(candidates: List[str]) -> Optional[Path]:
    home = str(Path.home())
    env_home = os.environ.get("HOME") or home
    userprofile = os.environ.get("USERPROFILE") or home
    hermes_home = os.environ.get("HERMES_HOME") or str(Path.home() / "AppData" / "Local" / "hermes")
    for tpl in candidates:
        p = Path(
            tpl.replace("{HOME}", env_home)
            .replace("{USERPROFILE}", userprofile)
            .replace("{HERMES_HOME}", hermes_home)
        )
        return p
    return None


def env_file_read(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    data: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k:
                    data[k] = v
    except Exception:
        pass
    return data


def env_file_write(path: Path, key_values: Dict[str, str]) -> None:
    ensure_dir(path.parent)
    lines: List[str] = []
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
    except Exception:
        lines = []
    existing = {}
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, _ = s.partition("=")
        existing[k.strip()] = i
    for k, v in key_values.items():
        v_str = str(v)
        if k in existing:
            idx = existing[k]
            lines[idx] = f"{k}={v_str}\n"
        else:
            lines.append(f"{k}={v_str}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def json_file_merge(path: Path, rules: List[Tuple[str, str]], values: Dict[str, str]) -> None:
    ensure_dir(path.parent)
    data: Dict[str, Any] = {}
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                read = json.load(f)
            if isinstance(read, dict):
                data = read
    except Exception:
        data = {}
    for key, backend_key in rules:
        v = values.get(backend_key)
        if v is not None and str(v):
            data[key] = str(v)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _toml_escape_value(val: str) -> str:
    r"""把普通字符串转成 TOML 安全的 quoted value。

    TOML 双引号字符串里 \ 是转义前缀，未识别的转义（如 \C \U 普通字符）会
    导致解析错误。所以这里一律把 \ 写成 \\，" 写成 \"，其他字符不动。
    """
    s = str(val)
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    return '"' + s + '"'


def toml_file_merge(path: Path, rules: Dict[str, str], values: Dict[str, str]) -> None:
    r"""极简 TOML merge：只更新/追加指定 key，其他原封不动保留。"""
    ensure_dir(path.parent)
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            orig_lines = f.read().splitlines()
    except Exception:
        orig_lines = []

    # 1. 扫一遍：已存在的 key 记录行号，不在文件里的 key 单独收集
    existing: Dict[str, Tuple[int, str]] = {}  # key -> (line_idx, 原行)
    for idx, line in enumerate(orig_lines):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("["):
            continue
        if "=" not in s:
            continue
        k, _, _ = s.partition("=")
        k = k.strip()
        if k and k not in existing:
            existing[k] = (idx, line)

    # 2. 按 rules 覆盖已有 / 追加新的
    add_keys: Dict[str, str] = {}
    for backend_key, frontend_key in rules.items():
        v = values.get(frontend_key)
        if v is None or not str(v):
            continue
        new_line = f"{backend_key} = {_toml_escape_value(str(v))}"
        if backend_key in existing:
            idx, _old = existing[backend_key]
            orig_lines[idx] = new_line
        else:
            add_keys[backend_key] = new_line

    # 3. 新 key 追加到末尾
    if add_keys:
        if orig_lines and orig_lines[-1].strip() != "":
            orig_lines.append("")
        for k, nl in add_keys.items():
            orig_lines.append(nl)

    # 4. 写回
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(orig_lines))
        if orig_lines:
            f.write("\n")


# ---------------------------- YAML 最小解析/生成（供 Hermes 配置） ----------------------------

def _yaml_scalar(s: str) -> Any:
    s = s.rstrip()
    if not s:
        return ""
    # 行尾注释
    if " #" in s and not (s.count('"') or s.count("'")):
        s = s.split(" #", 1)[0].rstrip()
    if not s:
        return ""
    if s.startswith("!") or s.startswith("*") or s.startswith("&"):
        s = s.split(" ", 1)[-1] if " " in s else ""
    if not s:
        return ""
    if s.startswith("'") and s.endswith("'"):
        return s[1:-1]
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1].replace('\\"', '"')
    if s.lower() == "true":
        return True
    if s.lower() == "false":
        return False
    if s.lower() in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", s):
        try:
            return int(s)
        except Exception:
            pass
    if re.fullmatch(r"-?\d+\.\d+", s):
        try:
            return float(s)
        except Exception:
            pass
    return s


def yaml_load(path: Path) -> Any:
    """极简 YAML 解析器：仅支持 map（键:值）和 list（- 项）、嵌套缩进。
    足够处理 Hermes config.yaml 的常见结构。"""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return {}

    def _strip_comment(line: str) -> str:
        # 去掉行尾 # 注释，但避开引号内的
        in_dq = False
        in_sq = False
        for i, ch in enumerate(line):
            if ch == '"' and not in_sq:
                in_dq = not in_dq
            elif ch == "'" and not in_dq:
                in_sq = not in_sq
            elif ch == "#" and not in_dq and not in_sq:
                return line[:i].rstrip()
        return line.rstrip()

    lines = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw.rstrip())
        if not stripped.strip():
            continue
        if stripped.lstrip().startswith("#"):
            continue
        lines.append(stripped)

    if not lines:
        return {}

    root: Dict[str, Any] = {}
    # 栈项: (indent, container) 其中 container 是 dict 或 list 的可写引用
    # 我们需要一个 wrapper 才能在 list 内部修改最后一项（map）
    stack: List[Tuple[int, Any, Any]] = [(-1, None, root)]

    def _get_container(level: int):
        while len(stack) > 1 and stack[-1][0] >= level:
            stack.pop()
        return stack[-1]

    for raw in lines:
        indent = len(raw) - len(raw.lstrip(" "))
        content = raw.lstrip(" ")

        if content.startswith("- "):
            cur_indent, cur_key, cur_container = _get_container(indent)
            # 必须当前顶层是 dict，取它最后一个 key 对应的 value 应为 list？不对：list 项自身会 push stack
            # 实际上 list 项的父容器可能是 dict 的 value 或是 list 的上层
            if isinstance(cur_container, dict):
                # list 项应该在 dict 的最后一个 key 的 value 上
                if not cur_container:
                    # 空 dict 不应该先出现 - 项；忽略或当为顶层 list
                    pass
                last_key = list(cur_container.keys())[-1] if cur_container else None
                if last_key is None:
                    # 顶层 list：把 root 变成 list
                    continue
                parent_val = cur_container[last_key]
                if not isinstance(parent_val, list):
                    parent_val = []
                    cur_container[last_key] = parent_val
                list_item_scalar = content[2:].strip()
                if ":" in list_item_scalar and not (
                    (list_item_scalar.startswith('"') or list_item_scalar.startswith("'"))
                ):
                    key_part, _, val_part = list_item_scalar.partition(":")
                    k = key_part.strip()
                    v = _yaml_scalar(val_part.strip())
                    new_map: Dict[str, Any] = {}
                    if v != "":
                        new_map[k] = v
                    else:
                        new_map[k] = {}
                    parent_val.append(new_map)
                    stack.append((indent, None, new_map))
                else:
                    parent_val.append(_yaml_scalar(list_item_scalar))
                    stack.append((indent, None, parent_val))
            elif isinstance(cur_container, list):
                list_item_scalar = content[2:].strip()
                if ":" in list_item_scalar and not (
                    (list_item_scalar.startswith('"') or list_item_scalar.startswith("'"))
                ):
                    key_part, _, val_part = list_item_scalar.partition(":")
                    k = key_part.strip()
                    v = _yaml_scalar(val_part.strip())
                    new_map = {}
                    if v != "":
                        new_map[k] = v
                    else:
                        new_map[k] = {}
                    cur_container.append(new_map)
                    stack.append((indent, None, new_map))
                else:
                    cur_container.append(_yaml_scalar(list_item_scalar))
                    stack.append((indent, None, cur_container))
        elif ":" in content:
            # 键值对
            key_part, _, val_part = content.partition(":")
            k = key_part.strip()
            v = val_part.strip()
            if v == "":
                # 空值 -> dict 或 list 占位
                val = {}
            else:
                val = _yaml_scalar(v)
            cur_indent, cur_key, cur_container = _get_container(indent)
            if isinstance(cur_container, dict):
                if isinstance(val, dict):
                    cur_container[k] = {}
                    stack.append((indent, k, cur_container[k]))
                else:
                    cur_container[k] = val
            elif isinstance(cur_container, list):
                # list 中出现 "key: val" 意味着最后一项 map 的新 key？不，应该是 list 的一项开始写 map 的 key
                if cur_container and isinstance(cur_container[-1], dict):
                    if isinstance(val, dict):
                        cur_container[-1][k] = {}
                        stack.append((indent, k, cur_container[-1][k]))
                    else:
                        cur_container[-1][k] = val
                else:
                    new_map = {k: val if not isinstance(val, dict) else {}}
                    cur_container.append(new_map)
                    stack.append((indent, k, new_map.get(k) if isinstance(new_map.get(k), dict) else new_map))
            else:
                pass

    return root


def _yaml_quote_scalar(val: Any) -> str:
    if val is None or val == "":
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    s = str(val)
    if any(ch in s for ch in "{}[],&*?|>!=%@`#") or s.strip() != s or s == "true" or s == "false" or s == "null" or s == "~":
        # 避免破坏：带引号
        return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return s


def yaml_dump(data: Any) -> str:
    """极简 YAML 生成：仅支持 dict / list / 标量。足够 Hermes config。"""
    lines: List[str] = []

    def _emit(obj: Any, indent: int) -> None:
        pad = " " * indent
        if isinstance(obj, dict):
            for k, v in obj.items():
                if isinstance(v, dict):
                    lines.append(f"{pad}{k}:")
                    _emit(v, indent + 2)
                elif isinstance(v, list):
                    lines.append(f"{pad}{k}:")
                    _emit(v, indent + 2)
                else:
                    lines.append(f"{pad}{k}: {_yaml_quote_scalar(v)}")
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, dict):
                    first = True
                    for k, v in item.items():
                        if first:
                            if isinstance(v, dict):
                                lines.append(f"{pad}- {k}:")
                                _emit(v, indent + 4)
                            elif isinstance(v, list):
                                lines.append(f"{pad}- {k}:")
                                _emit(v, indent + 4)
                            else:
                                lines.append(f"{pad}- {k}: {_yaml_quote_scalar(v)}")
                            first = False
                        else:
                            if isinstance(v, dict):
                                lines.append(f"{pad}  {k}:")
                                _emit(v, indent + 4)
                            elif isinstance(v, list):
                                lines.append(f"{pad}  {k}:")
                                _emit(v, indent + 4)
                            else:
                                lines.append(f"{pad}  {k}: {_yaml_quote_scalar(v)}")
                else:
                    lines.append(f"{pad}- {_yaml_quote_scalar(item)}")
        else:
            lines.append(f"{pad}{_yaml_quote_scalar(obj)}")

    _emit(data, 0)
    return "\n".join(lines) + "\n"


# ---------------------------- Hermes 适配 ----------------------------

HERMES_PROVIDER_PREFIX = "custom"  # Hermes 会拼成 custom:xxx
CCSWITCH_PROVIDER_NAME = "ccswitch"  # 我们在 Hermes 里注册的 custom_provider 名
HERMES_VIRTUAL_MODEL = "hermes-virtual"  # 给第三方 agent 调用时暴露的虚拟模型 ID


def hermes_default_home() -> Path:
    p = Path(os.environ.get("HERMES_HOME", str(Path.home() / "AppData" / "Local" / "hermes")))
    # 若默认不存在，仍返回 AppData/Local/hermes；Hermes CLI 会自动创建
    return p


def hermes_config_path() -> Path:
    # 优先 HERMES_HOME，其次 AppData/Local/hermes
    home = hermes_default_home()
    return home / "config.yaml"


def hermes_env_path() -> Path:
    home = hermes_default_home()
    return home / ".env"


def hermes_merge_config(provider: Provider, proxy_base_url: str, virtual_model: str) -> Tuple[Path, str]:
    """把 Provider 写入 Hermes config.yaml。

    同时覆盖顶层 model.{provider,default,base_url,api_key}，
    因为 Hermes 某些版本（或 fallback_providers 存在时）优先读这里，
    只写 custom_providers 会被忽略掉。
    """
    yaml_path = hermes_config_path()
    ensure_dir(yaml_path.parent)
    try:
        data = yaml_load(yaml_path)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    # ===== 1. custom_providers 注册/更新 ccswitch =====
    cps = data.get("custom_providers")
    if not isinstance(cps, list):
        cps = []
        data["custom_providers"] = cps
    cc_entry = None
    for entry in cps:
        if isinstance(entry, dict) and entry.get("name") == CCSWITCH_PROVIDER_NAME:
            cc_entry = entry
            break
    if cc_entry is None:
        cc_entry = {"name": CCSWITCH_PROVIDER_NAME}
        cps.append(cc_entry)

    cc_entry["api_key"] = provider.api_key or "ccswitch-empty"
    cc_entry["base_url"] = proxy_base_url.rstrip("/")
    # Hermes custom_provider 还可以带 model（部分版本会用作默认模型）
    cc_entry["model"] = virtual_model

    # ===== 2. 顶层 model 必须一起改（Hermes 优先读这里） =====
    data["model"] = {
        "provider": f"{HERMES_PROVIDER_PREFIX}:{CCSWITCH_PROVIDER_NAME}",
        "default": virtual_model,
        "base_url": proxy_base_url.rstrip("/"),
        "api_key": provider.api_key or "ccswitch-empty",
        "model": virtual_model,
    }

    # ===== 3. 清理可能引起回退的 fallback_providers =====
    # 有些 Hermes 版本会走 fallback_providers 而忽略 custom_provider，
    # 把它清掉或改成 ccswitch 代理地址
    fp = data.get("fallback_providers")
    if fp is not None:
        # 如果是字符串列表（Hermes 有时会写成 "[{...}]"），保留原结构但把 base_url 全改成代理
        try:
            if isinstance(fp, str):
                parsed = json.loads(fp.replace("`", "")) if fp.strip().startswith("[") else None
                if isinstance(parsed, list):
                    for item in parsed:
                        if isinstance(item, dict):
                            item["base_url"] = proxy_base_url.rstrip("/")
                            item["api_key"] = provider.api_key or "ccswitch-empty"
                            item["model"] = virtual_model
                            item["provider"] = f"{HERMES_PROVIDER_PREFIX}:{CCSWITCH_PROVIDER_NAME}"
                    data["fallback_providers"] = json.dumps(parsed, ensure_ascii=False)
                # 不是合法 JSON 的就直接清掉
                else:
                    data.pop("fallback_providers", None)
            elif isinstance(fp, list):
                for item in fp:
                    if isinstance(item, dict):
                        item["base_url"] = proxy_base_url.rstrip("/")
                        item["api_key"] = provider.api_key or "ccswitch-empty"
                        item["model"] = virtual_model
                        item["provider"] = f"{HERMES_PROVIDER_PREFIX}:{CCSWITCH_PROVIDER_NAME}"
        except Exception:
            # 兜底：干脆删掉 fallback_providers，强制只用我们的 ccswitch
            data.pop("fallback_providers", None)

    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(yaml_dump(data))

    # ===== 4. 写 .env =====
    env_path = hermes_env_path()
    ensure_dir(env_path.parent)
    env_kv = {
        "OPENAI_API_KEY": provider.api_key or "ccswitch-empty",
        "OPENAI_BASE_URL": proxy_base_url.rstrip("/"),
        "CCSWITCH_VIRTUAL_MODEL": virtual_model,
        "CCSWITCH_PROXY_URL": proxy_base_url.rstrip("/"),
        "DEEPSEEK_API_KEY": provider.api_key or "ccswitch-empty",
        "DEEPSEEK_BASE_URL": proxy_base_url.rstrip("/"),
        "ANTHROPIC_API_KEY": provider.api_key or "ccswitch-empty",
        "ANTHROPIC_BASE_URL": proxy_base_url.rstrip("/"),
    }
    env_file_write(env_path, {k: v for k, v in env_kv.items() if v})

    msg = (
        f"Hermes 已配置：provider={HERMES_PROVIDER_PREFIX}:{CCSWITCH_PROVIDER_NAME} "
        f"model.default={virtual_model} base_url={proxy_base_url.rstrip('/')}，"
        f"真实服务商 {provider.alias} ({provider.base_url})"
    )
    return yaml_path, msg


def _pick_codex_provider(provider: Provider, use_proxy: bool = False, proxy_url: str = "") -> str:
    """把 Provider 的 api_format 映射成 Codex 的 model_provider。

    Codex 0.116+ 的 model_provider 支持：codex(官方)/openai/anthropic/ollama/google/...
    走我们代理时统一走 openai（OpenAI 兼容）。
    """
    if use_proxy:
        return "openai"
    fmt = (provider.api_format or "openai").lower()
    # 已知常见服务商标识 → Codex 对应 provider
    if fmt == "openai":
        return "openai"
    if fmt == "anthropic":
        return "anthropic"
    if fmt in ("deepseek", "moonshot", "zhipu", "qwen", "dashscope",
               "openrouter", "nvidia", "siliconflow", "together",
               "compat", "custom", "azure"):
        return "openai"  # 都走 OpenAI 兼容
    # Gemini/Google — 看 base_url
    if "generativelanguage.googleapis.com" in (provider.base_url or ""):
        return "google"
    return "openai"


def apply_provider_to_cli(
    provider: Provider,
    cli_key: str,
    *,
    use_proxy: bool = False,
    proxy_base_url: str = "",
    virtual_model: str = "",
) -> List[Tuple[Path, bool, str]]:
    """把 Provider 写入指定 CLI 工具配置（兼容 Hermes）。

    参数：
        provider: 当前激活服务商
        cli_key: claude_code / codex / gemini / hermes
        use_proxy: 是否让 CLI 走本地代理
        proxy_base_url: 代理地址（use_proxy=True 时生效）
        virtual_model: 走代理时给 CLI 看的虚拟模型 ID
    """
    template = CLI_TEMPLATES.get(cli_key)
    if not template:
        raise ValueError(f"未知 CLI: {cli_key}")

    # 走代理时：base_url 用代理地址，model 用虚拟模型，api_key 用代理密码（可空）
    if use_proxy:
        model_for_cli = virtual_model or "hermes-virtual"
    else:
        model_for_cli = provider.model

    # 给 Codex 单独算 model_provider
    if cli_key == "codex":
        values = {
            "api_key": provider.api_key if not use_proxy else (provider.api_key or "ccswitch-empty"),
            "model": model_for_cli,
            "model_provider": _pick_codex_provider(provider, use_proxy=use_proxy, proxy_url=proxy_base_url),
            "base_url": (proxy_base_url if use_proxy else provider.base_url),
        }
    else:
        values = {
            "api_key": provider.api_key,
            "base_url": (proxy_base_url if use_proxy else provider.base_url),
            "model": model_for_cli,
        }
    results: List[Tuple[Path, bool, str]] = []
    # Hermes 的路径由 Hermes 统一通过专用函数配置，这里不直接写 yaml/env，
    # 调用方如果没启动代理也能写基础的 .env（保证 Hermes 至少能认出密钥）。
    if cli_key == "hermes":
        # 基础写入 .env 的 OPENAI/DEEPSEEK/ANTHROPIC 密钥
        candidates = template["paths"].get("env", [])
        path = _resolve_templates(candidates)
        if path:
            try:
                env_kv = {
                    "OPENAI_API_KEY": provider.api_key,
                    "OPENAI_BASE_URL": provider.base_url.rstrip("/"),
                    "DEEPSEEK_API_KEY": provider.api_key,
                    "DEEPSEEK_BASE_URL": provider.base_url.rstrip("/"),
                    "ANTHROPIC_API_KEY": provider.api_key,
                    "ANTHROPIC_BASE_URL": provider.base_url.rstrip("/"),
                }
                env_file_write(path, {k: v for k, v in env_kv.items() if v})
                results.append((path, True, "已写入 Hermes .env"))
            except Exception as e:
                results.append((path, False, f"写入 Hermes .env 失败: {e}"))
        # 基础写入 config.yaml 的 model.provider / custom_providers 占位
        yaml_path = hermes_config_path()
        try:
            data = yaml_load(yaml_path)
            if not isinstance(data, dict):
                data = {}
            data["model"] = data.get("model") if isinstance(data.get("model"), dict) else {}
            data["model"]["default"] = provider.model or "gpt-4o"
            data["model"]["provider"] = provider.api_format or "openai"
            cps = data.get("custom_providers")
            if not isinstance(cps, list):
                cps = []
            existing = False
            for entry in cps:
                if isinstance(entry, dict) and entry.get("name") == provider.alias:
                    entry["api_key"] = provider.api_key
                    entry["base_url"] = provider.base_url.rstrip("/")
                    entry["name"] = provider.alias
                    existing = True
                    break
            if not existing and provider.base_url:
                cps.append({
                    "name": provider.alias,
                    "api_key": provider.api_key,
                    "base_url": provider.base_url.rstrip("/"),
                })
            data["custom_providers"] = cps
            ensure_dir(yaml_path.parent)
            with open(yaml_path, "w", encoding="utf-8") as f:
                f.write(yaml_dump(data))
            results.append((yaml_path, True, "已写入 Hermes config.yaml"))
        except Exception as e:
            results.append((yaml_path, False, f"写入 Hermes config.yaml 失败: {e}"))
        return results

    for fmt, candidates in template.get("paths", {}).items():
        path = _resolve_templates(candidates)
        if not path:
            continue
        try:
            if fmt == "env":
                rules = template["format_rules"].get("env", {})
                kv = {env_k: values.get(bk, "") for env_k, bk in rules.items()}
                env_file_write(path, {k: v for k, v in kv.items() if v})
                results.append((path, True, f"已写入 env: {path}"))
            elif fmt == "json":
                rules = template["format_rules"].get("json", [])
                json_file_merge(path, rules, values)
                results.append((path, True, f"已写入 json: {path}"))
            elif fmt == "toml":
                rules = template["format_rules"].get("toml", {})
                toml_file_merge(path, rules, values)
                results.append((path, True, f"已写入 toml: {path}"))
            else:
                results.append((path, False, f"不支持的格式: {fmt}"))
        except Exception as e:
            results.append((path, False, f"写入失败 {path}: {e}"))
    return results


def apply_provider_to_all_clis(provider: Provider) -> Dict[str, List[Tuple[Path, bool, str]]]:
    out: Dict[str, List[Tuple[Path, bool, str]]] = {}
    for cli_key in CLI_TEMPLATES.keys():
        try:
            out[cli_key] = apply_provider_to_cli(provider, cli_key)
        except Exception as e:
            out[cli_key] = [(Path(""), False, f"错误: {e}")]
    return out


# ---------------------------- 备份/回滚 ----------------------------

def _ts() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def backup_cli_configs(backup_dir: Optional[Path] = None) -> Path:
    bk = ensure_dir(backup_dir or get_backup_dir()) / _ts()
    for cli_key, tmpl in CLI_TEMPLATES.items():
        for fmt, candidates in tmpl.get("paths", {}).items():
            src = _resolve_templates(candidates)
            if not src or not src.exists():
                continue
            rel = Path(cli_key) / fmt / src.name
            dst = bk / rel
            ensure_dir(dst.parent)
            try:
                shutil.copy2(src, dst)
            except Exception as e:
                logger.warning("备份 %s 失败: %s", src, e)
    mgr = ProviderManager()
    p = bk / "providers.json"
    ensure_dir(p.parent)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump({a: pv.to_dict() for a, pv in mgr.providers.items()}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("备份 providers 失败: %s", e)
    return bk


def list_backups(backup_dir: Optional[Path] = None) -> List[Path]:
    root = backup_dir or get_backup_dir()
    if not root.exists():
        return []
    items = [p for p in root.iterdir() if p.is_dir()]
    items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return items


def rollback_from_backup(backup_path: Path) -> Dict[str, int]:
    if not backup_path.exists():
        raise FileNotFoundError(str(backup_path))
    restored = {"files": 0, "providers": 0}
    p = backup_path / "providers.json"
    if p.exists():
        try:
            mgr = ProviderManager()
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            mgr.providers.clear()
            for alias, obj in (data if isinstance(data, dict) else {}).items():
                try:
                    mgr.providers[alias] = Provider.from_dict(dict(obj, alias=alias))
                except Exception:
                    continue
            mgr.save()
            restored["providers"] = 1
        except Exception as e:
            logger.warning("回滚 providers 失败: %s", e)
    for cli_key, tmpl in CLI_TEMPLATES.items():
        for fmt, candidates in tmpl.get("paths", {}).items():
            rel = Path(cli_key) / fmt
            src = backup_path / rel
            if not src.exists():
                for alt in backup_path.rglob(f"*"):
                    if alt.is_file() and alt.parent.name == fmt and alt.parent.parent.name == cli_key:
                        src = alt
                        break
            if not src.exists():
                continue
            dst = _resolve_templates(candidates)
            if not dst:
                continue
            ensure_dir(dst.parent)
            try:
                shutil.copy2(src, dst)
                restored["files"] += 1
            except Exception as e:
                logger.warning("回滚 %s 失败: %s", src, e)
    return restored


# ---------------------------- API 格式工具 ----------------------------

def normalize_base_url(base_url: str) -> str:
    u = (base_url or "").strip()
    if not u:
        return ""
    return u.rstrip("/")


# ---------------------------- OpenAI 兼容 HTTP 代理 ----------------------------

@dataclass
class TokenUsage:
    """Token 用量统计，支持日/月自动清零。

    - 当日用量在每日零点清零
    - 当月用量在每月1日零点清零
    """
    daily_prompt: int = 0
    daily_completion: int = 0
    daily_total: int = 0
    monthly_prompt: int = 0
    monthly_completion: int = 0
    monthly_total: int = 0
    last_date: str = ""
    last_month: str = ""

    def record(self, prompt: int, completion: int) -> None:
        """记录一次 API 调用的 token 用量，自动处理日/月切换。"""
        today = datetime.now().strftime("%Y-%m-%d")
        this_month = datetime.now().strftime("%Y-%m")

        # 检测日切换
        if self.last_date and self.last_date != today:
            self.daily_prompt = 0
            self.daily_completion = 0
            self.daily_total = 0

        # 检测月切换
        if self.last_month and self.last_month != this_month:
            self.monthly_prompt = 0
            self.monthly_completion = 0
            self.monthly_total = 0

        total = prompt + completion
        self.daily_prompt += prompt
        self.daily_completion += completion
        self.daily_total += total
        self.monthly_prompt += prompt
        self.monthly_completion += completion
        self.monthly_total += total
        self.last_date = today
        self.last_month = this_month

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TokenUsage":
        return cls(
            daily_prompt=data.get("daily_prompt", 0),
            daily_completion=data.get("daily_completion", 0),
            daily_total=data.get("daily_total", 0),
            monthly_prompt=data.get("monthly_prompt", 0),
            monthly_completion=data.get("monthly_completion", 0),
            monthly_total=data.get("monthly_total", 0),
            last_date=data.get("last_date", ""),
            last_month=data.get("last_month", ""),
        )


class ProxyState:
    """代理全局状态。

    代理启动时会读取 ProviderManager，按"当前激活服务商"转发。
    通过 set_provider() 可在运行中切换（无需重启代理）。
    支持通过 logger_cb 把运行日志回传到 GUI。
    """

    def __init__(self, manager: ProviderManager):
        self.manager = manager
        self._lock = threading.Lock()
        self.virtual_model = HERMES_VIRTUAL_MODEL
        self.proxy_api_key: Optional[str] = None
        self.logger_cb: Optional[Callable[[str, str], None]] = None
        self.static_models = [
            {"id": self.virtual_model, "object": "model", "owned_by": "ccswitch"},
            {"id": "hermes-virtual", "object": "model", "owned_by": "ccswitch"},
            {"id": "deepseek-chat", "object": "model", "owned_by": "deepseek"},
            {"id": "deepseek-reasoner", "object": "model", "owned_by": "deepseek"},
            {"id": "gpt-4o", "object": "model", "owned_by": "openai"},
            {"id": "gpt-4o-mini", "object": "model", "owned_by": "openai"},
            {"id": "claude-opus-4", "object": "model", "owned_by": "anthropic"},
            {"id": "claude-sonnet-4", "object": "model", "owned_by": "anthropic"},
        ]
        self.token_usage = TokenUsage()
        self._token_file = self.manager.config_dir / "token_usage.json"
        self._load_token_usage()
        self._rate_windows: Dict[str, List[float]] = {}

    # ---------- token 用量 ----------

    def _load_token_usage(self) -> None:
        """从文件加载持久化的 token 用量。"""
        try:
            if self._token_file.exists():
                raw = json.loads(self._token_file.read_text(encoding="utf-8"))
                self.token_usage = TokenUsage.from_dict(raw)
        except Exception as e:
            logger.warning("token_usage 加载失败: %s", e)

    def _save_token_usage(self) -> None:
        """持久化 token 用量到文件。"""
        try:
            self._token_file.write_text(
                json.dumps(self.token_usage.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("token_usage 保存失败: %s", e)

    def check_rate_limit(self, alias: str, limit: int) -> Optional[float]:
        """检查速率限制，返回建议的等待秒数。limit=0 时不限制。"""
        if limit <= 0:
            return None
        now = time.time()
        with self._lock:
            timestamps = self._rate_windows.setdefault(alias, [])
            cutoff = now - 60.0
            timestamps[:] = [t for t in timestamps if t > cutoff]
            if len(timestamps) >= limit:
                wait = timestamps[0] + 60.0 - now
                return max(wait, 0.1)
            timestamps.append(now)
            return None

    def record_tokens(self, prompt_tokens: int, completion_tokens: int) -> None:
        """记录一次 API 调用的 token 用量并持久化。"""
        with self._lock:
            self.token_usage.record(prompt_tokens, completion_tokens)
            self._save_token_usage()

    def get_token_summary(self) -> str:
        """获取当日/当月 token 用量摘要文本。"""
        with self._lock:
            t = self.token_usage
            return (
                f"📊 当日: ↑{t.daily_prompt} ↓{t.daily_completion} ∑{t.daily_total}  |  "
                f"当月: ↑{t.monthly_prompt} ↓{t.monthly_completion} ∑{t.monthly_total}"
            )

    # ---------- 原方法 ----------

    def current(self) -> Optional[Provider]:
        with self._lock:
            return self.manager.get_current()

    def switch(self, alias: str) -> bool:
        with self._lock:
            return self.manager.set_current(alias)

    def set_virtual_model(self, name: str) -> None:
        with self._lock:
            self.virtual_model = name

    def emit(self, icon: str, msg: str) -> None:
        """把日志推到外部（GUI），同时写标准 logger。"""
        logger.info("PROXY %s %s", icon, msg)
        cb = self.logger_cb
        if cb is not None:
            try:
                cb(icon, msg)
            except Exception:
                pass


class ProxyHandler(BaseHTTPRequestHandler):
    """最小 OpenAI 兼容代理。

    支持：
        POST /v1/chat/completions
        POST /v1/responses
        GET  /v1/models
        GET  /healthz
    """

    server_version = "CCSwitchProxy/1.0"

    state: Optional[ProxyState] = None

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401
        logger.info("PROXY %s %s", self.command, self.path)

    def _send_json(self, code: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
            pass

    def _read_body(self) -> bytes:
        n = int(self.headers.get("Content-Length", "0") or 0)
        if n <= 0:
            return b""
        return self.rfile.read(n)

    def _dispatch(self, method: str, path: str) -> None:
        # 规范化：去掉尾部斜杠，保留前导 /
        p = (path or "").strip()
        if p.endswith("/") and len(p) > 1:
            p = p[:-1]
        # 兼容客户端 base_url 尾部带 /v1 再拼 /v1/xxx 的情况（如 /v1/v1/messages）
        if p.startswith("/v1/v1"):
            p = "/v1" + p[6:]  # /v1/v1/xxx -> /v1/xxx

        if method == "GET":
            if p in ("/v1/models", "/models"):
                self._handle_models()
                return
            if p in ("/healthz", "/health", "/health_check"):
                self._send_json(200, {"ok": True, "service": "ccswitch-proxy"})
                return
            if p == "/v1/config":
                self._handle_config()
                return
            # 视频状态轮询 GET /v1/videos/{id}
            if p.startswith("/v1/videos/") or p.startswith("/videos/"):
                self._handle_forward_get(p.lstrip("/v1").lstrip("/"))
                return
            self._send_json(404, {"error": {"message": f"not found: {path}", "type": "not_found"}})
            return

        if method != "POST":
            self._send_json(405, {"error": {"message": "method not allowed", "type": "method"}})
            return

        # OpenAI 风格
        if p in ("/v1/chat/completions", "/chat/completions") or p.startswith("/v1/chat/completions?"):
            self._handle_chat_completions()
            return

        # OpenAI 新版 Responses API → 内部转 chat.completions（绝大多数上游不支持 /v1/responses）
        if p in ("/v1/responses", "/responses") or p.startswith("/v1/responses?"):
            self._handle_responses()
            return

        # Anthropic 风格：/v1/messages → 内部转 chat.completions
        if p in ("/v1/messages", "/messages") or p.startswith("/v1/messages?"):
            self._handle_anthropic_messages_as_chat()
            return

        # 兜底：按 path 前缀尝试 chat.completions
        if "chat/completions" in p:
            self._handle_chat_completions()
            return

        # 图片/视频生成端点（直接转发，不改写 payload）
        if p in ("/v1/images/generations", "/images/generations"):
            self._handle_forward("images/generations")
            return
        if p in ("/v1/video/generations", "/video/generations", "/v1/videos", "/videos"):
            self._handle_forward("videos")
            return

        self._send_json(404, {"error": {"message": f"not found: {path}", "type": "not_found"}})

    def do_GET(self) -> None:  # noqa: N802
        if not self._check_auth():
            return
        try:
            self._dispatch("GET", self.path.split("?")[0])
        except Exception as e:
            self._safe_error(e)

    def do_POST(self) -> None:  # noqa: N802
        if not self._check_auth():
            return
        try:
            self._dispatch("POST", self.path.split("?")[0])
        except Exception as e:
            self._safe_error(e)

    def _safe_error(self, e: Exception) -> None:
        """安全的 500 错误响应，确保不会二次异常。"""
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        print(f"[FATAL] 请求处理异常:\n{tb}", file=sys.__stderr__, flush=True)
        try:
            if self.state is not None:
                self.state.emit("❌", f"请求处理异常:\n{tb}")
        except Exception:
            pass
        try:
            self._send_json(500, {"error": {"message": f"internal error: {type(e).__name__}: {e}", "detail": tb[-500:]}})
        except Exception:
            try:
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"internal error","type":"internal_error"}}')
            except Exception:
                pass

    def do_OPTIONS(self) -> None:  # noqa: N802
        try:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            self.end_headers()
        except Exception:
            pass

    def do_HEAD(self) -> None:  # noqa: N802
        self._safe_error(Exception("method HEAD not supported"))

    def do_DELETE(self) -> None:  # noqa: N802
        self._safe_error(Exception("method DELETE not supported"))

    def _check_auth(self) -> bool:
        """可选的代理 API Key 校验：代理 key 为空时不校验；不为空时必须匹配。"""
        try:
            proxy_key = None
            if self.state is not None:
                proxy_key = getattr(self.state, "proxy_api_key", None)
            if not proxy_key:
                return True
            auth = self.headers.get("Authorization", "")
            token = ""
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
            else:
                token = auth.strip()
            x_api_key = self.headers.get("x-api-key", "").strip()
            if token and token == proxy_key:
                return True
            if x_api_key and x_api_key == proxy_key:
                return True
            self._send_json(401, {
                "error": {
                    "message": "Unauthorized: missing or invalid proxy API key",
                    "type": "auth_error",
                }
            })
            return False
        except Exception:
            return True

    # ------------------ 具体处理 ------------------
    def _handle_models(self) -> None:
        assert self.state is not None
        extra = []
        cur = self.state.current()
        if cur and cur.model:
            extra.append({"id": cur.model, "object": "model", "owned_by": cur.alias})
        seen = {m["id"] for m in self.state.static_models}
        for e in extra:
            if e["id"] not in seen:
                self.state.static_models.append(e)
                seen.add(e["id"])
        self._send_json(200, {"object": "list", "data": list(self.state.static_models)})

    def _handle_config(self) -> None:
        assert self.state is not None
        cur = self.state.current()
        self._send_json(
            200,
            {
                "virtual_model": self.state.virtual_model,
                "provider": cur.to_dict() if cur else None,
            },
        )

    def _upstream_endpoint(self, provider: Provider, action: str) -> str:
        """拼上游完整 URL。

        处理用户填写 base_url 带 /v1、/v3、/v4、/v5 等版本前缀的情况，
        避免重复拼接（例如智谱 base_url=https://open.bigmodel.cn/api/paas/v4/
        再拼 /v1/chat/completions 就变成 /v4/v1/chat/completions 404）。

        新逻辑：
          1. 把 base 拆分为「版本前缀」和「额外路径」两部分
          2. 若额外路径与 action 重复，去掉额外路径
          3. 再拼 action
        """
        base = normalize_base_url(provider.base_url) or ""
        base = base.rstrip("/")
        action_clean = action.lstrip("/")
        version_prefixes = {"/v1", "/v3", "/v4", "/v5"}

        # 找到版本前缀在 base 中的位置
        vp_pos = -1
        for vp in version_prefixes:
            pos = base.find(vp)
            if pos != -1:
                vp_pos = pos
                break

        if vp_pos == -1:
            # 不含版本前缀 → 补上 /v1/action
            return base + "/v1/" + action_clean

        # 版本前缀长度均为 3（/v1、/v3、/v4、/v5）
        PREFIX_LEN = 3
        base_root = base[:vp_pos + PREFIX_LEN]
        extra = base[vp_pos + PREFIX_LEN:].strip("/")

        # 若额外路径已经是 action 的一部分，跳过
        # 例如 base=.../v1/images/generations, action=images/generations → 直接用 base
        if extra and extra == action_clean:
            return base_root + "/" + extra

        # 若额外路径包含在 action 中（如 extra=images/generations, action=chat/completions）
        # 说明 base_url 填了具体端点 → 剥离 extra
        if extra:
            return base_root + "/" + action_clean

        return base_root + "/" + action_clean

    def _rewrite_payload_for_provider(self, provider: Provider, action: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """虚拟模型 -> 真实模型映射。

        规则：
        - 若 payload 中的 model 字段是我们的 virtual_model / hermes-virtual / virtual-model /
          provider.alias / provider.display_name，替换为 provider.model（真实模型）。
        - 其他字段原样透传。
        - 若调用方没给 model，补上 provider.model。
        """
        p = dict(payload)
        # 所有可能作为“虚拟模型 ID”出现的别名
        virtual_aliases = {
            "virtual-model",
            "virtual_model",
            "hermes-virtual",
            "hermes_virtual",
            "virtual",
        }
        try:
            if self.state and getattr(self.state, "virtual_model", None):
                virtual_aliases.add(str(self.state.virtual_model))
        except Exception:
            pass
        try:
            if HERMES_VIRTUAL_MODEL:
                virtual_aliases.add(str(HERMES_VIRTUAL_MODEL))
        except Exception:
            pass
        # 取当前选中模型（用户在下拉框中选择的单个 ID），
        # 若无选中则从逗号分隔列表中取第一个。
        def _effective_model() -> str:
            if self.state and self.state.manager:
                active = self.state.manager.get_active_model(provider.alias)
                if active:
                    return active
            # fallback：逗号列表的第一个
            parts = [x.strip() for x in (provider.model or "").split(",") if x.strip()]
            return parts[0] if parts else (provider.model or "")

        eff = _effective_model()
        # 只要有有效模型，始终改写（不管客户端发的是什么）
        if eff:
            p["model"] = eff
        # 若调用方没给 model 且没有有效模型，补虚拟别名
        elif not p.get("model"):
            p["model"] = "virtual-model"

        # ----------- max_tokens 兜底 -----------
        # 很多 CLI（Codex / Hermes / Claude Code）会发 max_tokens=0 或 max_tokens=null
        # 上游 DeepSeek / 硅基流动 / OpenAI 都不接受 0 或负数 → 400
        # 统一把非法值修正掉
        # 部分模型（如 NVIDIA）不支持超过 32768，统一截断避免 400
        DEFAULT_MAX_TOKENS = 512
        MAX_OUTPUT_TOKENS_CAP = 32768
        for key in ("max_tokens", "max_output_tokens"):
            if key in p:
                val = p[key]
                try:
                    if val is None or val <= 0:
                        p[key] = DEFAULT_MAX_TOKENS
                    else:
                        val_i = int(val)
                        if val_i <= 0:
                            p[key] = DEFAULT_MAX_TOKENS
                        else:
                            capped = min(val_i, MAX_OUTPUT_TOKENS_CAP)
                            if capped != val_i:
                                p[key] = capped
                            else:
                                p[key] = val_i
                except Exception:
                    p[key] = DEFAULT_MAX_TOKENS

        # ----------- stream 对齐 -----------
        # 统一把 stream 参数强制对齐：客户端要非流式就不要向上游发 stream=true，
        # 避免某些服务商（智谱）对 stream=true 非流式回调路径异常。
        # 客户端原始意图从 self._handle_chat_completions 里直接看
        # 这里只做一件事：把 stream_options 丢掉，并确保 stream 是布尔值
        if "stream_options" in p:
            p.pop("stream_options", None)
        if "stream" in p:
            try:
                p["stream"] = bool(p["stream"])
            except Exception:
                p["stream"] = False

        return p

    @staticmethod
    def _extract_usage_from_stream(raw: bytes) -> Dict[str, int]:
        """从 SSE 流式响应体中提取 token 用量。

        OpenAI 兼容服务商在结束前发送包含 usage 的 data: {...} 事件，
        格式如：data: {"id":"...","choices":[],"usage":{"prompt_tokens":N,"completion_tokens":N,...}}
        """
        usage: Dict[str, int] = {}
        try:
            text = raw.decode("utf-8", errors="replace")
            for line in text.split("\n"):
                line = line.strip()
                if line.startswith("data:") and line != "data: [DONE]":
                    chunk = line[5:].strip()
                    if chunk:
                        try:
                            obj = json.loads(chunk)
                            u = obj.get("usage")
                            if u and isinstance(u, dict):
                                usage = u
                        except Exception:
                            pass
        except Exception:
            pass
        return usage

    def _extract_usage_from_response(self, resp_body: Any, stream_body: Optional[bytes]) -> Dict[str, int]:
        """从上游响应中提取 token 用量。"""
        usage: Dict[str, int] = {}
        # 非流式：resp_body 是 dict，直接取 usage 字段
        if isinstance(resp_body, dict):
            u = resp_body.get("usage")
            if u and isinstance(u, dict):
                return dict(u)
        # 流式：从原始 bytes 中解析
        if stream_body:
            return ProxyHandler._extract_usage_from_stream(stream_body)
        return usage

    def _count_response_tokens(self, resp_body: Any, stream_body: Optional[bytes]) -> None:
        """从上游响应提取 token 并记录到状态。"""
        usage = self._extract_usage_from_response(resp_body, stream_body)
        prompt = usage.get("prompt_tokens", 0) or 0
        completion = usage.get("completion_tokens", 0) or 0
        if prompt > 0 or completion > 0:
            self.state.record_tokens(prompt, completion)
            total = prompt + completion
            self.state.emit("📊", f"tokens: ↑{prompt} ↓{completion} ∑{total}")

    def _handle_forward(self, action: str) -> None:
        """通用转发：图片/视频等端点直接透传，不改写 payload。"""
        assert self.state is not None
        cur = self.state.current()
        if not cur:
            self._send_json(503, {"error": {"message": "no active provider", "type": "no_provider"}})
            return
        raw = self._read_body()
        try:
            payload = json.loads(raw.decode("utf-8") or "{}") if raw else {}
        except Exception as e:
            self._send_json(400, {"error": {"message": f"invalid json: {e}", "type": "bad_request"}})
            return
        url = self._upstream_endpoint(cur, action)
        self.state.emit("ℹ️", f"收到 /v1/{action}  model={payload.get('model', '(无)')}")
        try:
            resp_body, resp_headers, resp_code = self._do_upstream(cur, url, payload)
        except Exception as e:
            self._send_json(502, {"error": {"message": f"upstream error: {e}", "type": "upstream_error"}})
            return
        if resp_code >= 400:
            if isinstance(resp_body, dict):
                self._send_json(resp_code, resp_body)
            else:
                self._send_json(resp_code, {"error": {"message": str(resp_body), "type": "upstream_error"}})
        else:
            try:
                self._send_json(resp_code, resp_body if isinstance(resp_body, dict) else {"content": str(resp_body)})
            except Exception:
                self._send_json(resp_code, {"content": str(resp_body) if resp_body is not None else ""})

    def _handle_forward_get(self, path: str) -> None:
        """通用 GET 转发（视频状态轮询等）。"""
        assert self.state is not None
        cur = self.state.current()
        if not cur:
            self._send_json(503, {"error": {"message": "no active provider", "type": "no_provider"}})
            return
        base = normalize_base_url(cur.base_url) or ""
        base = base.rstrip("/")
        url = base + "/" + path.lstrip("/")
        self.state.emit("ℹ️", f"GET 转发: {url}")
        try:
            ssl_ctx = self._create_ssl_context()
            req = urllib.request.Request(
                url, method="GET",
                headers={
                    "Authorization": f"Bearer {cur.api_key}",
                    "Accept": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ccswith-proxy/1.0",
                },
            )
            with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                parsed: Any = raw
                if raw:
                    try:
                        parsed = json.loads(raw)
                    except Exception:
                        parsed = raw
                self._send_json(resp.getcode() or 200, parsed)
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")
                parsed: Any = body
                try:
                    parsed = json.loads(body)
                except Exception:
                    pass
                self._send_json(e.code, parsed)
            except Exception:
                self._send_json(e.code, {"error": {"message": str(e), "type": "upstream_error"}})
        except Exception as e:
            self._send_json(502, {"error": {"message": f"upstream error: {e}", "type": "upstream_error"}})

    def _handle_chat_completions(self) -> None:
        assert self.state is not None
        cur = self.state.current()
        if not cur:
            self._send_json(503, {"error": {"message": "no active provider", "type": "no_provider"}})
            return
        raw = self._read_body()
        try:
            payload = json.loads(raw.decode("utf-8") or "{}") if raw else {}
        except Exception as e:
            self._send_json(400, {"error": {"message": f"invalid json: {e}", "type": "bad_request"}})
            return
        # 部分 agent（如 Hermes）发送工具探测请求时不带 messages，但上游要求必须存在
        if "messages" not in payload or not isinstance(payload.get("messages"), list) or not payload["messages"]:
            payload["messages"] = [{"role": "user", "content": "."}]
            self.state.emit("ℹ️", "请求缺少 messages，已自动补默认消息")
        payload = self._rewrite_payload_for_provider(cur, "chat/completions", payload)
        # 单次请求中重复信息合并
        if cur.deduplicate and "messages" in payload:
            merged = []
            for msg in payload["messages"]:
                if merged and msg.get("role") == merged[-1].get("role") and msg.get("content") == merged[-1].get("content"):
                    self.state.emit("ℹ️", f"去重合并: 跳过重复的 {msg.get('role')} 消息")
                    continue
                merged.append(msg)
            if len(merged) != len(payload["messages"]):
                payload["messages"] = merged
                self.state.emit("ℹ️", f"已合并 {len(payload['messages'])} 条去重后消息")
        # 根据改写后的真实模型名自动推断端点
        action = "chat/completions"
        real_model = (payload or {}).get("model", "")
        if isinstance(real_model, str):
            ml = real_model.lower().strip()
            if "image" in ml or "dall" in ml:
                action = "images/generations"
            elif "video" in ml:
                action = "videos"
        url = self._upstream_endpoint(cur, action)
        stream = bool(payload.get("stream"))
        if action != "chat/completions":
            # 非 chat 端点：上游响应是普通 JSON，不是 SSE 流 → 强制非流式
            payload["stream"] = False
            stream = False
        ep_label = f" → {action}" if action != "chat/completions" else ""
        self.state.emit("ℹ️", f"收到 /v1/chat/completions  model={payload.get('model')}{ep_label} stream={stream}")
        try:
            resp_body, resp_headers, resp_code = self._do_upstream(cur, url, payload)
        except Exception as e:
            self._send_json(502, {"error": {"message": f"upstream error: {e}", "type": "upstream_error"}})
            return

        # 流式：原样透传
        if stream and isinstance(resp_body, (bytes, bytearray)):
            try:
                body = bytes(resp_body)
                self._count_response_tokens(None, body)
                ctype = resp_headers.get("content-type", "text/event-stream")
                self.send_response(resp_code)
                self.send_header("Content-Type", ctype)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Expose-Headers", "*")
                self.end_headers()
                if body:
                    self.wfile.write(body)
                self.state.emit("✅", f"stream 响应已写回客户端，共 {len(body)} 字节")
                return
            except BrokenPipeError:
                self.state.emit("❌", "stream 写回客户端失败: 连接已断开 (BrokenPipe)")
                return
            except Exception as e:
                self.state.emit("❌", f"stream 写回客户端失败: {e}")
                return

        # 非流式（token 计数不会中断请求）
        try:
            self._count_response_tokens(resp_body, None)
        except Exception:
            pass
        if resp_code >= 400:
            # 错误响应：原样透传，不包装
            if isinstance(resp_body, dict):
                self._send_json(resp_code, resp_body)
            else:
                self._send_json(resp_code, {"error": {"message": str(resp_body), "type": "upstream_error"}})
        else:
            try:
                self._send_json(resp_code, resp_body if isinstance(resp_body, dict) else {"content": str(resp_body) if resp_body is not None else ""})
            except Exception:
                self._send_json(resp_code, {"content": str(resp_body) if resp_body is not None else ""})

    def _handle_responses(self) -> None:
        """OpenAI /v1/responses 新版 API：上游大多不支持，自动转成 chat.completions。"""
        assert self.state is not None
        cur = self.state.current()
        if not cur:
            self._send_json(503, {"error": {"message": "no active provider", "type": "no_provider"}})
            return
        raw = self._read_body()
        try:
            payload = json.loads(raw.decode("utf-8") or "{}") if raw else {}
        except Exception as e:
            self._send_json(400, {"error": {"message": f"invalid json: {e}", "type": "bad_request"}})
            return
        # 部分 agent 发送工具探测请求时不带 messages，但上游要求必须存在
        if "messages" not in payload or not isinstance(payload.get("messages"), list) or not payload["messages"]:
            payload["messages"] = [{"role": "user", "content": "."}]
            self.state.emit("ℹ️", "请求缺少 messages，已自动补默认消息")

        # 统一映射成 chat.completions 格式
        chat_payload = self._responses_to_chat(payload)
        chat_payload = self._rewrite_payload_for_provider(cur, "chat/completions", chat_payload)
        # 单次请求中重复信息合并
        if cur.deduplicate and "messages" in chat_payload:
            merged = []
            for msg in chat_payload["messages"]:
                if merged and msg.get("role") == merged[-1].get("role") and msg.get("content") == merged[-1].get("content"):
                    self.state.emit("ℹ️", f"去重合并: 跳过重复的 {msg.get('role')} 消息")
                    continue
                merged.append(msg)
            if len(merged) != len(chat_payload["messages"]):
                chat_payload["messages"] = merged
                self.state.emit("ℹ️", f"已合并 {len(chat_payload['messages'])} 条去重后消息")
        url = self._upstream_endpoint(cur, "chat/completions")
        stream = bool(chat_payload.get("stream"))
        self.state.emit(
            "ℹ️",
            f"收到 /v1/responses -> 内部转 /v1/chat/completions  model={chat_payload.get('model')} stream={stream}",
        )
        try:
            resp_body, resp_headers, resp_code = self._do_upstream(cur, url, chat_payload)
        except Exception as e:
            self._send_json(502, {"error": {"message": f"upstream error: {e}", "type": "upstream_error"}})
            return

        # token 计数（不会因此中断请求）
        try:
            self._count_response_tokens(resp_body, bytes(resp_body) if isinstance(resp_body, (bytes, bytearray)) else None)
        except Exception:
            pass

        # 流式透传
        if stream and isinstance(resp_body, (bytes, bytearray)):
            try:
                self.send_response(resp_code)
                self.send_header("Content-Type", resp_headers.get("content-type", "text/event-stream"))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Expose-Headers", "*")
                self.end_headers()
                if resp_body:
                    self.wfile.write(bytes(resp_body))
                return
            except Exception as e:
                self.state.emit("❌", f"stream 写回失败: {e}")
                return

        # 非流式：把 chat.completions 响应再包装成 responses 格式
        if resp_code >= 400:
            if isinstance(resp_body, dict):
                self._send_json(resp_code, resp_body)
            else:
                self._send_json(resp_code, {"error": {"message": str(resp_body), "type": "upstream_error"}})
        else:
            try:
                if isinstance(resp_body, dict) and "choices" in resp_body:
                    return self._chat_to_responses(resp_body)
                self._send_json(resp_code, resp_body if isinstance(resp_body, dict) else {"content": str(resp_body) if resp_body is not None else ""})
            except Exception:
                self._send_json(resp_code, {"content": str(resp_body) if resp_body is not None else ""})

    def _handle_anthropic_messages_as_chat(self) -> None:
        """把 Anthropic /v1/messages 请求转成 OpenAI chat.completions，再把响应转回来。"""
        assert self.state is not None
        cur = self.state.current()
        if not cur:
            self._send_json(503, {"error": {"message": "no active provider", "type": "no_provider"}})
            return
        raw = self._read_body()
        try:
            payload = json.loads(raw.decode("utf-8") or "{}") if raw else {}
        except Exception as e:
            self._send_json(400, {"error": {"message": f"invalid json: {e}", "type": "bad_request"}})
            return

        chat_payload = self._anthropic_to_chat(payload)
        chat_payload = self._rewrite_payload_for_provider(cur, "chat/completions", chat_payload)
        url = self._upstream_endpoint(cur, "chat/completions")
        stream = bool(chat_payload.get("stream"))
        self.state.emit(
            "ℹ️",
            f"收到 /v1/messages (Anthropic) -> 内部转 /v1/chat/completions  model={chat_payload.get('model')} stream={stream}",
        )
        try:
            resp_body, resp_headers, resp_code = self._do_upstream(cur, url, chat_payload)
        except Exception as e:
            self._send_json(502, {"error": {"message": f"upstream error: {e}", "type": "upstream_error"}})
            return

        # token 计数（不会因此中断请求）
        try:
            self._count_response_tokens(resp_body, bytes(resp_body) if isinstance(resp_body, (bytes, bytearray)) else None)
        except Exception:
            pass

        if stream and isinstance(resp_body, (bytes, bytearray)):
            try:
                self.send_response(resp_code)
                self.send_header("Content-Type", resp_headers.get("content-type", "text/event-stream"))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Expose-Headers", "*")
                self.end_headers()
                if resp_body:
                    self.wfile.write(bytes(resp_body))
                return
            except Exception as e:
                self.state.emit("❌", f"stream 写回失败: {e}")
                return

        if resp_code >= 400:
            # 错误响应：原样透传上游错误，不包装
            if isinstance(resp_body, dict):
                self._send_json(resp_code, resp_body)
            else:
                self._send_json(resp_code, {"error": {"message": str(resp_body), "type": "upstream_error"}})
        else:
            try:
                if isinstance(resp_body, dict) and "choices" in resp_body:
                    return self._chat_to_anthropic(resp_body, payload)
                self._send_json(resp_code, {"content": str(resp_body) if resp_body is not None else ""})
            except Exception:
                self._send_json(resp_code, {"content": str(resp_body) if resp_body is not None else ""})

    # ---------------- payload 转换工具 ----------------
    @staticmethod
    def _responses_to_chat(payload: Dict[str, Any]) -> Dict[str, Any]:
        """OpenAI /v1/responses -> OpenAI /v1/chat/completions。"""
        p = dict(payload)
        if "messages" not in p:
            inp = p.pop("input", None)
            if isinstance(inp, str):
                p["messages"] = [{"role": "user", "content": inp}]
            elif isinstance(inp, list):
                msgs = []
                for item in inp:
                    if isinstance(item, dict):
                        role = item.get("role", "user")
                        content = item.get("content", "")
                        if isinstance(content, list):
                            text = "".join(
                                (c.get("text", "") if isinstance(c, dict) else str(c)) for c in content
                            )
                            content = text
                        msgs.append({"role": role, "content": str(content)})
                    elif isinstance(item, str):
                        msgs.append({"role": "user", "content": item})
                if msgs:
                    p["messages"] = msgs
        if "max_output_tokens" in p and "max_tokens" not in p:
            p["max_tokens"] = p.pop("max_output_tokens")
        p.pop("tools", None)
        p.pop("tool_choice", None)
        return p

    def _chat_to_responses(self, chat_resp: Dict[str, Any]) -> None:
        """把 chat.completions 响应再包装成 responses 格式写回。"""
        try:
            import time as _t
            now_ms = int(_t.time() * 1000)
            model = chat_resp.get("model", "")
            choices = chat_resp.get("choices", []) or []
            msg_text = ""
            finish_reason = "stop"
            if choices:
                c0 = choices[0] or {}
                msg = (c0.get("message") or {}) if isinstance(c0, dict) else {}
                msg_text = msg.get("content") or ""
                finish_reason = (c0.get("finish_reason") or "stop") if isinstance(c0, dict) else "stop"
            out = {
                "id": chat_resp.get("id") or f"resp_{chat_resp.get('id', '')}",
                "object": "response",
                "created_at": now_ms,
                "model": model,
                "status": "completed",
                "output": [
                    {
                        "id": chat_resp.get("id") or f"msg_{chat_resp.get('id', '')}",
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": msg_text,
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "usage": chat_resp.get("usage", {}),
            }
            if finish_reason != "stop":
                out["output"][0]["status"] = finish_reason
            ProxyHandler._send_json_fallback(self, 200, out)
        except Exception as e:
            # 兜底：直接透传原始 chat 响应
            ProxyHandler._send_json_fallback(self, 200, chat_resp)

    @staticmethod
    def _anthropic_to_chat(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Anthropic /v1/messages -> OpenAI chat.completions。

        保留 system prompt（转成 system 角色消息），透传 stream 等字段。
        """
        p = dict(payload)
        msgs = []

        # Anthropic 顶级 system 字段 → 转为 system 角色消息
        system_text = p.pop("system", None)
        if system_text and isinstance(system_text, str):
            msgs.append({"role": "system", "content": system_text})

        for m in p.get("messages", []) or []:
            if isinstance(m, dict):
                role = m.get("role", "user")
                content = m.get("content", "")
                if isinstance(content, list):
                    text = "".join(
                        (c.get("text", "") if isinstance(c, dict) else str(c)) for c in content
                    )
                    content = text
                msgs.append({"role": role, "content": str(content)})

        chat = {"model": p.get("model", ""), "messages": msgs}
        if p.get("max_tokens"):
            chat["max_tokens"] = p["max_tokens"]
        if "stream" in p:
            chat["stream"] = bool(p["stream"])
        return chat

    def _chat_to_anthropic(self, chat_resp: Dict[str, Any], req_payload: Dict[str, Any]) -> None:
        """把 chat.completions 响应包装成 Anthropic /v1/messages 响应写回。"""
        try:
            import time as _t
            now_ms = int(_t.time() * 1000)
            model = chat_resp.get("model", req_payload.get("model", ""))
            choices = chat_resp.get("choices", []) or []
            msg_text = ""
            stop_reason = "end_turn"
            if choices:
                c0 = choices[0] or {}
                msg = (c0.get("message") or {}) if isinstance(c0, dict) else {}
                msg_text = msg.get("content") or ""
                fr = (c0.get("finish_reason") or "stop") if isinstance(c0, dict) else "stop"
                if fr == "length":
                    stop_reason = "max_tokens"
                else:
                    stop_reason = "end_turn"
            out = {
                "id": chat_resp.get("id") or f"msg_{chat_resp.get('id', '')}",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [{"type": "text", "text": msg_text}],
                "stop_reason": stop_reason,
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": int((chat_resp.get("usage") or {}).get("completion_tokens", 0) or 0),
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            }
            ProxyHandler._send_json_fallback(self, 200, out)
        except Exception:
            ProxyHandler._send_json_fallback(self, 200, chat_resp)

    @staticmethod
    def _send_json_fallback(obj: Any, code: int, data: Any) -> None:
        """静态方法版的 _send_json，供 payload 转换工具内部调用。"""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        obj.send_response(code)
        obj.send_header("Content-Type", "application/json; charset=utf-8")
        obj.send_header("Content-Length", str(len(body)))
        obj.send_header("Cache-Control", "no-store")
        obj.end_headers()
        obj.wfile.write(body)


    @staticmethod
    def _create_ssl_context() -> ssl.SSLContext:
        """创建兼容性更好的 SSL 上下文，解决 Windows 环境下的证书验证问题。"""
        ctx = ssl.create_default_context()
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx

    def _do_upstream(
        self,
        provider: Provider,
        url: str,
        payload: Dict[str, Any],
        use_anthropic_messages: bool = False,
    ) -> Tuple[Any, Dict[str, str], int]:
        assert self.state is not None
        # 速率限制检查（滑动窗口）
        if provider.rate_limit > 0:
            wait = self.state.check_rate_limit(provider.alias, provider.rate_limit)
            if wait is not None:
                self.state.emit("⚠️", f"速率限制 {provider.rate_limit}/分钟，等待 {wait:.1f}s ...")
                try:
                    time.sleep(wait)
                except KeyboardInterrupt:
                    pass
        # 是否流式
        stream = bool(payload.get("stream"))
        self.state.emit("📊", f"转发请求 -> {url}  stream={stream}")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {provider.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream else "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ccswith-proxy/1.0",
            },
        )

        # 重试配置：最多重试 2 次（共 3 次尝试），指数退避
        max_attempts = 3
        base_delay = 1.0
        last_exception: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            try:
                ssl_ctx = self._create_ssl_context()
                with urllib.request.urlopen(req, timeout=300 if stream else 120, context=ssl_ctx) as resp:
                    resp_code = resp.getcode() or 200
                    resp_headers = {k.lower(): v for k, v in resp.headers.items()}
                    if stream:
                        self.state.emit("📊", f"收到上游 stream 响应 code={resp_code}，开始透传 chunks")
                        raw = resp.read()
                        self.state.emit("📊", f"stream 转发完成，共 {len(raw)} 字节")
                        return raw, resp_headers, resp_code
                    raw = resp.read().decode("utf-8", errors="replace")
                    self.state.emit("📊", f"上游响应 code={resp_code} 长度={len(raw)}")
                    parsed: Any = raw
                    if raw:
                        try:
                            parsed = json.loads(raw)
                        except Exception:
                            parsed = raw
                    return parsed, resp_headers, resp_code
            except urllib.error.HTTPError as e:
                code = e.code or 502
                try:
                    body_raw = e.read()
                    body_text = body_raw.decode("utf-8", errors="replace")
                except Exception:
                    body_raw = b""
                    body_text = ""
                self.state.emit("❌", f"上游 HTTP 错误 code={code}  body={body_text[:400]}")
                # max_tokens 超限自动修复: 提取上限值, 重发
                if code == 400 and "max_tokens" in body_text and "above maximum" in body_text and attempt < max_attempts:
                    import re
                    m = re.search(r'expected a value <= (\d+)', body_text)
                    if m:
                        max_val = int(m.group(1))
                        old_max = payload.get("max_tokens", payload.get("max_output_tokens", "?"))
                        for k in ("max_tokens", "max_output_tokens"):
                            if k in payload:
                                payload[k] = max_val
                        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                        req.data = data
                        self.state.emit("⚠️", f"max_tokens {old_max} → {max_val} 自动降级重试 ({attempt}/{max_attempts})")
                        delay = base_delay * (2 ** (attempt - 1))
                        time.sleep(delay)
                        continue
                # 429（限流）和 5xx（服务端错误）可重试
                retryable = (code == 429 or code >= 500) and attempt < max_attempts
                if retryable:
                    delay = base_delay * (2 ** (attempt - 1))
                    self.state.emit("⚠️", f"HTTP {code} 将在 {delay:.1f}s 后重试 ({attempt}/{max_attempts})")
                    time.sleep(delay)
                    continue
                if stream:
                    return body_raw, {}, code
                parsed: Any = body_text
                if body_text:
                    try:
                        parsed = json.loads(body_text)
                    except Exception:
                        parsed = body_text
                return parsed, {}, code
            except (urllib.error.URLError, socket.timeout, OSError) as e:
                last_exception = e
                # 网络级错误可重试
                if attempt < max_attempts:
                    delay = base_delay * (2 ** (attempt - 1))
                    self.state.emit("⚠️", f"连接异常 {type(e).__name__} 将在 {delay:.1f}s 后重试 ({attempt}/{max_attempts})")
                    time.sleep(delay)
                    continue
                self.state.emit("❌", f"上游请求异常（已重试 {max_attempts} 次）: {type(e).__name__}: {e}")
                raise
            except Exception as e:
                # 非网络类异常不重试，立即抛出
                self.state.emit("❌", f"上游请求异常: {type(e).__name__}: {e}")
                raise


def create_proxy_http_server(host: str, port: int, state: ProxyState) -> ThreadingHTTPServer:
    """创建代理 HTTP 服务。

    返回的 httpd 可以通过 shutdown() + server_close() 停止。
    """
    ProxyHandler.state = state
    httpd = ThreadingHTTPServer((host, port), ProxyHandler)
    state.emit("✅", f"API 网关已准备就绪: http://{host}:{port}/v1")
    return httpd


def pick_free_port(host: str = "127.0.0.1", start: int = 8787, tries: int = 20) -> int:
    for i in range(tries):
        p = start + i
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, p))
            s.close()
            return p
        except OSError:
            continue
    return start


# ---------------------------- 运行时上下文（供 GUI / CLI 共用） ----------------------------

class SwitchEngine:
    """把 ProviderManager + CLI 适配 + 备份 组合成高层能力。"""

    def __init__(self):
        self.manager = ProviderManager()

    def switch_to(
        self,
        alias: str,
        backup_first: bool = True,
        enabled_clis: Optional[List[str]] = None,
        proxy_base_url: Optional[str] = None,
        virtual_model: Optional[str] = None,
        write_hermes: bool = True,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        provider = self.manager.get(alias)
        if not provider:
            return False, f"未找到服务商: {alias}", {}
        if backup_first:
            try:
                backup_dir = backup_cli_configs()
            except Exception as e:
                logger.warning("自动备份失败: %s", e)
                backup_dir = None
        else:
            backup_dir = None

        try:
            enabled = enabled_clis if enabled_clis else list(CLI_TEMPLATES.keys())
            results: Dict[str, Any] = {}
            use_proxy = bool(proxy_base_url)
            for cli_key in enabled:
                if cli_key == "hermes":
                    # Hermes 走专用函数（写 config.yaml custom_provider + model）
                    if write_hermes and proxy_base_url:
                        yaml_path, msg = hermes_merge_config(
                            provider,
                            proxy_base_url.rstrip("/"),
                            virtual_model or HERMES_VIRTUAL_MODEL,
                        )
                        results["hermes"] = [{"path": str(yaml_path), "ok": True, "msg": msg}]
                    else:
                        sub = apply_provider_to_cli(provider, "hermes")
                        results["hermes"] = [
                            {"path": str(p), "ok": ok, "msg": msg} for (p, ok, msg) in sub
                        ]
                else:
                    sub = apply_provider_to_cli(
                        provider, cli_key,
                        use_proxy=use_proxy,
                        proxy_base_url=(proxy_base_url or "").rstrip("/"),
                        virtual_model=(virtual_model or HERMES_VIRTUAL_MODEL),
                    )
                    results[cli_key] = [
                        {"path": str(p), "ok": ok, "msg": msg} for (p, ok, msg) in sub
                    ]
            self.manager.set_current(alias)
            summary = f"已切换到 {alias}"
            if write_hermes and proxy_base_url:
                summary += (
                    f"；Hermes 已指向代理 {proxy_base_url.rstrip('/')} "
                    f"，虚拟模型 {virtual_model or HERMES_VIRTUAL_MODEL}"
                )
            return True, summary, {
                "provider": provider.alias,
                "backup_dir": str(backup_dir) if backup_dir else "",
                "details": results,
            }
        except Exception as e:
            return False, f"切换失败: {e}", {}
