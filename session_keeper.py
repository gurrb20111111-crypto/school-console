# -*- coding: utf-8 -*-
"""
NEIS(나이스) / K-에듀파인 자동 로그아웃 방지 (세션 유지) 프로그램  [v2]
---------------------------------------------------------------
- 크롬을 '원격 디버깅 모드'로 띄운 뒤, 로그인된 탭의 세션을 주기적으로 갱신합니다.
- 화면 전환/포커스 가로채기 없이 백그라운드에서 조용히 동작합니다 (Chrome DevTools Protocol 사용).

[v2에서 바뀐 점 — 왜 예전엔 나이스가 그래도 종료됐나]
  · 나이스 4세대는 자기 JS가 setInterval 로 20분마다  POST /sessionExtension.do  를 호출해
    WAS 세션을 살리고, 카운트다운(1초 setInterval)이 10분 남으면 '연장하시겠습니까?' 창을 띄움.
  · 그런데 이 타이머들이 전부 setInterval 이라, 나이스 탭이 '백그라운드'로 가면 크롬이 탭을
    얼려서(freeze) 타이머가 멈춤 → /sessionExtension.do 가 안 나가고 → 세션 만료 → 강제 종료.
  · 예전 버전은 fetch(main.jsp) + 가짜 마우스만 보내서 '진짜 경로'(/sessionExtension.do)를
    전혀 건드리지 못했고, 그런데도 '갱신 완료'라고 찍어서 멀쩡해 보였음.

[v2의 해결]
  1) 매 주기마다 탭 freeze 를 해제 (Page.setWebLifecycleState=active)
  2) 나이스는 그 사이트 '자기 함수'와 똑같이  POST /sessionExtension.do  를 직접 호출
  3) 응답이 "Y" 일 때만 성공으로 기록 (정직한 로그). 아니면 경고.
  4) '연장하시겠습니까?' 확인창이 떠 있으면, 그 버튼 위치에 '신뢰된 클릭'을 보내 자동 연장.
"""

import json
import sys
import time
import urllib.request
from datetime import datetime, timedelta

try:
    from websocket import create_connection  # pip install websocket-client
except ImportError:
    print("[오류] websocket-client 모듈이 필요합니다.  설치:  pip install websocket-client")
    sys.exit(1)

# ============================ 설정 ============================
DEBUG_PORT = 9222          # start_chrome_debug.bat 의 포트와 동일해야 함
CHECK_INTERVAL_SEC = 300   # 세션 갱신 주기(초). 300=5분 (나이스 연장창 놓치지 않게 짧게)

# 유지할 사이트
#   mode="neis"    : 나이스 전용 (/sessionExtension.do 직접 호출 + 연장창 자동 확인)
#   mode="edupine" : 에듀파인 전용 (Nexacro '연장' 버튼에 '신뢰된 클릭' → 타이머 리셋)
#   mode="button"  : 화면의 '연장' 버튼을 글자로 찾아 DOM 클릭 (기타 사이트)
TARGETS = [
    {"name": "나이스(NEIS)", "match": "neis.go.kr",     "mode": "neis"},
    {"name": "K-에듀파인",    "match": "klef.sen.go.kr", "mode": "edupine"},
]

# '세션 연장' 버튼으로 인식할 텍스트 (button 모드용)
EXTEND_KEYWORDS = ["연장", "세션연장", "시간연장", "세션 연장", "시간 연장",
                   "연장하기", "계속사용", "계속 사용", "시간연장하기"]

USE_TOAST = True           # 문제 발생 시 윈도우 알림 표시 (plyer 설치 필요, 없으면 자동 무시)
# =============================================================


