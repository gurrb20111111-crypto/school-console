# -*- coding: utf-8 -*-
"""
EVPN(서울특별시교육청 원격업무지원) 자동 로그인 + VPN 연결 대기
---------------------------------------------------------------
- 디버그 크롬(포트 9222)의 evpn.sen.go.kr 탭에 붙어:
    1) ID/PW 입력칸(#username/#password)에 값을 넣고 로그인 버튼(#loginButton) 클릭
    2) '연결' 상태(#connect_status)가 "Yes"가 될 때까지 대기 (VPN 연결 완료 감지)
- 성공(연결 Yes) → 종료코드 0,  실패/시간초과 → 종료코드 1
  (AHK가 0일 때만 다음 단계: 업무포털 인증서 로그인으로 진행)

※ 아래 EVPN_ID / EVPN_PW 두 줄을 본인 정보로 바꿔주세요.
"""
import sys, json, time, re, urllib.request

try:
    from websocket import create_connection
except ImportError:
    print("websocket-client 없음 (pip install websocket-client)"); sys.exit(1)

# ====================== 본인 정보 (여기만 수정) ======================
EVPN_ID = ""          # ← EVPN 로그인 ID
EVPN_PW = ""        # ← EVPN 로그인 비밀번호
# ===================================================================

PORT = 9222
URL_MATCH = "evpn.sen.go.kr"
RUNNING_TIMEOUT = 40    # '실행 Yes'(AXGATE 클라이언트 구동) 대기 최대 초
CONNECT_TIMEOUT = 90    # '연결 Yes'(VPN 연결) 대기 최대 초


def list_pages():
    return json.loads(urllib.request.urlopen("http://127.0.0.1:%d/json/list" % PORT, timeout=5).read().decode())


def find_page():
    for p in list_pages():
        if p.get("type") == "page" and URL_MATCH in (p.get("url") or ""):
            return p
    return None


