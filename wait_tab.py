# -*- coding: utf-8 -*-
"""
디버그 크롬(9222)에 '지정한 주소의 탭이 다 열릴 때까지' 기다린다.
------------------------------------------------------------------
사용: python wait_tab.py <주소 일부> [최대 대기 초=20]
  예) python wait_tab.py neis.go.kr/jsp/main.jsp 20
      python wait_tab.py ealimi.com 15

왜 필요한가:
  로그인 자동화가 나이스 → 에듀파인 → E알리미 → 콘솔을 '정해진 초'만 쉬고 연달아 열면,
  PC가 느린 날에는 앞 창이 다 뜨기도 전에 다음 창이 열려 버린다.
  그래서 "앞 창이 실제로 다 열렸는지" 확인한 뒤에 다음으로 넘어가게 한다.

다 열리면 0, 시간 안에 안 열리면 1.  (AHK는 어느 쪽이든 다음 단계로 진행한다)
"""
import sys, json, time, urllib.request

PORT = 9222
MATCH = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    MAX_SEC = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
except ValueError:
    MAX_SEC = 20.0


def find_page():
    data = json.loads(urllib.request.urlopen(
        "http://127.0.0.1:%d/json/list" % PORT, timeout=3).read().decode())
    for p in data:
        if p.get("type") == "page" and MATCH in (p.get("url") or ""):
            return p
    return None


def ready_state(page):
    """탭이 다 읽혔는지(document.readyState). 확인할 수 없으면 None."""
    try:
        from websocket import create_connection
    except ImportError:
        return None
    ws = None
    try:
        ws = create_connection(page["webSocketDebuggerUrl"], timeout=3)
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": "document.readyState", "returnByValue": True}}))
        end = time.time() + 3
        while time.time() < end:
            d = json.loads(ws.recv())
            if d.get("id") == 1:
                return (d.get("result", {}).get("result", {}) or {}).get("value")
    except Exception:
        return None
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
    return None


def main():
    if not MATCH:
        sys.exit(1)
    deadline = time.time() + MAX_SEC
    seen_at = None
    while time.time() < deadline:
        page = None
        try:
            page = find_page()
        except Exception:
            page = None
        if page:
            if seen_at is None:
                seen_at = time.time()
            state = ready_state(page)
            if state == "complete":
                sys.exit(0)
            # 상태를 못 읽는 환경이면, 탭이 보인 뒤 3초를 '다 열림'으로 본다
            if state is None and time.time() - seen_at >= 3:
                sys.exit(0)
        time.sleep(0.4)
    sys.exit(1)


if __name__ == "__main__":
    main()