# ── 나이스 전용: 진짜 세션연장 호출 + 연장창 자동확인 (페이지 안에서 실행) ──
NEIS_KEEPALIVE_JS = """
(async function () {
  var out = { ok: false, body: '', dialog: false, btnX: 0, btnY: 0 };
  // 1) 나이스 자신이 쓰는 그대로 WAS 세션 연장 호출
  try {
    var r = await fetch('/sessionExtension.do',
                        { method: 'POST', credentials: 'include', cache: 'no-store' });
    out.body = (await r.text() || '').replace(/\\s+/g, ' ').trim();
    out.ok = (r.status === 200) && (out.body.indexOf('Y') !== -1);
  } catch (e) { out.body = 'ERR:' + e; }

  // 2) '접속시간을 연장하시겠습니까?' 확인창이 떠 있으면 '확인/예' 버튼 위치 찾기
  try {
    var dlgOpen = false;
    var texts = document.querySelectorAll('.cl-text');
    for (var i = 0; i < texts.length; i++) {
      var tt = (texts[i].innerText || '');
      if (/연장하시겠습니까|접속유지시간/.test(tt)) { dlgOpen = true; break; }
    }
    out.dialog = dlgOpen;
    if (dlgOpen) {
      var btns = document.querySelectorAll('.cl-button, button, a, .cl-text');
      for (var j = 0; j < btns.length; j++) {
        var t = (btns[j].innerText || btns[j].value || '').replace(/\\s+/g, ' ').trim();
        if (t === '확인' || t === '예' || t === '연장') {
          var b = btns[j].getBoundingClientRect();
          if (b.width > 0 && b.height > 0) {
            out.btnX = Math.round(b.left + b.width / 2);
            out.btnY = Math.round(b.top + b.height / 2);
            break;
          }
        }
      }
    }
  } catch (e) {}
  return JSON.stringify(out);
})();
"""

# ── 일반(button) 모드: '연장' 버튼 클릭 + 서버 GET (페이지 안에서 실행) ──
BUTTON_KEEPALIVE_JS = """
(function () {
  var result = { clicked: false, fetched: false, found: [] };
  var keywords = %KEYWORDS%;
  try {
    var els = document.querySelectorAll('a,button,input[type=button],input[type=submit],span,div,td,li');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var txt = (el.innerText || el.value || '').replace(/\\s+/g, ' ').trim();
      if (!txt || txt.length > 12) continue;
      if (keywords.indexOf(txt) !== -1) {
        try { el.click(); result.clicked = true; result.found.push(txt); } catch (e) {}
      }
    }
  } catch (e) {}
  try {
    fetch(window.location.href, { method: 'GET', cache: 'no-store', credentials: 'include' });
    result.fetched = true;
  } catch (e) {}
  return JSON.stringify(result);
})();
""".replace("%KEYWORDS%", json.dumps(EXTEND_KEYWORDS, ensure_ascii=False))


# ── 에듀파인(Nexacro) 전용: 타이머값 + '연장' 버튼 화면좌표 찾기 ──
#  · 에듀파인은 Nexacro 플랫폼이라 DOM el.click() 에 반응하지 않음.
#    버튼(div)의 화면 좌표를 찾아, 거기에 '신뢰된 CDP 클릭'을 보내야 실제로 눌림.
EDU_FIND_JS = """
(function () {
  var out = { timer: '', x: 0, y: 0 };
  var t = document.querySelector('[id$="staUseTime:text"]');       // 사용시간 카운트다운
  if (t) out.timer = (t.innerText || '').trim();
  var b = document.querySelector('[id$="form.btnUseTimeExtn"]');   // '연장' 버튼
  if (!b) {                                                        // 못 찾으면 '연장' 글자로 대체
    var all = document.querySelectorAll('div');
    for (var i = 0; i < all.length; i++) {
      if ((all[i].innerText || '').trim() === '연장') { b = all[i]; break; }
    }
  }
  if (b) {
    var r = b.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) {
      out.x = Math.round(r.left + r.width / 2);
      out.y = Math.round(r.top + r.height / 2);
    }
  }
  return JSON.stringify(out);
})();
"""


def _mmss_to_sec(s):
    """'mm:ss' → 초. 실패 시 -1."""
    try:
        m, ss = s.split(":")
        return int(m) * 60 + int(ss)
    except Exception:
        return -1


def log(msg):
    print("[{}] {}".format(datetime.now().strftime("%H:%M:%S"), msg))


def toast(title, message):
    if not USE_TOAST:
        return
    try:
        from plyer import notification
        notification.notify(title=title, message=message, timeout=5)
    except Exception:
        pass


def list_pages():
    url = "http://127.0.0.1:{}/json/list".format(DEBUG_PORT)
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


