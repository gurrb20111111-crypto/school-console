# -*- coding: utf-8 -*-
"""
업무포털 로그인 버튼을 "글자 텍스트"로 찾아 클릭 (좌표 무관)
- 크롬 디버그 포트(9222)에 붙어, 해당 글자가 든 요소의 위치를 찾고
  그 위치에 신뢰된 마우스 클릭을 보냅니다. 창이 움직여도 정확히 눌립니다.
- 성공 시 종료코드 0, 실패 시 1 (AHK 에서 실패하면 좌표클릭으로 대체)
"""
import sys, json, time, urllib.request

try:
    from websocket import create_connection
except ImportError:
    print("websocket-client 없음"); sys.exit(1)

PORT = 9222
TARGET_TEXT = "전자서명 인증서 로그인"   # 이 글자가 포함된 버튼을 클릭
URL_MATCH   = "eduptl.kr"                # 이 문자열이 든 탭에서 찾기


def list_pages():
    with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % PORT, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def find_page(substr):
    for p in list_pages():
        if p.get("type") == "page" and substr in (p.get("url") or ""):
            return p
    return None


def send(ws, mid, method, params):
    ws.send(json.dumps({"id": mid, "method": method, "params": params}))
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            data = json.loads(ws.recv())
        except Exception:
            break
        if data.get("id") == mid:
            return data.get("result", {})
    return {}


FIND_JS = r"""
(function(){
  var target = %TARGET%;
  var nodes = document.querySelectorAll('a,button,input,span,div,li,td');
  for (var i=0;i<nodes.length;i++){
    var el = nodes[i];
    var t = (el.innerText||el.value||'').replace(/\s+/g,' ').trim();
    if (t && t.indexOf(target)!==-1 && t.length<40){
      el.scrollIntoView({block:'center'});
      var r = el.getBoundingClientRect();
      if (r.width>0 && r.height>0)
        return JSON.stringify({x:r.left+r.width/2, y:r.top+r.height/2, text:t});
    }
  }
  return JSON.stringify({error:'not found'});
})();
""".replace("%TARGET%", json.dumps(TARGET_TEXT, ensure_ascii=False))


def main():
    try:
        page = find_page(URL_MATCH)
    except Exception as e:
        print("디버그 포트 연결 실패:", e); sys.exit(1)
    if not page:
        print("업무포털 탭을 찾지 못함"); sys.exit(1)

    ws = create_connection(page["webSocketDebuggerUrl"], timeout=10)
    try:
        res = send(ws, 1, "Runtime.evaluate", {"expression": FIND_JS, "returnByValue": True})
        val = res.get("result", {}).get("value", "")
        info = json.loads(val) if val else {}
        if "error" in info or "x" not in info:
            print("버튼을 찾지 못함:", info); sys.exit(1)

        cx, cy = info["x"], info["y"]
        send(ws, 2, "Input.dispatchMouseEvent", {"type": "mouseMoved",    "x": cx, "y": cy})
        send(ws, 3, "Input.dispatchMouseEvent", {"type": "mousePressed",  "x": cx, "y": cy, "button": "left", "clickCount": 1})
        send(ws, 4, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1})
        print("클릭 성공:", info.get("text"))
        sys.exit(0)
    finally:
        ws.close()


if __name__ == "__main__":
    main()