class CDP:
    def __init__(self, ws_url):
        self.ws = create_connection(ws_url, timeout=10)
        self._id = 0
        self.dlg_count = 0        # 자동 '확인' 처리한 JS 경고창 수
        self.last_dialog = ""     # 마지막으로 뜬 경고창 문구(실패 원인 파악용)
        # Page 도메인을 켜야 alert/confirm 창을 CDP로 받아 자동 처리 가능
        try:
            self._send("Page.enable")
        except Exception:
            pass

    def _send(self, method, params=None):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        return mid

    def _handle_dialog(self, msg):
        """JS 경고창(alert/confirm)이 뜨면 자동으로 '확인'(accept) 처리."""
        if msg.get("method") == "Page.javascriptDialogOpening":
            self.last_dialog = msg.get("params", {}).get("message", "") or self.last_dialog
            try:
                self._send("Page.handleJavaScriptDialog", {"accept": True})
                self.dlg_count += 1
            except Exception:
                pass
            return True
        return False

    def ev(self, expr):
        mid = self._send("Runtime.evaluate", {"expression": expr, "returnByValue": True})
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                d = json.loads(self.ws.recv())
            except Exception:
                break
            self._handle_dialog(d)          # 대기 중 경고창 뜨면 즉시 확인
            if d.get("id") == mid:
                return d.get("result", {}).get("result", {}).get("value")
        return None

    def pump(self, seconds):
        """지정 시간 동안 들어오는 이벤트를 읽으며 경고창만 자동 확인 (sleep 대용)."""
        end = time.time() + seconds
        old = None
        try:
            old = self.ws.gettimeout()
            self.ws.settimeout(0.5)
        except Exception:
            pass
        while time.time() < end:
            try:
                d = json.loads(self.ws.recv())
            except Exception:
                continue
            self._handle_dialog(d)
        try:
            self.ws.settimeout(old)
        except Exception:
            pass

    def trusted_click(self, x, y):
        """실제 사람 클릭과 동일한 '신뢰된(trusted)' 마우스 클릭을 좌표로 전송."""
        self._send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        self._send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y,
                                                "button": "left", "clickCount": 1})
        self._send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y,
                                                "button": "left", "clickCount": 1})

    def press_enter(self):
        """포커스된 입력칸에 신뢰된 Enter 키 입력(폼 submit 유도)."""
        base = {"key": "Enter", "code": "Enter",
                "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13}
        self._send("Input.dispatchKeyEvent", dict(base, type="keyDown"))
        self._send("Input.dispatchKeyEvent", dict(base, type="keyUp"))

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


FILL_JS = """
(function(){
  var u=document.getElementById('username');
  var p=document.getElementById('password');
  if(!u||!p) return JSON.stringify({status:'no-fields'});
  function set(el,val){
    el.focus(); el.value=val;
    el.dispatchEvent(new Event('input',{bubbles:true}));
    el.dispatchEvent(new Event('change',{bubbles:true}));
  }
  set(u,%s); set(p,%s);
  p.focus();
  // 로그인 버튼: id 우선 → 흔한 셀렉터 → 텍스트('로그인'/'LOGIN') 매칭
  var b=document.getElementById('loginButton')
     || document.querySelector('#btnLogin,#loginBtn,.btn_login,button[type=submit],input[type=submit]');
  if(!b){
    var cand=[].slice.call(document.querySelectorAll('a,button,input,span,div'));
    for(var i=0;i<cand.length;i++){
      var t=(cand[i].innerText||cand[i].value||'').replace(/\\s+/g,'');
      if(/^(로그인|LOGIN)$/i.test(t)){ b=cand[i]; break; }
    }
  }
  var out={status:'ok', found:false};
  if(b){
    var r=b.getBoundingClientRect();
    out.found=true;
    out.x=Math.round(r.left+r.width/2);
    out.y=Math.round(r.top+r.height/2);
  }
  return JSON.stringify(out);
})();
""" % (json.dumps(EVPN_ID, ensure_ascii=False), json.dumps(EVPN_PW, ensure_ascii=False))

STATUS_JS = """
(function(){
  function v(id){var e=document.getElementById(id); return e?((e.innerText||e.textContent||'').trim()):'';}
  return JSON.stringify({install:v('install_status'), running:v('running_status'), connect:v('connect_status')});
})();
"""


def status(cdp):
    try:
        return json.loads(cdp.ev(STATUS_JS) or "{}")
    except Exception:
        return {}


def submit_login(cdp):
    """아이디/비번을 채우고 로그인 트리거(버튼이면 신뢰된 클릭, 없으면 Enter). info dict 반환."""
    try:
        info = json.loads(cdp.ev(FILL_JS) or "{}")
    except Exception:
        info = {}
    if info.get("status") != "ok":
        return info
    if info.get("found") and info.get("x") is not None:
        cdp.trusted_click(info["x"], info["y"])   # 사람 클릭과 동일 → 합성 클릭 무시하는 사이트 대응
    else:
        cdp.press_enter()                          # 버튼 못 찾으면 Enter로 폼 submit
    return info


def main():
    page = None
    for _ in range(20):
        try:
            page = find_page()
        except Exception as e:
            print("디버그 포트 연결 실패:", e)
        if page:
            break
        time.sleep(1)
    if not page:
        print("EVPN 탭을 찾지 못했습니다. (디버그 크롬에서 evpn.sen.go.kr를 열었는지 확인)")
        sys.exit(1)

    cdp = CDP(page["webSocketDebuggerUrl"])
    try:
        # 1) '실행 Yes'(AXGATE 구동) 대기 — 이미 Yes면 바로 통과
        end = time.time() + RUNNING_TIMEOUT
        while time.time() < end:
            if status(cdp).get("running", "").lower() == "yes":
                break
            cdp.pump(1)

        # 2) ID/PW 입력 + 로그인 (신뢰된 클릭으로 트리거)
        info = submit_login(cdp)
        print("로그인 시도:", info)
        if info.get("status") == "no-fields":
            print("로그인 폼(아이디/비번칸)을 찾지 못했습니다.")
            sys.exit(1)
        if not info.get("found"):
            print("로그인 버튼을 못 찾아 Enter로 시도했습니다.")

        # 2-1) 로그인 직후 뜨는 '아이디/비밀번호 확인' 경고창 자동 확인
        #      (사람이 '확인' 누르던 동작 대신. 뜨면 로그인 한 번 재시도)
        before = cdp.dlg_count
        cdp.pump(3)
        if cdp.dlg_count > before:
            print("검증 경고창 자동 확인 → 재로그인")
            submit_login(cdp)
            cdp.pump(2)

        # 2-2) 계정 만료/불일치 등 '오류' 경고창이면 90초 기다리지 말고 즉시 실패 + 이유 표시
        if cdp.last_dialog and re.search(r"만료|실패|일치하지|잘못|차단|권한|없습니다|불가", cdp.last_dialog):
            print("EVPN 로그인 실패:", cdp.last_dialog)
            if "만료" in cdp.last_dialog:
                print("→ EVPN 사용기간 만료입니다. 나이스에서 '원격업무지원서비스(EVPN)'를 재신청/연장하세요.")
            sys.exit(1)

        # 3) '연결 Yes'(VPN 연결) 대기 (대기 중에도 경고창 자동 확인)
        end = time.time() + CONNECT_TIMEOUT
        while time.time() < end:
            st = status(cdp)
            if st.get("connect", "").lower() == "yes":
                print("VPN 연결 완료 (연결 Yes). 다음 단계로 진행합니다.")
                sys.exit(0)
            cdp.pump(2)

        print("시간초과: '연결 Yes'가 되지 않았습니다. 마지막 상태:", status(cdp))
        sys.exit(1)
    finally:
        cdp.close()


if __name__ == "__main__":
    main()