class CDP:
    """탭 1개의 DevTools 웹소켓과 통신하는 최소 클라이언트."""

    def __init__(self, ws_url):
        self.ws = create_connection(ws_url, timeout=15)
        self._id = 0

    def send(self, method, params=None, await_promise=False, timeout=15):
        self._id += 1
        mid = self._id
        p = dict(params or {})
        if method == "Runtime.evaluate":
            p.setdefault("returnByValue", True)
            if await_promise:
                p["awaitPromise"] = True
        self.ws.send(json.dumps({"id": mid, "method": method, "params": p}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data = json.loads(self.ws.recv())
            except Exception:
                break
            # 연장 시 네이티브 확인/알림 창이 떠서 멈추면 자동 수락
            if data.get("method") == "Page.javascriptDialogOpening":
                self.ws.send(json.dumps({"id": 999990, "method": "Page.handleJavaScriptDialog",
                                         "params": {"accept": True}}))
                continue
            if data.get("id") == mid:
                return data.get("result", {})
        return {}

    def eval_value(self, expr, await_promise=False):
        res = self.send("Runtime.evaluate", {"expression": expr}, await_promise=await_promise)
        try:
            return res.get("result", {}).get("value")
        except Exception:
            return None

    def trusted_click(self, x, y):
        """실제 커서는 안 움직이고, 그 좌표에 신뢰된 클릭을 보냄."""
        self.send("Input.dispatchMouseEvent", {"type": "mouseMoved",    "x": x, "y": y})
        self.send("Input.dispatchMouseEvent", {"type": "mousePressed",  "x": x, "y": y,
                                               "button": "left", "clickCount": 1})
        self.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y,
                                               "button": "left", "clickCount": 1})

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def keep_alive_neis(cdp, name):
    """나이스: /sessionExtension.do 직접 호출 + 연장창 자동 확인."""
    raw = cdp.eval_value(NEIS_KEEPALIVE_JS, await_promise=True)
    info = {}
    try:
        info = json.loads(raw or "{}")
    except Exception:
        pass

    # 연장창이 떠 있으면 그 버튼에 신뢰된 클릭 → 카운트다운 리셋
    if info.get("dialog") and info.get("btnX"):
        cdp.trusted_click(info["btnX"], info["btnY"])
        log("  → {} 연장 확인창 자동 클릭".format(name))
        # 한 번 더 연장 호출로 마무리
        cdp.eval_value("fetch('/sessionExtension.do',{method:'POST',credentials:'include',cache:'no-store'})")

    if info.get("ok"):
        log("  ✓ {} 세션연장 OK  (/sessionExtension.do = \"Y\")".format(name))
        return True
    else:
        body = info.get("body", "")
        log("  ✗ {} 세션연장 실패 — 서버응답: {}".format(name, body or "(없음)"))
        toast("세션 유지 프로그램", "{} 세션이 만료됐을 수 있습니다.\n응답: {}".format(name, body))
        return False


def keep_alive_edupine(cdp, name):
    """에듀파인(Nexacro): '연장' 버튼 화면좌표에 신뢰된 클릭 → 타이머가 최대치로 리셋."""
    info = {}
    try:
        info = json.loads(cdp.eval_value(EDU_FIND_JS) or "{}")
    except Exception:
        pass
    before = info.get("timer", "")
    x, y = info.get("x"), info.get("y")
    if not x:
        log("  ✗ {} '연장' 버튼을 찾지 못함 (에듀파인 탭/로그인 확인)".format(name))
        toast("세션 유지 프로그램", "{} 연장 버튼을 찾지 못했습니다.".format(name))
        return False

    cdp.trusted_click(x, y)        # ★ Nexacro는 이 신뢰된 클릭에만 반응
    time.sleep(1.0)

    after = ""
    try:
        after = json.loads(cdp.eval_value(EDU_FIND_JS) or "{}").get("timer", "")
    except Exception:
        pass

    # 정직한 판정:
    #  · 타이머가 위로 점프했으면(after>before) 연장 성공
    #  · 이미 30분 이상 남아있었으면(만땅 근처) 클릭이 무효타여도 세션은 건강 → 성공으로 봄
    #  · 둘 다 아니면(클릭이 안 먹혀 계속 줄고 있음) 실패
    b_sec, a_sec = _mmss_to_sec(before), _mmss_to_sec(after)
    if (a_sec > b_sec) or (b_sec >= 30 * 60):
        log("  ✓ {} 세션연장 OK  ({} → {})".format(name, before, after))
        return True
    else:
        log("  ✗ {} 연장이 안 먹힘 — 타이머 계속 감소  ({} → {})".format(name, before, after))
        toast("세션 유지 프로그램", "{} 연장이 적용되지 않았습니다.".format(name))
        return False


