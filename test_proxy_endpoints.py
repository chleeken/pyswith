"""验证代理到 NVIDIA 的完整链路。"""
import json
import urllib.request
import threading
import time
from core import ProviderManager, ProxyState, create_proxy_http_server


def main():
    mgr = ProviderManager()
    state = ProxyState(mgr)
    state.set_virtual_model("virtual-model")
    httpd = create_proxy_http_server("127.0.0.1", 8789, state)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    time.sleep(0.5)

    for ep, payload in [
        ("chat/completions", {"model": "virtual-model", "messages": [{"role": "user", "content": "ping"}], "stream": False}),
        ("responses", {"model": "virtual-model", "input": "ping", "stream": False}),
    ]:
        req = urllib.request.Request(
            f"http://127.0.0.1:8789/v1/{ep}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer test"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                body = r.read().decode("utf-8", errors="replace")[:400]
                print(f"✅ /v1/{ep} OK status={r.status}")
                print(f"   body={body}")
        except urllib.error.HTTPError as e:
            try:
                body = e.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                body = ""
            print(f"❌ /v1/{ep} HTTPERR code={e.code} body={body}")
        except Exception as e:
            print(f"❌ /v1/{ep} ERR {type(e).__name__}: {e}")

    httpd.shutdown()


if __name__ == "__main__":
    main()
