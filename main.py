"""
CC-Switch Python 版 - 程序入口。

启动 GUI：
    python main.py

纯 CLI：
    python main.py list
    python main.py add <alias> <api_key> <base_url> <model> [api_format]
    python main.py remove <alias>
    python main.py switch <alias>                        # 把配置写入所有 CLI
    python main.py switch <alias> --no-backup
    python main.py switch <alias> --clis claude_code,hermes
    python main.py switch <alias> --proxy 127.0.0.1:8787  # 同时启动代理并把 Hermes 指到它
    python main.py backup
    python main.py backups
    python main.py rollback <backup_dir_name>
    python main.py import <json_file> [--merge]
    python main.py export <json_file>
    python main.py status                                # 当前服务商 / 代理地址 / 虚拟模型
    python main.py serve                                 # 启动 OpenAI 兼容 HTTP 代理（前台）
    python main.py serve --host 127.0.0.1 --port 8787
    python main.py hermes <alias> --proxy 127.0.0.1:8787  # 只配 Hermes + 代理地址
    python main.py test <alias> --proxy 127.0.0.1:8787   # 发送一次 /v1/chat/completions 冒烟测试
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

from core import (
    CLI_TEMPLATES,
    HERMES_VIRTUAL_MODEL,
    Provider,
    ProviderManager,
    ProxyState,
    SwitchEngine,
    apply_provider_to_cli,
    backup_cli_configs,
    create_proxy_http_server,
    get_backup_dir,
    hermes_merge_config,
    list_backups,
    pick_free_port,
    rollback_from_backup,
)

logger = logging.getLogger("ccswith")


def _safe_stdout() -> None:
    try:
        import sys as _sys
        if hasattr(_sys.stdout, "reconfigure"):
            try:
                _sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        if hasattr(_sys.stderr, "reconfigure"):
            try:
                _sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    except Exception:
        pass


def _init_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------------------------- 子命令实现 ----------------------------

def _cmd_list(_args) -> int:
    mgr = ProviderManager()
    lst = mgr.list_all()
    if not lst:
        print("(暂无服务商，请使用 `python main.py add ...` 新增)")
        return 0
    cur = mgr.current_alias
    print(f"当前服务商: {cur or '(未设置)'}")
    for p in lst:
        flag = " *" if p.alias == cur else "  "
        enabled = "启用" if p.enabled else "禁用"
        print(f"{flag} {p.alias:<20} display={p.display_name} model={p.model or '-'} fmt={p.api_format} [{enabled}]")
    return 0


def _cmd_add(args) -> int:
    mgr = ProviderManager()
    fmt = args.api_format or "openai"
    p = Provider(
        alias=args.alias,
        display_name=args.alias,
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
        api_format=fmt,
        enabled=True,
    )
    mgr.add_or_update(p)
    print(f"[OK] 已添加/更新 {args.alias}  ({p.display_name}, {p.model or '-'}, {p.api_format})")
    return 0


def _cmd_remove(args) -> int:
    mgr = ProviderManager()
    ok = mgr.remove(args.alias)
    if ok:
        print(f"[OK] 已删除 {args.alias}")
        return 0
    print(f"[ERR] 未找到 {args.alias}")
    return 1


def _parse_clis(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    clis = [c.strip() for c in raw.split(",") if c.strip()]
    unknown = [c for c in clis if c not in CLI_TEMPLATES]
    if unknown:
        print(f"[WARN] 未知 CLI: {unknown}. 已知: {list(CLI_TEMPLATES.keys())}")
    return clis


def _cmd_switch(args) -> int:
    mgr = ProviderManager()
    p = mgr.get(args.alias)
    if not p:
        print(f"[ERR] 未找到服务商 {args.alias}，可用 `python main.py add {args.alias} ...`")
        return 1

    # 决定代理地址（可选）
    proxy_url = None
    need_start_proxy = False
    if args.proxy:
        host, _, port = args.proxy.partition(":")
        host = host or "127.0.0.1"
        try:
            port_i = int(port) if port else pick_free_port(host)
        except ValueError:
            port_i = pick_free_port(host)
        proxy_url = f"http://{host}:{port_i}/v1"
        need_start_proxy = True

    engine = SwitchEngine()
    clis = _parse_clis(args.clis)
    ok, msg, info = engine.switch_to(
        args.alias,
        backup_first=not args.no_backup,
        enabled_clis=clis,
        proxy_base_url=proxy_url,
        virtual_model=args.virtual_model or HERMES_VIRTUAL_MODEL,
        write_hermes=True,
    )
    print(f"{'[OK]' if ok else '[ERR]'} {msg}")
    for cli_key, arr in info.get("details", {}).items():
        print(f"  [{cli_key}]")
        for item in arr:
            mark = "+" if item.get("ok") else "-"
            print(f"    {mark} {item.get('msg')}  ({item.get('path')})")
    if not ok:
        return 1

    if need_start_proxy and proxy_url:
        host, _, port = args.proxy.partition(":")
        host = host or "127.0.0.1"
        try:
            port_i = int(port) if port else int(proxy_url.rsplit(":", 1)[-1].rstrip("/v1"))
        except ValueError:
            port_i = int(proxy_url.rsplit(":", 1)[-1].rstrip("/v1"))
        state = ProxyState(engine.manager)
        httpd = create_proxy_http_server(host, port_i, state)
        print(f"[OK] OpenAI 兼容代理已启动: {proxy_url}  (CTRL+C 停止)")
        print(f"     虚拟模型 ID: {args.virtual_model or HERMES_VIRTUAL_MODEL}")
        print(f"     当前服务商: {p.alias} -> {p.base_url} ({p.model or '-'}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("[OK] 代理已停止")
            httpd.server_close()
    return 0


def _cmd_backup(_args) -> int:
    b = backup_cli_configs()
    print(f"[OK] 备份完成: {b}")
    return 0


def _cmd_backups(_args) -> int:
    items = list_backups()
    if not items:
        print("(暂无备份)")
        return 0
    for it in items:
        print(it.name)
    return 0


def _cmd_rollback(args) -> int:
    root = get_backup_dir()
    p = Path(args.backup) if os.path.isabs(args.backup) else root / args.backup
    if not p.exists():
        print(f"[ERR] 备份不存在: {p}")
        return 1
    res = rollback_from_backup(p)
    print(f"[OK] 已恢复: files={res['files']}, providers={res['providers']}")
    return 0


def _cmd_import(args) -> int:
    mgr = ProviderManager()
    n = mgr.import_json(Path(args.file), merge=bool(args.merge))
    print(f"[OK] 导入 {n} 个服务商")
    return 0


def _cmd_export(args) -> int:
    mgr = ProviderManager()
    n = mgr.export_json(Path(args.file))
    print(f"[OK] 已导出 {n} 个服务商")
    return 0


def _cmd_status(_args) -> int:
    mgr = ProviderManager()
    cur = mgr.get_current()
    print(f"当前服务商: {cur.alias if cur else '(未设置)'}")
    if cur:
        print(f"  display:   {cur.display_name}")
        print(f"  model:     {cur.model or '(未设置)'}")
        print(f"  api_format:{cur.api_format}")
        print(f"  base_url:  {cur.base_url}")
    print(f"虚拟模型 ID: {HERMES_VIRTUAL_MODEL}")
    print(f"Hermes 配置: {getattr(__import__('core'), 'hermes_config_path', lambda: '?')()}")
    print(f"CLI 目标：  {list(CLI_TEMPLATES.keys())}")
    return 0


def _cmd_serve(args) -> int:
    mgr = ProviderManager()
    cur = mgr.get_current()
    host = args.host or "127.0.0.1"
    port = args.port if args.port else pick_free_port(host)
    state = ProxyState(mgr)
    state.set_virtual_model(args.virtual_model or HERMES_VIRTUAL_MODEL)
    httpd = create_proxy_http_server(host, port, state)
    url = f"http://{host}:{port}/v1"
    print(f"[OK] OpenAI 兼容代理已启动: {url}  (CTRL+C 停止)")
    print(f"     虚拟模型 ID: {args.virtual_model or HERMES_VIRTUAL_MODEL}")
    if cur:
        print(f"     当前服务商: {cur.alias} -> {cur.base_url} ({cur.model or '-'})")
    else:
        print("     (尚未设置当前服务商；可用 `python main.py switch <alias>` 设置)")
    print(f"     /v1/models              列模型")
    print(f"     /v1/chat/completions    对话")
    print(f"     /v1/responses            OpenAI 新版响应")
    print(f"     /healthz                健康检查")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("[OK] 代理已停止")
        httpd.server_close()
    return 0


def _cmd_hermes(args) -> int:
    mgr = ProviderManager()
    p = mgr.get(args.alias)
    if not p:
        print(f"[ERR] 未找到服务商 {args.alias}")
        return 1
    if args.proxy:
        host, _, port = args.proxy.partition(":")
        host = host or "127.0.0.1"
        try:
            port_i = int(port) if port else pick_free_port(host)
        except ValueError:
            port_i = pick_free_port(host)
        proxy_url = f"http://{host}:{port_i}/v1"
        yaml_path, msg = hermes_merge_config(p, proxy_url, args.virtual_model or HERMES_VIRTUAL_MODEL)
        mgr.set_current(args.alias)
        print(f"[OK] Hermes 已配置: {msg}")
        print(f"     代理地址 {proxy_url}  (如需启动代理请运行 `python main.py serve --host {host} --port {port_i}`)")
    else:
        # 只写基础 .env / config.yaml，不启动代理
        mgr.set_current(args.alias)
        for path, ok, msg in apply_provider_to_cli(p, "hermes"):
            mark = "+" if ok else "-"
            print(f"    {mark} {msg}  ({path})")
    return 0


def _cmd_test(args) -> int:
    mgr = ProviderManager()
    cur = mgr.get_current()
    if not cur:
        print("[ERR] 请先 `switch <alias>` 设置当前服务商")
        return 1
    host, _, port = (args.proxy or "127.0.0.1:0").partition(":")
    host = host or "127.0.0.1"
    try:
        port_i = int(port) if port and int(port) > 0 else pick_free_port(host)
    except ValueError:
        port_i = pick_free_port(host)

    state = ProxyState(mgr)
    state.set_virtual_model(args.virtual_model or HERMES_VIRTUAL_MODEL)
    httpd = create_proxy_http_server(host, port_i, state)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.3)
    url = f"http://{host}:{port_i}/v1/chat/completions"
    payload = {
        "model": args.virtual_model or HERMES_VIRTUAL_MODEL,
        "messages": [{"role": "user", "content": "请用一句话介绍你自己。"}],
        "temperature": 0.2,
        "max_tokens": 120,
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": "Bearer test-dummy",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            code = resp.getcode()
        print(f"[OK] 代理冒烟测试通过 code={code}")
        try:
            parsed = json.loads(body)
            choices = parsed.get("choices") or []
            text = ""
            if choices:
                msg = choices[0].get("message") or {}
                text = msg.get("content", "") or ""
            print(f"     回复: {text[:200]}{'...' if len(text) > 200 else ''}")
        except Exception:
            print(f"     raw: {body[:200]}")
    except Exception as e:
        print(f"[ERR] 代理测试失败: {e}")
        return 1
    finally:
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass
    return 0


# ---------------------------- 解析器 ----------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ccswith", description="CC-Switch Python 版 - 多 AI CLI 配置切换工具 + 本地 API 代理")
    p.add_argument("-v", "--verbose", action="store_true", help="详细日志")

    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("list", help="列出所有服务商")

    a = sub.add_parser("add", help="添加或更新一个服务商")
    a.add_argument("alias")
    a.add_argument("api_key")
    a.add_argument("base_url")
    a.add_argument("model")
    a.add_argument("api_format", nargs="?", default="openai")

    r = sub.add_parser("remove", help="删除服务商")
    r.add_argument("alias")

    s = sub.add_parser("switch", help="把选中服务商写入所有 CLI（可启动代理并把 Hermes 指向它）")
    s.add_argument("alias")
    s.add_argument("--no-backup", action="store_true", help="切换前不自动备份")
    s.add_argument("--clis", help="只写入指定 CLI（逗号分隔），如 claude_code,codex,hermes")
    s.add_argument("--proxy", help="同时启动 OpenAI 兼容代理并让 Hermes 指向它，格式 host:port")
    s.add_argument("--virtual-model", default=HERMES_VIRTUAL_MODEL, help=f"虚拟模型 ID（默认 {HERMES_VIRTUAL_MODEL}）")

    sub.add_parser("backup", help="立即生成备份快照")
    sub.add_parser("backups", help="列出所有备份")

    rb = sub.add_parser("rollback", help="从备份恢复")
    rb.add_argument("backup")

    im = sub.add_parser("import", help="从 JSON 导入服务商")
    im.add_argument("file")
    im.add_argument("--merge", action="store_true", help="合并到现有列表")

    ex = sub.add_parser("export", help="导出服务商到 JSON")
    ex.add_argument("file")

    sub.add_parser("status", help="显示当前服务商 / 代理地址 / 虚拟模型")

    sv = sub.add_parser("serve", help="启动 OpenAI 兼容 HTTP 代理（前台）")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=None)
    sv.add_argument("--virtual-model", default=HERMES_VIRTUAL_MODEL, help=f"虚拟模型 ID（默认 {HERMES_VIRTUAL_MODEL}）")

    hm = sub.add_parser("hermes", help="只配置 Hermes（可附带代理地址）")
    hm.add_argument("alias")
    hm.add_argument("--proxy", help="同时让 Hermes 指向的代理地址 host:port")
    hm.add_argument("--virtual-model", default=HERMES_VIRTUAL_MODEL, help=f"虚拟模型 ID（默认 {HERMES_VIRTUAL_MODEL}）")

    ts = sub.add_parser("test", help="启动代理并发送一次 chat.completions 冒烟测试")
    ts.add_argument("--proxy", help="指定代理地址 host:port（端口可填 0 自动挑选）")
    ts.add_argument("--virtual-model", default=HERMES_VIRTUAL_MODEL)
    return p


def main(argv: list[str] | None = None) -> int:
    _safe_stdout()
    parser = _build_parser()
    args = parser.parse_args(argv)
    _init_logging(getattr(args, "verbose", False))

    if not getattr(args, "cmd", None):
        # 无参数：启动 GUI
        try:
            import gui  # noqa: F401
            gui.run_gui()
        except Exception as e:
            print(f"[ERR] GUI 启动失败: {e}")
            sys.exit(1)
        return 0

    dispatch = {
        "list": _cmd_list,
        "add": _cmd_add,
        "remove": _cmd_remove,
        "switch": _cmd_switch,
        "backup": _cmd_backup,
        "backups": _cmd_backups,
        "rollback": _cmd_rollback,
        "import": _cmd_import,
        "export": _cmd_export,
        "status": _cmd_status,
        "serve": _cmd_serve,
        "hermes": _cmd_hermes,
        "test": _cmd_test,
    }
    fn = dispatch.get(args.cmd)
    if fn is None:
        parser.print_help()
        return 1
    try:
        return int(fn(args))
    except KeyboardInterrupt:
        print("\n[OK] 用户中断")
        return 0


if __name__ == "__main__":
    sys.exit(main())