def keep_alive_button(cdp, name):
    """에듀파인 등: 화면의 '연장' 버튼 클릭 + 서버 GET."""
    raw = cdp.eval_value(BUTTON_KEEPALIVE_JS)
    cdp.pump(1.0)
    info = {}
    try:
        info = json.loads(raw or "{}")
    except Exception:
        pass
    detail = []
    if info.get("clicked"):
        detail.append("연장버튼({})".format(",".join(info.get("found", []))))
    if info.get("fetched"):
        detail.append("서버요청")
    log("  ✓ {} 갱신 {}".format(name, ("- " + ", ".join(detail)) if detail else "(연장버튼 없음)"))
    return True


def keep_alive(page, target):
    name = target["name"]
    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        return False
    cdp = CDP(ws_url)
    try:
        cdp.send("Page.enable")
        # ★ 핵심: 백그라운드로 얼어붙은 탭을 깨운다 (이게 없으면 타이머/연장이 멈춰 있음)
        cdp.send("Page.setWebLifecycleState", {"state": "active"})

        # 신뢰된 입력 이벤트로 클라이언트 유휴감지도 한 번 리셋 (실제 커서는 안 움직임)
        cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 7, "y": 7})

        mode = target.get("mode")
        if mode == "neis":
            return keep_alive_neis(cdp, name)
        elif mode == "edupine":
            return keep_alive_edupine(cdp, name)
        else:
            return keep_alive_button(cdp, name)
    except Exception as e:
        log("  ✗ {} 갱신 실패: {}".format(name, e))
        return False
    finally:
        cdp.close()


# button 모드에서 늦게 뜨는 네이티브 확인창 처리용 (CDP 에 pump 추가)
def _pump(self, seconds):
    end = time.time() + seconds
    self.ws.settimeout(0.4)
    while time.time() < end:
        try:
            data = json.loads(self.ws.recv())
        except Exception:
            continue
        if data.get("method") == "Page.javascriptDialogOpening":
            self.ws.send(json.dumps({"id": 999991, "method": "Page.handleJavaScriptDialog",
                                     "params": {"accept": True}}))
    try:
        self.ws.settimeout(15)
    except Exception:
        pass
CDP.pump = _pump


def find_targets():
    pages = list_pages()
    out = []
    for t in TARGETS:
        match = None
        for p in pages:
            if p.get("type") == "page" and t["match"] in (p.get("url") or ""):
                match = p
                break
        out.append((t, match))
    return out


def main():
    print("=" * 60)
    print(" NEIS(나이스) / K-에듀파인  자동 로그아웃 방지 프로그램  [v2]")
    print(" 갱신 주기: {}분   |   디버그 포트: {}".format(CHECK_INTERVAL_SEC // 60, DEBUG_PORT))
    print(" 중지하려면  Ctrl + C")
    print("=" * 60)

    try:
        list_pages()
    except Exception:
        log("크롬 디버그 포트({})에 연결할 수 없습니다.".format(DEBUG_PORT))
        log("→ 먼저 디버그 모드 크롬(프로필_최초설정/AHK)으로 실행했는지,")
        log("→ 그리고 나이스를 '그 크롬 창'에서 열었는지 확인하세요.")
        toast("세션 유지 프로그램", "크롬 디버그 모드가 실행되어 있지 않습니다.")
        sys.exit(1)

    while True:
        log("세션 점검 시작")
        for t, page in find_targets():
            if page is None:
                log("  - {} 탭을 찾을 수 없음 (이 크롬 창에서 해당 사이트를 열고 로그인했는지 확인)".format(t["name"]))
                toast("세션 유지 프로그램", "{} 탭이 (디버그 크롬에) 열려 있지 않습니다.".format(t["name"]))
                continue
            keep_alive(page, t)

        nxt = (datetime.now() + timedelta(seconds=CHECK_INTERVAL_SEC)).strftime("%H:%M:%S")
        log("다음 점검: {}분 후 ({})\n".format(CHECK_INTERVAL_SEC // 60, nxt))
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n종료합니다.")
