# -*- coding: utf-8 -*-
"""
업무포털 상단 메뉴(나이스 / K-에듀파인)를 좌표·팝업차단 무관하게 실행.
------------------------------------------------------------------
사용: python portal_menu.py nais       (나이스)
      python portal_menu.py edufine    (K-에듀파인)

방식(견고): 상단 실행 메뉴 <a class="menuBtn">[나이스]</a> 의 id에는
  SSO 로그인 URL(sen.neis.go.kr/cmc_fcm_lg01_000.do?data=토큰)이 박혀 있다.
  → 그 URL을 꺼내 '새 탭으로 직접 열기'.  (window.open 팝업차단을 완전 우회)
  ※ 메뉴를 그냥 클릭하면 openNeisDlivPop()이 window.open으로 새 창을 여는데
    비동기라 사용자 제스처가 풀려 팝업차단에 막힘 → 그래서 URL 직접열기가 정답.
  ※ 나이스는 URL(main.jsp) 직접열기론 SSO 안 되지만, 이 'cmc_fcm_lg01' URL은 SSO 됨.

성공 → 0, 실패 → 1  (AHK는 실패 시 예전 좌표 클릭으로 폴백)
"""
import sys, os, json, time, subprocess, urllib.request

try:
    from websocket import create_connection
except ImportError:
    print("websocket-client 없음"); sys.exit(1)

PORT = 9222
URL_MATCH = "eduptl.kr"
PROFILE = os.path.join(os.environ.get("LOCALAPPDATA", ""), "SenKeepAliveProfile")

TARGET = (sys.argv[1] if len(sys.argv) > 1 else "nais").lower()
NAMES = {
    "nais": ["나이스"], "neis": ["나이스"],
    "edufine": ["K에듀파인", "에듀파인"], "kef": ["K에듀파인", "에듀파인"],
}.get(TARGET, ["나이스"])

# 업무포털 '전달사항 공지' 팝업 닫기 (있으면) — 사용자 편의
CLOSE_JS = r"""
(function(){
  document.querySelectorAll('.pop-bottom-close, button.pop-bottom-close').forEach(function(b){ try{ b.click(); }catch(e){} });
  document.querySelectorAll('.modal-bg, [class*="modal-bg"]').forEach(function(m){ try{ m.style.display='none'; }catch(e){} });
  return 'ok';
})();
"""

# 메뉴 요소의 SSO URL(id) + 좌표 추출
FIND_JS = """
(function(){
  var NAMES=%s;
  function strip(s){return (s||"").replace(/[\\[\\]\\s\\-]/g,"");}
  function vis(el){try{var r=el.getBoundingClientRect();return r.width>1&&r.height>1&&el.offsetParent!==null;}catch(e){return false;}}
  function match(el){var raw=strip(el.innerText); for(var k=0;k<NAMES.length;k++){ if(raw===strip(NAMES[k])) return true; } return false;}
  var el=null;
  var cands=document.querySelectorAll("a.menuBtn");
  for(var i=0;i<cands.length && !el;i++){ if(vis(cands[i]) && match(cands[i])) el=cands[i]; }
  if(!el){
    var all=document.querySelectorAll("a,button,li,span");
    for(var j=0;j<all.length && !el;j++){ if(!vis(all[j])) continue;
      if(/광장|지원|원격|공지|알림/.test(all[j].innerText||"")) continue;
      if(match(all[j])) el=all[j];
    }
  }
  if(!el) return JSON.stringify({error:"not-found"});
  var url=(el.id||"");
  try{ el.scrollIntoView({block:"center"}); }catch(e){}
  var r=el.getBoundingClientRect();
  return JSON.stringify({url:(url.indexOf("http")===0?url:""),
                         x:Math.round(r.left+r.width/2), y:Math.round(r.top+r.height/2),
                         text:(el.innerText||"").replace(/\\s+/g," ").trim().slice(0,14)});
})();
""" % json.dumps(NAMES, ensure_ascii=False)


def find_chrome():
    for c in [os.path.join(os.environ.get("ProgramFiles", ""), r"Google\Chrome\Application\chrome.exe"),
              os.path.join(os.environ.get("ProgramFiles(x86)", ""), r"Google\Chrome\Application\chrome.exe"),
              os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe")]:
        if c and os.path.exists(c):
            return c
    return "chrome.exe"


def find_page():
    data = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/json/list" % PORT, timeout=5).read().decode())
    for p in data:
        if p.get("type") == "page" and URL_MATCH in (p.get("url") or ""):
            return p
    return None


def send(ws, mid, method, params):
    ws.send(json.dumps({"id": mid, "method": method, "params": params}))
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            d = json.loads(ws.recv())
        except Exception:
            break
        if d.get("id") == mid:
            return d.get("result", {})
    return {}


def main():
    page = None
    for _ in range(6):
        try:
            page = find_page()
        except Exception as e:
            print("디버그 포트 연결 실패:", e)
        if page:
            break
        time.sleep(1)
    if not page:
        print("업무포털 탭을 찾지 못함"); sys.exit(1)

    ws = create_connection(page["webSocketDebuggerUrl"], timeout=10)
    try:
        send(ws, 8, "Runtime.evaluate", {"expression": CLOSE_JS, "returnByValue": True})   # 팝업 닫기
        res = send(ws, 1, "Runtime.evaluate", {"expression": FIND_JS, "returnByValue": True})
        info = json.loads(res.get("result", {}).get("value", "") or "{}")
        url = info.get("url", "")
        if url.startswith("http"):
            subprocess.Popen([find_chrome(), "--user-data-dir=" + PROFILE, url])
            print("SSO URL 직접 열기:", info.get("text"))
            sys.exit(0)
        # 폴백: SSO URL을 못 얻으면 좌표 신뢰클릭
        if info.get("x"):
            cx, cy = info["x"], info["y"]
            send(ws, 2, "Input.dispatchMouseEvent", {"type": "mouseMoved", "x": cx, "y": cy})
            send(ws, 3, "Input.dispatchMouseEvent", {"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1})
            send(ws, 4, "Input.dispatchMouseEvent", {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1})
            print("메뉴 신뢰클릭(폴백):", info.get("text"))
            sys.exit(0)
        print("메뉴 못 찾음:", info); sys.exit(1)
    finally:
        ws.close()


if __name__ == "__main__":
    main()
