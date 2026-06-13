"""把 Codex + Hermes 都改成走 CC-Switch 代理 127.0.0.1:3000。"""
import json
import pathlib

import core


def main() -> None:
    mgr = core.ProviderManager()
    cur = mgr.get_current()
    proxy_url = "http://127.0.0.1:3000/v1"
    virtual = "virtual-model"

    # 1) 写 Codex config.toml
    toml_p = pathlib.Path.home() / ".codex" / "config.toml"
    toml_p.write_text(
        f'model_provider = "openai"\n'
        f'model = "{virtual}"\n'
        f'api_key = "{cur.api_key or "ccswitch-empty"}"\n'
        f'base_url = "{proxy_url}"\n',
        encoding="utf-8",
    )
    print(f"✅ Codex config.toml: base_url={proxy_url} model={virtual}")

    # 2) 写 Codex config.json
    json_p = pathlib.Path.home() / ".codex" / "config.json"
    try:
        j = json.loads(json_p.read_text(encoding="utf-8"))
    except Exception:
        j = {}
    j["apiKey"] = cur.api_key or "ccswitch-empty"
    j["baseURL"] = proxy_url
    j["model"] = virtual
    json_p.write_text(json.dumps(j, indent=2, ensure_ascii=False), encoding="utf-8")

    # 3) 写 Hermes（所有可能位置）
    for base in [
        pathlib.Path.home() / ".config" / "hermes",
        pathlib.Path.home() / ".hermes",
        pathlib.Path.home() / "AppData" / "Roaming" / "hermes",
        pathlib.Path.home() / "AppData" / "Local" / "hermes",
    ]:
        try:
            base.mkdir(parents=True, exist_ok=True)
        except Exception:
            continue
        yaml_p = base / "config.yaml"
        data = {}
        if yaml_p.exists():
            try:
                data = core.yaml_load(yaml_p) or {}
            except Exception:
                data = {}
        data = data if isinstance(data, dict) else {}
        data.setdefault("model", {})
        data["model"]["provider"] = "openai"
        data["model"]["default"] = virtual
        data["model"]["base_url"] = proxy_url
        data["model"]["api_key"] = cur.api_key or "ccswitch-empty"
        cps = data.get("custom_providers", [])
        if not isinstance(cps, list):
            cps = []
        cc = next(
            (e for e in cps if isinstance(e, dict) and e.get("name") == "ccswitch"), None
        )
        if not cc:
            cc = {"name": "ccswitch"}
            cps.append(cc)
        cc["base_url"] = proxy_url
        cc["api_key"] = cur.api_key or "ccswitch-empty"
        cc["model"] = virtual
        data["custom_providers"] = cps
        with open(yaml_p, "w", encoding="utf-8") as f:
            f.write(core.yaml_dump(data))
        print(f"✅ Hermes: {yaml_p}")

    # 4) 验证代理 3000 端口
    import urllib.request

    try:
        req = urllib.request.Request(
            f"{proxy_url}/chat/completions",
            data=json.dumps(
                {
                    "model": virtual,
                    "messages": [{"role": "user", "content": "ping"}],
                    "stream": False,
                }
            ).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer test",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=45) as r:
            body = r.read().decode("utf-8", errors="replace")
            print(f"✅ 代理 3000 测试 OK status={r.status} body={body[:200]}")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        print(f"❌ 代理 3000 HTTPERR code={e.code} body={body}")
    except Exception as e:
        print(f"❌ 代理 3000 连接失败: {type(e).__name__}: {e}")
        print("   请先启动 CC-Switch GUI（自动起代理到 3000）或 python main.py serve --port 3000")


if __name__ == "__main__":
    main()
