# -*- coding: utf-8 -*-
"""
gian_draft.py — K-에듀파인 '반복 기안' 초안 도우미
=====================================================================
예전에 올린 기안문을 [재작성]으로 다시 열고, 바뀌는 부분(사안번호·일시·장소·
관련 문서번호 등)만 찾아 바꿔서 '결재올림 직전' 상태로 만들어 둔다.
붙임 파일도 새 사안 파일로 바꿔 끼운다(원본 첨부는 재작성 때 그대로 복사돼 옴).
마지막 확인과 [결재올림]은 반드시 사람이 한다. 이 모듈은 결재올림(fncSanctn),
임시저장(fncSaveTmpr), 닫기(fncClose)를 호출하지 않는다.

구조 (2026-11-24 실측)
  1) 크롬(CDP 9222) 쪽: klef.sen.go.kr/keris_ui/main.do 안의 iframe
     'retrieveDrftComptDocList.do'(기안한문서)에 문서 목록 + [재작성] 버튼이 있다.
     [재작성] = 행 체크 후 fncDoCallGian('', DRAFTMODE_SERVEROPEN).
  2) 기안기는 크롬이 아니라 별도 프로그램 WXSClient.exe(IE 엔진, 32비트)로 뜬다.
     창 제목 '재작성_', 안쪽 'Internet Explorer_Server' 창에서
     WM_HTML_GETOBJECT → ObjectFromLresult 로 DOM(IHTMLDocument2)을 얻는다.
  3) 기안기 DOM:  제목 = input#Sj
                  본문 = frames[0] 의 #editor1 (한컴 HwpCtrl, 누름틀: 결재제목·본문·수신…)
                         frames[1] 의 #editor2 (같은 내용의 보조 편집기 — 같이 바꿔 둔다)
     본문 치환은 HwpCtrl 'AllReplace' 액션 → 표·서식이 그대로 유지된다.

필요 패키지: comtypes (pip install comtypes)
"""

import ctypes
import ctypes.wintypes as W
import json
import re
import time
from datetime import datetime

try:
    import comtypes
    import comtypes.client
    from comtypes import GUID
    from comtypes.automation import IDispatch
    HAS_COM = True
except Exception:  # comtypes 미설치
    HAS_COM = False

# 이 모듈이 절대 부르지 않을 기안기 함수 (안전장치: 실행할 JS에 들어 있으면 거부)
FORBIDDEN_JS = ("fncSanctn", "fncSaveTmpr", "fncClose", "docClose", "fncReportCours",
                "fncRcverAppn", "fncDeleteFile", "submit(")

EDUFINE_MATCH = "klef.sen.go.kr"
DRFT_LIST_KEY = "retrieveDrftComptDocList"     # 기안한문서 iframe 주소의 일부
WXS_TITLE_KEY = "재작성"                        # 재작성 기안기 창 제목


# ───────────────────────── 1) 크롬 쪽: 기안한문서 목록 / 재작성 열기 ─────────────────────────

_FIND_FRAME_JS = r"""
  function __gdFrame(){
    for (var i=0;i<window.frames.length;i++){
      try { if ((window.frames[i].location.href||'').indexOf('%KEY%')>=0) return window.frames[i]; } catch(e){}
    }
    return null;
  }
""".replace("%KEY%", DRFT_LIST_KEY)

LIST_JS = "(function(){" + _FIND_FRAME_JS + r"""
  var w=__gdFrame(); if(!w) return JSON.stringify({err:'noframe'});
  var d=w.document; var f=d.forms[0]; var obj=f && f.docinfo;
  if(!obj) return JSON.stringify({err:'nolist'});
  if(obj.length===undefined) obj=[obj];
  var rows=[];
  for(var i=0;i<obj.length;i++){
    var tr=obj[i].closest('tr'); if(!tr) continue;
    var tds=tr.querySelectorAll('td');
    var cells=[].map.call(tds,function(td){return (td.innerText||'').replace(/\s+/g,' ').trim();});
    // 열: [체크, 상태, 문서번호, 제목, 결재권자, 결재완료일시, 수신자, 연계유형]
    var docno='', title='', who='', done='', rcv='', kind='';
    for(var c=0;c<cells.length;c++){ if(/^[^\s]+-\d+$/.test(cells[c])){ docno=cells[c]; title=cells[c+1]||''; who=cells[c+2]||''; done=cells[c+3]||''; rcv=cells[c+4]||''; kind=cells[c+5]||''; break; } }
    var v=(obj[i].value||'').split(',');
    rows.push({idx:i, docno:docno, title:title, who:who, done:done, rcv:rcv, kind:kind,
               elec: v.length>21 ? v[21] : ''});
  }
  var total=''; try{ var m=(d.body.innerText||'').match(/총\s*(\d+)\s*건/); if(m) total=m[1]; }catch(e){}
  return JSON.stringify({rows:rows, total:total});
})()"""

OPEN_JS_TMPL = "(function(){" + _FIND_FRAME_JS + r"""
  var w=__gdFrame(); if(!w) return 'noframe';
  var obj=w.document.forms[0].docinfo; if(!obj) return 'nolist';
  if(obj.length===undefined) obj=[obj];
  var want=%DOCNO%, idx=-1;
  for(var i=0;i<obj.length;i++){ var tr=obj[i].closest('tr'); if(tr && tr.innerText.indexOf(want)>=0){ idx=i; break; } }
  if(idx<0) return 'notfound';
  var keep=[]; for(var j=0;j<obj.length;j++){ keep.push(obj[j].checked); obj[j].checked=false; }
  obj[idx].checked=true;
  try { w.fncDoCallGian('', w.DRAFTMODE_SERVEROPEN); }
  finally { for(var k=0;k<obj.length;k++) obj[k].checked=keep[k]; }
  return 'ok';
})()"""


# [기안한문서] 화면이 안 열려 있으면 에듀파인 메뉴를 코드로 연다 (메뉴 클릭과 같은 gfnOpenMenu, 읽기 화면이라 안전).
#  메뉴 ID는 학교마다 다를 수 있어서 메뉴 목록(gdsMenu)에서 화면 주소로 찾는다.
DRFT_STATE_JS = "(function(){" + _FIND_FRAME_JS + r"""
  var w=__gdFrame(); if(!w) return 'none';
  try { var d=w.document; if(d.readyState!=='complete') return 'loading';
        if(d.forms[0] && d.forms[0].docinfo) return 'ready';
        if(/총\s*\d+\s*건/.test(d.body.innerText||'')) return 'ready'; } catch(e){ return 'loading'; }
  return 'loading';
})()"""

DRFT_OPEN_MENU_JS = r"""(function(){
  try { var a=nexacro.getApplication(); var ds=a.gdsMenu; ds.filter('');
    var col=a.gv_progrmCoursAdresCol||'progrmCoursAdres', id=null;
    for(var r=0;r<ds.getRowCount();r++){ var u=ds.getColumn(r,col)||'';
      if(/\/retrieveDrftComptDocList\.do$/.test(u)){ id=ds.getColumn(r,a.gv_menuIdCol||'menuId'); break; } }
    if(!id) return 'nomenu';
    a.mainframe.MainVFrameSet.TopFrame.form.gfnOpenMenu(id);
    return 'ok';
  } catch(e){ return 'err ' + e; }
})()"""


def ensure_drafted_frame(cdp, timeout=20):
    """기안한문서 화면이 없으면 메뉴로 열고 목록이 뜰 때까지 기다린다. → None(준비됨) 또는 안내 문구."""
    st = cdp.eval_value(DRFT_STATE_JS)
    if st == "ready":
        return None
    if st == "none":
        r = cdp.eval_value(DRFT_OPEN_MENU_JS) or "?"
        if r != "ok":
            return ("에듀파인 메뉴에서 [기안한문서]를 못 찾았습니다 — 로그인 상태를 확인하거나 "
                    "[문서관리 > 내문서함 > 기안한문서]를 직접 열어 주세요 ({})".format(r))
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.7)
        if cdp.eval_value(DRFT_STATE_JS) == "ready":
            time.sleep(0.5)
            return None
    return ("[기안한문서] 화면이 {}초 안에 뜨지 않았습니다 — 에듀파인 창(열린 화면 개수 제한 안내 등)을 "
            "확인해 주세요".format(timeout))


def _edufine_cdp(port, find_target_page, CDP):
    page = find_target_page(port, EDUFINE_MATCH)
    if not page or not page.get("webSocketDebuggerUrl"):
        return None
    return CDP(page["webSocketDebuggerUrl"])


def read_drafted_list(port, find_target_page, CDP):
    """기안한문서 목록 읽기 → (rows, status)."""
    cdp = _edufine_cdp(port, find_target_page, CDP)
    if cdp is None:
        return None, "K-에듀파인 탭이 없습니다 (디버그 크롬에서 에듀파인 로그인 필요)"
    try:
        cdp.send("Page.setWebLifecycleState", {"state": "active"})
        why = ensure_drafted_frame(cdp)
        if why:
            return None, why
        res = json.loads(cdp.eval_value(LIST_JS) or "{}")
    finally:
        cdp.close()
    if res.get("err") == "noframe":
        return None, "[기안한문서] 화면을 열지 못했습니다 — 에듀파인에서 [문서관리 > 내문서함 > 기안한문서]를 직접 열어 주세요"
    if res.get("err"):
        return None, "기안한문서 목록을 못 읽음 ({})".format(res["err"])
    return res.get("rows", []), "ok (화면 {}건 / 전체 {}건)".format(len(res.get("rows", [])), res.get("total") or "?")


def open_redraft(port, docno, find_target_page, CDP):
    """기안한문서 목록에서 docno 행을 골라 [재작성]과 똑같이 호출. → status 문자열."""
    cdp = _edufine_cdp(port, find_target_page, CDP)
    if cdp is None:
        return "K-에듀파인 탭 없음"
    try:
        cdp.send("Page.setWebLifecycleState", {"state": "active"})
        if ensure_drafted_frame(cdp):
            return "noframe"
        return cdp.eval_value(OPEN_JS_TMPL.replace("%DOCNO%", json.dumps(docno, ensure_ascii=False))) or "?"
    finally:
        cdp.close()


# ───────────────────────── 제목 묶기 (반복 기안 찾기) ─────────────────────────

_CASE_RE = re.compile(r"\(\s*[가-힣A-Za-z]+\s*-\s*\d{4}\s*-\s*\d+\s*\)")        # (우리중-2026-8)
_LEAD_RE = re.compile(r"^\s*\(\s*[가-힣]+\s*\)\s*")                             # (우리중) 머리말


def group_key(title):
    t = _CASE_RE.sub("", title or "")
    t = _LEAD_RE.sub("", t)
    t = re.sub(r"\d+", "#", t)
    t = re.sub(r"[\s·ㆍ,]+", "", t)
    return t


def group_rows(rows):
    """[{key, sample, count, latest(row), rows}] — 많이 올린 순."""
    groups = {}
    for r in rows:
        k = group_key(r.get("title", ""))
        if not k:
            continue
        g = groups.setdefault(k, {"key": k, "rows": []})
        g["rows"].append(r)
    out = []
    for g in groups.values():
        g["rows"].sort(key=lambda r: r.get("done", ""), reverse=True)
        g["latest"] = g["rows"][0]
        g["count"] = len(g["rows"])
        g["sample"] = _CASE_RE.sub("", g["latest"]["title"]).strip()
        out.append(g)
    out.sort(key=lambda g: g["latest"].get("done", ""), reverse=True)   # 최근 순
    out.sort(key=lambda g: -g["count"])                                  # 많이 올린 순 (안정 정렬)
    return out


# ───────────────────────── 2) 기안기(WXSClient, IE 엔진) 붙기 ─────────────────────────

_u = ctypes.windll.user32
_EnumProc = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
IID_IHTMLDocument2 = "{332C4425-26CB-11D0-B483-00C04FD90119}"


def _cls(h):
    b = ctypes.create_unicode_buffer(256)
    _u.GetClassNameW(h, b, 256)
    return b.value


def _txt(h):
    b = ctypes.create_unicode_buffer(256)
    _u.GetWindowTextW(h, b, 256)
    return b.value


def find_wxs_windows(title_key=WXS_TITLE_KEY):
    """[(top_hwnd, ie_server_hwnd, title)] — 보이는 기안기 창들."""
    out = []

    def top(h, _l):
        t = _txt(h)
        if title_key in t and _u.IsWindowVisible(h):
            found = []

            def kid(k, _l2):
                if _cls(k) == "Internet Explorer_Server":
                    found.append(k)
                return True
            _u.EnumChildWindows(h, _EnumProc(kid), 0)
            for k in found[:1]:
                out.append((h, k, t))
        return True
    _u.EnumWindows(_EnumProc(top), 0)
    return out


def _get_doc(ie_hwnd):
    if not HAS_COM:
        raise RuntimeError("comtypes가 필요합니다 (pip install comtypes)")
    comtypes.CoInitialize()
    msg = _u.RegisterWindowMessageW("WM_HTML_GETOBJECT")
    res = W.DWORD()
    _u.SendMessageTimeoutW(ie_hwnd, msg, 0, 0, 2, 4000, ctypes.byref(res))
    if not res.value:
        raise RuntimeError("기안기 창이 응답하지 않습니다")
    p = ctypes.POINTER(IDispatch)()
    iid = GUID(IID_IHTMLDocument2)
    hr = ctypes.windll.oleacc.ObjectFromLresult(res.value, ctypes.byref(iid), 0, ctypes.byref(p))
    if hr != 0:
        raise OSError("ObjectFromLresult 실패 0x{:08X}".format(hr & 0xFFFFFFFF))
    return comtypes.client.dynamic.Dispatch(p)


class Gianki:
    """열려 있는 기안기 창 하나. (COM 객체는 만든 스레드에서만 쓴다)"""

    def __init__(self, ie_hwnd, top_hwnd=None):
        self.ie = ie_hwnd
        self.top = top_hwnd
        self.doc = _get_doc(ie_hwnd)

    def js(self, expr):
        """IE 문서에서 JS 식을 계산해 문자열로 돌려받는다 (execScript + body 속성 경유)."""
        for bad in FORBIDDEN_JS:
            if bad in expr:
                raise PermissionError("안전장치: '{}' 호출은 허용하지 않습니다".format(bad))
        code = ("try{document.body.setAttribute('data-gd', String(" + expr + "));}"
                "catch(e){document.body.setAttribute('data-gd','__ERR__ '+e.message);}")
        self.doc.parentWindow.execScript(code, "JavaScript")
        v = self.doc.body.getAttribute("data-gd")
        v = "" if v is None else str(v)
        if v.startswith("__ERR__"):
            raise RuntimeError(v[8:])
        return v

    @staticmethod
    def _ed(n):
        return "window.frames[{0}].document.getElementById('editor{1}')".format(n - 1, n)

    def editors(self):
        out = []
        for n in (1, 2):
            try:
                if self.js("(" + self._ed(n) + ")?'y':'n'") == "y":
                    out.append(n)
            except Exception:
                pass
        return out

    def title(self):
        return self.js("document.getElementById('Sj').value")

    def set_title(self, t):
        self.js("(function(){var e=document.getElementById('Sj'); e.value=%s; "
                "try{e.fireEvent('onchange');}catch(x){} return e.value;})()" % json.dumps(t, ensure_ascii=False))

    def body_text(self, n=1):
        return self.js(self._ed(n) + ".GetTextFile('TEXT','')")

    def ready(self):
        try:
            if self.js("document.readyState") != "complete":
                return False
            return bool(self.title()) and len(self.body_text(1)) > 30
        except Exception:
            return False

    def all_replace(self, n, find, repl):
        expr = r"""(function(){
          var h=%s; var old=h.EditMode; h.EditMode=1;
          try{ var a=h.CreateAction('AllReplace'); var s=a.CreateSet(); a.GetDefault(s);
               s.SetItem('FindString',%s); s.SetItem('ReplaceString',%s);
               s.SetItem('IgnoreMessage',1); s.SetItem('Direction',2);
               a.Execute(s); }
          finally{ h.EditMode=old; }
          return 'ok'; })()""" % (self._ed(n), json.dumps(find, ensure_ascii=False), json.dumps(repl, ensure_ascii=False))
        return self.js(expr)

    def bring_front(self):
        if self.top:
            _u.ShowWindow(self.top, 9)          # SW_RESTORE
            _u.SetForegroundWindow(self.top)


def _pid_of(h):
    p = W.DWORD()
    _u.GetWindowThreadProcessId(h, ctypes.byref(p))
    return p.value


def _exe_of(pid):
    k32 = ctypes.windll.kernel32
    hp = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not hp:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        n = W.DWORD(520)
        return buf.value if k32.QueryFullProcessImageNameW(hp, 0, buf, ctypes.byref(n)) else ""
    finally:
        k32.CloseHandle(hp)


def wxs_pids():
    """지금 떠 있는 기안기(WXSClient.exe) 프로세스 번호들."""
    pids = set()

    def cb(h, _l):
        pid = _pid_of(h)
        if pid not in pids and _exe_of(pid).lower().endswith("wxsclient.exe"):
            pids.add(pid)
        return True
    _u.EnumWindows(_EnumProc(cb), 0)
    return pids


# 새 기안기가 뜰 때 나오는 확인창 중, 이 도구가 '취소'로 대신 답해도 안전한 것만.
#  - 자동저장문서 읽어오기 → 취소 (재작성 원본을 써야 하므로 자동저장본을 불러오지 않음)
#  - 열려있는 기안/결재기를 닫습니다 → 취소 (선생님의 다른 기안 창을 닫지 않음)
SAFE_CANCEL_PROMPTS = ("자동저장문서", "열려있는 기안/결재기를 닫습니다")


def _prompt_text(h):
    best = ""

    def kc(k, _l):
        nonlocal best
        if _cls(k) == "Static":
            b = ctypes.create_unicode_buffer(2048)
            _u.GetWindowTextW(k, b, 2048)
            if len(b.value) > len(best):
                best = b.value
        return True
    _u.EnumChildWindows(h, _EnumProc(kc), 0)
    return best


def handle_new_prompts(pids):
    """주어진 기안기 프로세스의 '웹 페이지 메시지' 창 처리 → (취소한 것들, 모르는 것들)."""
    done, unknown = [], []

    def cb(h, _l):
        if _pid_of(h) in pids and _u.IsWindowVisible(h) and _cls(h) == "#32770" and _txt(h) == "웹 페이지 메시지":
            msg = _prompt_text(h).replace("‎", "")
            if any(k in msg for k in SAFE_CANCEL_PROMPTS):
                btn = _u.FindWindowExW(h, 0, "Button", "취소")
                if btn:
                    _u.PostMessageW(btn, 0x00F5, 0, 0)     # BM_CLICK
                    done.append(msg.splitlines()[0])
            else:
                unknown.append(msg)
        return True
    _u.EnumWindows(_EnumProc(cb), 0)
    return done, unknown


def wait_new_gianki(before_ie, handle_prompts=True, timeout=70, poll=0.8):
    """[재작성] 호출 뒤 새 기안기 창이 본문까지 다 읽힐 때까지 기다림.
       새 기안기에서 뜨는 알려진 확인창(자동저장본 읽기 / 다른 기안기 닫기)은 '취소'로 넘긴다.
       → (target 또는 None, notes[str])"""
    deadline = time.time() + timeout
    target, notes, seen_unknown = None, [], set()
    while time.time() < deadline:
        if handle_prompts:
            # 새 창은 기존 WXSClient 프로세스에 붙어 뜨기도 하므로 기안기 전체를 본다
            done, unknown = handle_new_prompts(wxs_pids())
            for d in done:
                notes.append("기안기 확인창 '{}' → 취소".format(d[:30]))
            if unknown:
                for m in unknown:
                    if m not in seen_unknown:
                        seen_unknown.add(m)
                        notes.append("⚠ 기안기에 처음 보는 확인창이 떠 있습니다(직접 답해 주세요): " + m[:80])
                time.sleep(poll)
                continue                          # 확인창이 떠 있는 동안은 창 안을 건드리지 않음
            if done:
                time.sleep(1.5)
                continue
        for top, ie, _t in find_wxs_windows():
            if ie not in before_ie:
                target = (ie, top)
        if target:
            try:
                g = Gianki(*target)
                _mute_autosave_prompt(g)
                if g.ready():
                    return target, notes
            except Exception:
                pass
        time.sleep(poll)
    return target, notes   # 창은 떴지만 아직 준비 중일 수 있음


class PromptGuard:
    """작업하는 동안 그 기안기 프로세스에 '안전한 확인창'(SAFE_CANCEL_PROMPTS)이 뜨면 취소해 준다.
       (확인창이 떠 있으면 창 안 스크립트 호출이 멈추므로, 다른 스레드에서 지켜본다)"""

    def __init__(self, top_hwnd, every=0.7):
        import threading
        self.pid = _pid_of(top_hwnd) if top_hwnd else 0
        self.every = every
        self.cancelled = []
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.wait(self.every):
            if self.pid:
                done, _unk = handle_new_prompts({self.pid})
                self.cancelled.extend(done)

    def __enter__(self):
        self._t.start()
        return self

    def __exit__(self, *_a):
        self._stop.set()


def _mute_autosave_prompt(g):
    """기안기는 로딩이 끝난 뒤(약 14초) fncPreLoadAutoSaveDoc()로 '자동저장문서를 읽어오시겠습니까?'를
       띄운다. 여기서 [확인]을 누르면 만들어 둔 초안이 예전 자동저장본으로 바뀌므로, 이 창에서만
       그 자동 확인을 끈다. (필요하면 기안 창의 [자동저장문서] 버튼 = fncLoadAutoSaveDoc 은 그대로 동작)"""
    try:
        g.js("(function(){ if(typeof fncPreLoadAutoSaveDoc==='function' && !window.__gdMuted){"
             " window.fncPreLoadAutoSaveDoc=function(){ window.__gdSkipped=1; }; window.__gdMuted=1; }"
             " return window.__gdMuted?1:0; })()")
    except Exception:
        pass


# ───────────────────────── 3) 바뀌는 부분(변수) 찾기 ─────────────────────────

_TOKEN_RES = [
    ("사안번호", re.compile(r"[가-힣]{2,}-\d{4}-\d{1,3}")),
    ("문서번호", re.compile(r"[가-힣]{2,}학교-\d{3,}")),
    ("날짜",     re.compile(r"\d{4}\.\s?\d{1,2}\.\s?\d{1,2}\.(?:\s?\([월화수목금토일]\))?")),
    ("시각",     re.compile(r"(?<![\d:])\d{1,2}:\d{2}(?:\s?~\s?\d{1,2}:\d{2})?(?![\d:])")),
]
_LABEL_RE = re.compile(r"^\s*(?:[가-하]\.|\d{1,2}\.|[①-⑳]|[-○□▪•])\s*([^:：\n]{1,14}?)\s*[:：]\s*(\S.{0,80}?)\s*$")


def body_region(text):
    """기안문 텍스트에서 '제목 다음 ~ 협조자/시행 앞'만 잘라낸다(머리·꼬리 서식 제외)."""
    lines = text.splitlines()
    start = 0
    for i, ln in enumerate(lines):
        if ln.strip() == "제목":
            start = i + 1
            break
    end = len(lines)
    for i in range(start, len(lines)):
        s = lines[i].strip()
        if s in ("협조자", "시행") or s.startswith("시행 "):
            end = i
            break
    return "\n".join(lines[start:end])


def detect_vars(title, text):
    """[{label, old}] — 사람이 '새 값'만 채우면 되도록 바뀔 만한 문자열 후보."""
    region = body_region(text)
    seen, out = set(), []

    def add(label, val):
        val = (val or "").strip()
        if len(val) < 2 or val in seen:
            return
        seen.add(val)
        out.append({"label": label, "old": val})

    for ln in region.splitlines():
        m = _LABEL_RE.match(ln)
        if m:
            add(m.group(1).strip(), m.group(2))
    for label, rx in _TOKEN_RES:
        for m in rx.finditer(title + "\n" + region):
            add(label, m.group(0))
    return out


# ───────────────────────── 4) 적용 ─────────────────────────

def apply_changes(ie_hwnd, top_hwnd, pairs, new_title=None):
    """pairs: [(old, new)] — old != new 인 것만. 두 단계 치환(자리표 경유)으로 연쇄 치환을 막는다.
       반환: {ok, title, report:[...], warn:[...]}"""
    with PromptGuard(top_hwnd):
        return _apply_changes(ie_hwnd, top_hwnd, pairs, new_title)


def _apply_changes(ie_hwnd, top_hwnd, pairs, new_title=None):
    g = Gianki(ie_hwnd, top_hwnd)
    _mute_autosave_prompt(g)
    pairs = [(o, n) for o, n in pairs if o and o != n]
    pairs.sort(key=lambda p: -len(p[0]))              # 긴 것부터 (부분 문자열 충돌 방지)
    warn = [o for o, _n in pairs if len(o) < 3]
    report = []
    eds = g.editors() or [1]
    before = g.body_text(1)

    for n in eds:
        for i, (o, _new) in enumerate(pairs):
            g.all_replace(n, o, "@@GD{}@@".format(i))
        for i, (_o, new) in enumerate(pairs):
            g.all_replace(n, "@@GD{}@@".format(i), new)

    old_title = g.title()
    if new_title is None:
        t = old_title
        for i, (o, _new) in enumerate(pairs):
            t = t.replace(o, "@@GD{}@@".format(i))
        for i, (_o, new) in enumerate(pairs):
            t = t.replace("@@GD{}@@".format(i), new)
        new_title = t
    if new_title and new_title != old_title:
        g.set_title(new_title)

    after = g.body_text(1)
    for o, new in pairs:
        left = None if o in new else after.count(o)       # 새 값이 원본을 품으면 셀 수 없음
        report.append({"old": o, "new": new, "found": before.count(o), "left": left,
                       "now": after.count(new)})
    if "@@GD" in after:
        warn.append("자리표(@@GD)가 본문에 남아 있습니다 — 확인 필요")
    g.bring_front()
    return {"ok": True, "title": g.title(), "report": report, "warn": warn,
            "at": datetime.now().strftime("%H:%M:%S")}


def read_gianki(ie_hwnd, top_hwnd=None):
    g = Gianki(ie_hwnd, top_hwnd)
    title = g.title()
    text = g.body_text(1)
    try:
        atts = list_attachments(g)
    except Exception:
        atts = []
    return {"title": title, "text": text, "vars": detect_vars(title, text),
            "atts": atts, "butim": parse_butim(text)}


# ───────────────────────── 5) 붙임 파일 (첨부) ─────────────────────────
#  기안기 첨부 = Attach.fileBox (화면 목록) + _hsAttach(HSAttach1 ActiveX, HSAttachCtrl.dll)
#  [파일추가] = fileBox.openFileDialog() → _hsAttach.Attach()(파일 대화상자) → 확장자·크기·이름길이
#  검사 → addFiles(). 여기서는 그 함수 원문에서 Attach() 만 AddFile(경로)로 바꿔 실행한다
#  (검사 로직은 에듀파인 것 그대로). ※ 재작성은 원본 문서의 첨부를 그대로 복사해 온다.

ATT_LIST_JS = r"""(function(){var f=Attach.fileBox.filesAtGroup(),r=[];   // 화면 순서(=올라가는 순서)
  for(var i=0;i<f.length;i++) r.push({name:f[i].name, size:Number(f[i].size)||0, pc:!!f[i].atPc});
  return JSON.stringify(r);})()"""

ATT_ADD_JS = r"""(function(){
  var fb=Attach.fileBox; window.__gdAlerts=[];
  var key='this._hsAttach.Attach();';
  var src=String(fb.openFileDialog);
  if(src.indexOf(key)<0) return JSON.stringify({err:'첨부 함수 구조가 바뀌었습니다'});
  src=src.split(key).join('for(var __i=0;__i<window.__gdPaths.length;__i++){this._hsAttach.AddFile(window.__gdPaths[__i]);}');
  if(src.indexOf('.Attach()')>=0) return JSON.stringify({err:'대화상자 호출이 남아 있어 중단'});
  var fn=eval('('+src+')');
  var oldAlert=window.alert; window.alert=function(m){window.__gdAlerts.push(String(m));};
  var before=fb.files().length;
  try{ fn.call(fb); } finally { window.alert=oldAlert; }
  try{ fncSetExtFileCount(); }catch(e){}
  return JSON.stringify({before:before, after:fb.files().length, alerts:window.__gdAlerts});
})()"""

ATT_REMOVE_JS = r"""(function(names){var fb=Attach.fileBox, n=0;
  fb.children.listview.children().each(function(){var f=$(this).data('file');
    if(f && names.indexOf(f.name)>=0){ fb._setDeleteFile($(this)); n++; }});
  fb._updateFileSizeInfo(); return n;})(%s)"""

# 이름이 name 인 항목 하나만 선택해서 한 칸 위로 (에듀파인 [첨부파일위로]와 같은 함수)
ATT_MOVEUP_JS = r"""(function(name){var fb=Attach.fileBox, lv=fb.children.listview.children(), hit=-1;
  lv.each(function(i){var f=$(this).data('file');
    if(f && f.name===name){ hit=i; $(this).addClass('selected'); $(this).find('td:first-child input[type=checkbox]').prop('checked',true); }
    else { $(this).removeClass('selected'); $(this).find('td:first-child input[type=checkbox]').prop('checked',false); } });
  if(hit<=0) return hit;
  fb.moveUpFiles();
  lv=fb.children.listview.children(); lv.removeClass('selected'); lv.find('td:first-child input[type=checkbox]').prop('checked',false);
  return hit;})(%s)"""


def _jl(s, default=None):
    """기안기 페이지는 prototype.js를 써서 JSON.stringify가 배열을 두 번 인코딩한다 → 풀릴 때까지 푼다."""
    try:
        v = json.loads(s) if s else default
        for _ in range(3):
            if isinstance(v, str) and v[:1] in "[{":
                v = json.loads(v)
            else:
                break
        if isinstance(v, dict):
            v = {k: _jl(x, x) if isinstance(x, str) and x[:1] in "[{" else x for k, x in v.items()}
        if isinstance(v, list):
            v = [_jl(x, x) if isinstance(x, str) and x[:1] in "[{" else x for x in v]
        return v
    except Exception:
        return default


def list_attachments(g):
    return _jl(g.js(ATT_LIST_JS), [])


def attach_rules(g):
    p = _jl(g.js("(function(){var p=Attach.fileBox.properties;return JSON.stringify("
                 "{gubun:p.docFileExtListGubun, ext:p.extensions, dis:p.disallows, editable:p.editable});})()"), {})
    split = lambda s: {x.strip().upper() for x in (s or "").split(",") if x.strip()}
    return {"gubun": p.get("gubun"), "allow": split(p.get("ext")), "deny": split(p.get("dis")),
            "editable": p.get("editable")}


def check_file(path, rules):
    """에듀파인이 경고창으로 거절할 파일을 미리 걸러낸다. → 문제 문자열 또는 ''"""
    import os
    name = os.path.basename(path)
    if not os.path.isfile(path):
        return "파일 없음"
    if os.path.getsize(path) == 0:
        return "파일 크기 0"
    if "." not in name:
        return "확장자 없음"
    if name.rfind(".") > 70:
        return "파일명 70자 초과"
    if "?" in name:
        return "파일명에 쓸 수 없는 문자"
    ext = name.rsplit(".", 1)[1].upper()
    if rules.get("gubun") == "02" and rules["allow"] and ext not in rules["allow"]:
        return "허용되지 않는 확장자(.{})".format(ext.lower())
    if rules.get("gubun") == "01" and ext in rules["deny"]:
        return "금지된 확장자(.{})".format(ext.lower())
    return ""


def _cancel_file_dialogs(top_hwnd):
    """안전장치: 기안기 프로세스에 파일 '열기' 대화상자가 떠 있으면 [취소]."""
    pid = W.DWORD()
    _u.GetWindowThreadProcessId(top_hwnd, ctypes.byref(pid))
    hits = []

    def cb(h, _l):
        p = W.DWORD()
        _u.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid.value and _u.IsWindowVisible(h) and _cls(h) == "#32770" and _txt(h) == "열기":
            hits.append(h)
        return True
    _u.EnumWindows(_EnumProc(cb), 0)
    for h in hits:
        btn = _u.FindWindowExW(h, 0, "Button", "취소")
        if btn:
            _u.PostMessageW(btn, 0x00F5, 0, 0)   # BM_CLICK
    return len(hits)


def _apply_pairs(s, pairs):
    pairs = sorted([(o, n) for o, n in pairs if o and o != n], key=lambda p: -len(p[0]))
    for i, (o, _n) in enumerate(pairs):
        s = s.replace(o, "\x01{}\x01".format(i))
    for i, (_o, n) in enumerate(pairs):
        s = s.replace("\x01{}\x01".format(i), n)
    return s


def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFC", s or "").lower()
    return re.sub(r"[\s_\-·ㆍ()\[\]{}（）]+", "", s)


def match_attachments(atts, pairs, folder, max_files=400):
    """옛 첨부마다 '바꿀 새 파일'을 폴더에서 찾는다.
       atts: [{name}], pairs: [(원본, 새값)] (기안문 전체 치환 기준)
       → rows: [{old, expect, path, how}]  (how: 정확/비슷/없음/유지)
          extras: 폴더에서 새 사안 표시가 있는데 짝이 없는 파일 경로들"""
    import difflib
    import os
    files = []
    if folder and os.path.isdir(folder):
        for root, _dirs, names in os.walk(folder):
            for n in names:
                if not n.startswith(("~$", ".")):
                    files.append(os.path.join(root, n))
                if len(files) >= max_files:
                    break
            if root.count(os.sep) - folder.count(os.sep) >= 1:
                _dirs[:] = []                     # 하위 폴더는 한 단계까지만
    by_norm = {}
    for p in files:
        by_norm.setdefault(_norm(os.path.basename(p)), p)
    changed_tokens = [n for o, n in pairs if o != n and len(n) >= 3]
    used, rows = set(), []
    for a in atts:
        old = a["name"]
        expect = _apply_pairs(old, pairs)
        row = {"old": old, "expect": expect, "path": None, "how": "유지" if expect == old else "없음"}
        if expect != old and files:
            exact = [p for p in files if os.path.basename(p) == expect]
            if exact:
                row.update(path=exact[0], how="정확")
            elif _norm(expect) in by_norm:
                row.update(path=by_norm[_norm(expect)], how="정확")
            else:
                stem = _norm(os.path.splitext(expect)[0])
                best, score = None, 0.0
                for p in files:
                    if p in used:
                        continue
                    b = os.path.basename(p)
                    if changed_tokens and not any(t in b for t in changed_tokens):
                        continue
                    r = difflib.SequenceMatcher(None, stem, _norm(os.path.splitext(b)[0])).ratio()
                    if r > score:
                        best, score = p, r
                if best and score >= 0.6:
                    row.update(path=best, how="비슷({:.0%})".format(score))
        if row["path"]:
            used.add(row["path"])
        rows.append(row)
    extras = [p for p in files if p not in used and changed_tokens
              and any(t in os.path.basename(p) for t in changed_tokens)]
    return rows, extras


_BUTIM_ITEM = re.compile(r"^\s*(?:붙임\s*)?(?:(\d{1,2})\.\s*)?(.+?)\s+(\d+)\s*부\.?(?:\s*끝\.?)?\s*$")


def parse_butim(text):
    """본문의 '붙임 1. ○○ 1부.' 목록 → [이름]."""
    lines = body_region(text).splitlines()
    out, on = [], False
    for ln in lines:
        s = ln.strip()
        if s.startswith("붙임"):
            on = True
        if not on or not s:
            continue
        m = _BUTIM_ITEM.match(s)
        if m:
            out.append(m.group(2).strip())
        if "끝." in s:
            break
    return out


def compare_butim(butim, atts):
    """본문 붙임 목록 ↔ 실제 첨부 이름 대조 → [(붙임항목, 첨부이름 또는 None)], 남는 첨부."""
    import os
    left = [a["name"] for a in atts]
    pairs = []
    for b in butim:
        nb = _norm(b)
        hit = next((n for n in left if nb and nb in _norm(os.path.splitext(n)[0])), None)
        if hit is None:
            hit = next((n for n in left if _norm(os.path.splitext(n)[0]) in nb), None)
        if hit:
            left.remove(hit)
        pairs.append((b, hit))
    return pairs, left


def replace_attachments(ie_hwnd, top_hwnd, plan):
    """plan: 최종 순서대로 [{"old": 옛이름|None, "path": 새파일|None, "act": "유지"|"교체"|"빼기"|"추가"}]
       → {ok, atts(최종), added, removed, problems, alerts, order_ok}"""
    with PromptGuard(top_hwnd):
        return _replace_attachments(ie_hwnd, top_hwnd, plan)


def _replace_attachments(ie_hwnd, top_hwnd, plan):
    import os
    import threading
    g = Gianki(ie_hwnd, top_hwnd)
    _mute_autosave_prompt(g)
    rules = attach_rules(g)
    if not rules.get("editable", True):
        return {"ok": False, "err": "이 문서는 첨부를 고칠 수 없는 상태입니다"}
    problems = []
    for r in plan:
        if r["act"] in ("교체", "추가"):
            why = check_file(r["path"], rules)
            if why:
                problems.append("{} — {}".format(os.path.basename(r["path"] or "?"), why))
    if problems:
        return {"ok": False, "err": "첨부할 수 없는 파일이 있습니다", "problems": problems}

    current = [a["name"] for a in list_attachments(g)]
    new_names = [os.path.basename(r["path"]) for r in plan if r["act"] in ("교체", "추가")]
    clash = [n for n in new_names if n in current and
             not any(r["old"] == n and r["act"] in ("교체", "빼기") for r in plan)]
    if clash:
        return {"ok": False, "err": "이미 같은 이름의 첨부가 있습니다: " + ", ".join(clash)}

    to_remove = [r["old"] for r in plan if r["act"] in ("교체", "빼기") and r.get("old")]
    removed = int(g.js(ATT_REMOVE_JS % json.dumps(to_remove, ensure_ascii=False))) if to_remove else 0

    paths = [r["path"] for r in plan if r["act"] in ("교체", "추가")]
    alerts, added = [], 0
    if paths:
        g.js("(function(){window.__gdPaths=%s;return 1;})()" % json.dumps(paths, ensure_ascii=False))
        guard = threading.Timer(12, _cancel_file_dialogs, args=(top_hwnd,))
        guard.start()
        try:
            res = _jl(g.js(ATT_ADD_JS), {})
        finally:
            guard.cancel()
        if res.get("err"):
            return {"ok": False, "err": res["err"]}
        added = res.get("after", 0) - res.get("before", 0)
        alerts = res.get("alerts", [])

    # 순서 맞추기: plan 순서대로 (위에서부터) 제자리로 끌어올림
    want = []
    for r in plan:
        if r["act"] == "유지":
            want.append(r["old"])
        elif r["act"] in ("교체", "추가"):
            want.append(os.path.basename(r["path"]))
    names = [a["name"] for a in list_attachments(g)]
    for i, n in enumerate(want):
        if n not in names:
            continue
        guard = 0
        while names.index(n) > i and guard < 50:
            g.js(ATT_MOVEUP_JS % json.dumps(n, ensure_ascii=False))
            names = [a["name"] for a in list_attachments(g)]
            guard += 1
    final = list_attachments(g)
    order_ok = [a["name"] for a in final][:len(want)] == want
    g.bring_front()
    return {"ok": True, "atts": final, "added": added, "removed": removed, "alerts": alerts,
            "order_ok": order_ok, "problems": [], "at": datetime.now().strftime("%H:%M:%S")}


# ───────────────────────── 6) 기안 표준 (종류별 일관화) ─────────────────────────
#  기안한문서의 과거 문서들을 서버 조회(Capi)로 읽어 종류별 표준(결재선·과제카드·공개·열람제한·
#  수신·첨부)을 제안하고, 열린 재작성 창을 표준과 대조한다.
#  ※ 실측(2026-11-24): 재작성하면 ① 비공개 '사유 호수'(1~8호)가 빠지고 ② 대외문서의 수신자가
#    비어서(내부결재로) 온다. 결재선·과제카드·공개구분·열람제한은 원본대로 따라온다.

OTHBC = {"1": "공개", "2": "부분공개", "3": "비공개"}
READ = {"0": "설정안함", "1": "결재완료일까지", "2": "지정날짜", "3": "영구"}
_WIN_OTHBC = {"pubtype_open": "1", "pubtype_partial": "2", "pubtype_not": "3"}
_WIN_READ = {"none": "0", "beforeComplete": "1", "expireDate": "2", "permanent": "3"}
_READ_RADIO = {"0": "readngLmtt0", "1": "readngLmtt3", "2": "readngLmtt1", "3": "readngLmtt2"}
_OTHBC_RADIO = {"1": "othbcLmtt1", "2": "othbcLmtt2", "3": "othbcLmtt3"}

META_JS = "(function(){" + _FIND_FRAME_JS + r"""
  var w=__gdFrame(); if(!w) return JSON.stringify({err:'noframe'});
  var obj=w.document.forms[0].docinfo, C=w.Capi, out=[];
  if(!obj || !C) return JSON.stringify({err:'nolist'});
  if(obj.length===undefined) obj=[obj];
  for(var i=0;i<obj.length;i++){
    var v=obj[i].value.split(','), id=v[0], tds=obj[i].closest('tr').querySelectorAll('td');
    var r={id:id, docno:v[13], rcvCol:((tds[6]||{}).innerText||'').replace(/\s+/g,' ').trim()};
    try{ var a=C.getApprovalInfo(id); r.title=a.title; r.files=a.fileNames; r.draft=a.draftDate; r.done=a.approvalDate; }catch(e){ r.err='approval'; }
    try{ r.task=C.getTaskInfo(id).tkList; }catch(e){ r.err='task'; }
    try{ r.line=C.getPrtcpntInfoListBySanctnId(id).prtcpntList; }catch(e){ r.err='line'; }
    try{ var s=C.getSanctnInfo(id).docInfo; r.othbcSe=s.othbcSe; r.othbcResn=s.othbcLmttResnCn;
         r.readTy=s.readngLmttTy; r.readUntil=(s.readngLmttRelisDt||'').slice(0,10); }catch(e){ r.err='sanctn'; }
    out.push(r);
  }
  return JSON.stringify({rows:out});
})()"""


def _list(v):
    v = _jl(v, v) if isinstance(v, str) else v
    return v if isinstance(v, list) else []


def read_doc_meta(port, find_target_page, CDP, with_body=False, progress=None):
    """기안한문서 목록(현재 화면)의 문서마다 결재선·과제카드·공개·열람제한·첨부를 서버에서 읽는다 (읽기만).
       with_body=True 면 본문(.odt)도 내려받아 핵심 줄(body)로 넣는다."""
    cdp = _edufine_cdp(port, find_target_page, CDP)
    if cdp is None:
        return None, "K-에듀파인 탭이 없습니다"
    bodies, nb = {}, 0
    try:
        cdp.send("Page.setWebLifecycleState", {"state": "active"})
        why = ensure_drafted_frame(cdp)
        if why:
            return None, why
        res = _jl(cdp.eval_value(META_JS), {})
        if with_body and isinstance(res, dict) and not res.get("err"):
            todo = [r for r in res.get("rows", []) if r.get("id")]
            for i, r in enumerate(todo):
                if progress:
                    progress(i + 1, len(todo))
                try:
                    bodies[r["id"]] = body_core(odt_text(fetch_body_odt(cdp, r["id"])))
                    nb += 1
                except Exception:
                    bodies[r["id"]] = None
    finally:
        cdp.close()
    if not isinstance(res, dict) or res.get("err"):
        return None, ("에듀파인에서 [기안한문서] 화면을 한 번 열어 주세요" if (res or {}).get("err") == "noframe"
                      else "문서 정보를 못 읽음 ({})".format((res or {}).get("err")))
    rows = []
    for r in res.get("rows", []):
        line = [(x.get("prtcpntOfcpsNm", ""), x.get("prtcpntNm", "")) for x in _list(r.get("line"))]
        task = ["{} ({})".format(x.get("NAME", ""), x.get("DPSN", "")) for x in _list(r.get("task"))]
        files = [f for f in _list(r.get("files")) if f not in ("HOX_file",) and not f.startswith("결재문서본문")]
        resn = r.get("othbcResn") or ""
        rows.append({
            "docno": r.get("docno", ""), "title": r.get("title", ""), "key": group_key(r.get("title", "")),
            "draft": r.get("draft", ""), "done": r.get("done") or "", "line": line, "task": task, "files": files,
            "othbc": r.get("othbcSe") or "", "ho": [i + 1 for i, ch in enumerate(resn) if ch == "Y"],
            "read": r.get("readTy") or "", "read_until": r.get("readUntil") or "", "rcv": r.get("rcvCol", ""),
            "err": r.get("err"), "body": bodies.get(r.get("id")),
        })
    return rows, "ok ({}건{})".format(len(rows), ", 본문 {}건".format(nb) if with_body else "")


def othbc_label(se, ho):
    return OTHBC.get(se, se or "?") + (" " + ",".join("{}호".format(h) for h in ho) if ho else "")


def parse_othbc_label(s):
    se = next((k for k, v in sorted(OTHBC.items(), key=lambda kv: -len(kv[1])) if s.startswith(v)), "")
    return se, [int(h) for h in re.findall(r"(\d)호", s)]


def line_label(line):
    return " → ".join(p for p, _n in line)


def att_stem(fname):
    s = fname.rsplit(".", 1)[0] if "." in fname else fname
    s = _CASE_RE.sub("", s)
    s = re.sub(r"[_\s]*[가-힣]{2,}-\d{4}-\d{1,3}\s*$", "", s)     # 꼬리 '_우리중-2026-8'
    return s.strip(" _-")


FIELDS = [("line", "결재선"), ("task", "과제카드"), ("othbc", "공개"), ("read", "열람제한"), ("rcv", "수신")]


def _field_value(r, f):
    if f == "line":
        return line_label(r["line"])
    if f == "task":
        return " / ".join(r["task"])
    if f == "othbc":
        return othbc_label(r["othbc"], r["ho"])
    if f == "read":
        return READ.get(r["read"], r["read"])
    return r.get(f, "")


def propose_standards(rows):
    """종류(group_key)별 표준 제안. 값은 가장 많이 쓴 것(같으면 최근 문서 쪽)."""
    groups = {}
    for r in rows:
        if r.get("key"):
            groups.setdefault(r["key"], []).append(r)
    out = {}
    for key, rs in groups.items():
        rs.sort(key=lambda r: r.get("draft", ""), reverse=True)      # 최근 먼저
        std = {"name": _CASE_RE.sub("", rs[0]["title"]).strip(), "count": len(rs),
               "docs": [r["docno"] for r in rs], "fields": {}, "confirmed": False}
        for f, _label in FIELDS:
            variants = {}
            for r in rs:
                variants.setdefault(_field_value(r, f), []).append(r["docno"].split("-")[-1])
            ranked = sorted(variants.items(), key=lambda kv: -len(kv[1]))   # 안정 정렬 → 동률이면 최근 문서 쪽
            std["fields"][f] = {"value": ranked[0][0], "variants": [[v, d] for v, d in ranked]}
        dated = [r["read_until"][5:10] for r in rs if r["read"] == "2" and r["read_until"][:4] not in ("", "1970", "9999")]
        std["read_mmdd"] = dated[0] if dated else ""
        # 첨부: 이름 줄기(사안번호 뗀 것)를 띄어쓰기 무시하고 묶음
        seen, spell = {}, {}
        for r in rs:
            for fn in r["files"]:
                st = att_stem(fn)
                n = _norm(st)
                seen.setdefault(n, set()).add(r["docno"])
                spell.setdefault(n, [])
                if st not in spell[n]:
                    spell[n].append(st)
        req = [spell[n][0] for n, d in seen.items() if len(d) == len(rs)]
        opt = [spell[n][0] for n, d in seen.items() if len(d) < len(rs)]
        std["atts_required"], std["atts_optional"] = req, opt
        std["att_spellings"] = [v for v in spell.values() if len(v) > 1]
        std.update(body_standard([(r["docno"], r["body"], (_CASENO_RE.findall(r["title"]) or [r["docno"]])[0])
                                  for r in rs if r.get("body")]))
        body_spread = any(1 < x["n"] < std["body_n"] for x in std["body_lines"] if x.get("k")) or \
            bool(std["body_dropped"])
        std["spread"] = sum(1 for f, _l in FIELDS if len(std["fields"][f]["variants"]) > 1) + \
            (1 if opt and len(rs) > 1 else 0) + len(std["att_spellings"]) + (1 if body_spread else 0)
        out[key] = std
    return out


def merge_standards(saved, proposed):
    """확정한 표준은 값은 그대로 두고 관찰 정보(건수·변형)만 새로 고친다."""
    merged = dict(saved or {})
    for key, p in proposed.items():
        s = merged.get(key)
        if s and s.get("confirmed"):
            for f, _l in FIELDS:
                s["fields"].setdefault(f, {})["variants"] = p["fields"][f]["variants"]
            s.update(count=p["count"], docs=p["docs"], spread=p["spread"], att_spellings=p["att_spellings"])
            if p.get("body_n") or not s.get("body_n"):        # 본문을 못 읽은 회차엔 예전 본문 표준 유지
                s.update({k: p.get(k) for k in BODY_KEYS})
            if not s.get("read_mmdd"):
                s["read_mmdd"] = p.get("read_mmdd", "")
        else:
            if s and s.get("body_n") and not p.get("body_n"):
                p.update({k: s.get(k) for k in BODY_KEYS})
            merged[key] = p
    return merged


def load_standards(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_standards(path, stds):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(stds, f, ensure_ascii=False, indent=1)


# --- 본문 표준 ---
#  과거 문서 본문 = 결재문서본문.odt. 파일ID = apprID 앞 17자리 + '001' (HOX=002, PDF=060/061, 첨부=1xx).
#  Capi.getSancFile('', apprID, FID) → TRID → /bms/cz/cb/wrt/manageFileDwld.do?TRID= (기안기 transfile_url 과 같은 길).
#  비교는 '제목 다음 ~ 끝.' 줄만, 숫자는 모두 같은 것으로 보고(#), 띄어쓰기 무시.
#  표준에는 2건 이상에 공통으로 있던 줄만 글자로 남기고, 한 문서에만 있던 줄(사안 내용·학생 정보)은 줄 수만 남긴다.

BODY_KEYS = ("body_n", "body_ref", "body_lines", "body_dropped")

BODY_JS_TMPL = "(async function(){" + _FIND_FRAME_JS + r"""
  var w=__gdFrame(); if(!w) return 'ERR noframe';
  var id=%ID%, t=w.Capi.getSancFile('', id, id.slice(0,17)+'001');
  if(!t || !t.TRID) return 'ERR nofile';
  var r=await w.fetch((w.PROJECT_CODE||'/bms')+'/cz/cb/wrt/manageFileDwld.do?TRID='+t.TRID+'&fileName=body.odt');
  if(!r.ok) return 'ERR http '+r.status;
  var b=new Uint8Array(await r.arrayBuffer()), s='', CH=0x8000;
  for(var j=0;j<b.length;j+=CH) s+=String.fromCharCode.apply(null, b.subarray(j,j+CH));
  return btoa(s);
})()"""

_CASENO_RE = re.compile(r"[가-힣]{2,}-\d{4}-\d{1,3}")


def fetch_body_odt(cdp, appr_id):
    import base64
    v = cdp.eval_value(BODY_JS_TMPL.replace("%ID%", json.dumps(appr_id)), await_promise=True) or ""
    if v.startswith("ERR"):
        raise RuntimeError(v)
    return base64.b64decode(v)


def odt_text(data):
    """ODT(본문) → 문단마다 한 줄인 글자."""
    import io
    import zipfile
    import xml.etree.ElementTree as ET
    if not data or data[:2] != b"PK":
        raise ValueError("본문이 odt가 아님")
    root = ET.fromstring(zipfile.ZipFile(io.BytesIO(data)).read("content.xml"))
    T = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"

    def inline(e):
        out = [e.text or ""]
        for c in e:
            if c.tag == T + "s":
                out.append(" " * int(c.get(T + "c", "1") or 1))
            elif c.tag == T + "tab":
                out.append("\t")
            elif c.tag == T + "line-break":
                out.append("\n")
            else:
                out.append(inline(c))
            out.append(c.tail or "")
        return "".join(out)

    lines = []

    def walk(e):
        for c in e:
            if c.tag in (T + "p", T + "h"):
                lines.append(inline(c))
            else:
                walk(c)
    body = root.find("{urn:oasis:names:tc:opendocument:xmlns:office:1.0}body")
    walk(body if body is not None else root)
    return "\n".join(lines)


def body_core(text):
    """기안문 글자 → 제목 다음 줄부터 '끝.'까지 (빈 줄 빼고, 띄어쓰기 한 칸으로)."""
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in (text or "").splitlines()]
    start = None
    for i, ln in enumerate(lines[:80]):
        if ln == "제목":                       # 기안 창: '제목' 다음 줄이 제목 글자
            j = i + 1
            while j < len(lines) and not lines[j]:
                j += 1
            start = j + 1
            break
        if "제목" in ln:                       # odt: 머리 서식이 '…수신…제목(제목 글자)' 한 줄로 붙어 나옴
            start = i + 1
            break
    if start is None:
        return []
    out = []
    for ln in lines[start:]:
        if ln in ("협조자", "시행") or ln.startswith("협조자"):
            break
        if ln:
            out.append(ln)
        if "끝." in ln:
            break
    return out


def _bkey(line):
    """비교 키: 숫자는 #, 띄어쓰기와 끝의 '끝.'은 무시 (붙임 목록 번호·끝 위치가 달라도 같은 줄)."""
    k = re.sub(r"\s+", "", re.sub(r"\d+", "#", line))
    return re.sub(r"끝\.?$", "", k)


def _clip(s, n=48):
    return s if len(s) <= n else s[:n - 1] + "…"


def body_standard(bodies):
    """bodies: [(docno, [줄], 사안)] 최근 문서 먼저 → 본문 표준. 같은 사안 문서는 최근 것 하나만 센다
       (같은 사안을 다시 올린 문서끼리는 학생 이름 줄까지 겹치므로 '공통 문장'으로 남기지 않기 위해).
       body_lines: 최근 문서 순서대로 {t 글자, k 비교키, n 몇 건에 있나, miss 없는 문서} / 한 문서에만 있던 줄은 {k:'', c 줄 수}
       body_dropped: 최근 문서엔 없지만 다른 문서 2건 이상에 있던 줄."""
    seen_case, uniq = set(), []
    for d, b, case in bodies:
        if case not in seen_case:
            seen_case.add(case)
            uniq.append((d, b))
    bodies = uniq
    out = {"body_n": len(bodies), "body_ref": "", "body_lines": [], "body_dropped": []}
    if len(bodies) < 2:
        return out
    short = lambda d: d.split("-")[-1]
    keysets = [(short(d), {_bkey(ln) for ln in b}) for d, b in bodies]
    ref_doc, ref = bodies[0]
    out["body_ref"] = short(ref_doc)
    ref_keys = set()
    for ln in ref:
        k = _bkey(ln)
        if not k or k in ref_keys:
            continue
        ref_keys.add(k)
        has = [d for d, ks in keysets if k in ks]
        if len(has) >= 2:
            out["body_lines"].append({"t": ln, "k": k, "n": len(has),
                                      "miss": [d for d, ks in keysets if k not in ks]})
        elif out["body_lines"] and not out["body_lines"][-1]["k"]:
            out["body_lines"][-1]["c"] += 1
        else:
            out["body_lines"].append({"t": "", "k": "", "n": 1, "c": 1})
    seen = {}
    for d, b in bodies[1:]:
        for ln in b:
            k = _bkey(ln)
            if k and k not in ref_keys:
                e = seen.setdefault(k, {"t": ln, "k": k, "docs": []})
                if short(d) not in e["docs"]:
                    e["docs"].append(short(d))
    out["body_dropped"] = [dict(e, n=len(e["docs"])) for e in seen.values() if len(e["docs"]) >= 2]
    return out


def body_template_lines(std):
    """표준 점검 화면에 보여 줄 본문 틀."""
    total = std.get("body_n", 0)
    if not std.get("body_lines"):
        return ["본문: 서로 다른 사안의 문서가 2건 이상이어야 본문 표준을 만듭니다 (지금 {}건)".format(total)]
    out = ["본문 표준 — 최근 문서 {} 기준, [같은 줄이 있던 문서/{}건]  (숫자는 달라도 같은 줄로 봄, 같은 사안은 1건으로 셈)".format(
        std.get("body_ref", ""), total)]
    for x in std["body_lines"]:
        if not x["k"]:
            out.append("   [ — ]  (사안마다 다른 내용 {}줄)".format(x["c"]))
        else:
            mark = "  " if x["n"] == total else "⚠ "
            out.append(" {}[{}/{}] {}{}".format(mark, x["n"], total, _clip(x["t"], 70),
                                               "" if x["n"] == total else "  ← 없는 문서: " + ",".join(x["miss"])))
    for x in std.get("body_dropped", []):
        out.append(" ⚠ [{}/{}] {}  ← 최근 문서엔 없음 (있던 문서: {})".format(
            x["n"], total, _clip(x["t"], 60), ",".join(x["docs"])))
    return out


def body_findings(std, title, text, atts, add):
    """본문 대조 → add(field, want, have, ok, fix=False, tip='')"""
    import difflib
    core = body_core(text)
    total = std.get("body_n", 0)
    if not core:
        add("본문", "", "본문을 못 읽음", True, tip="기안 창 본문에서 '제목'~'끝.'을 찾지 못했습니다")
    elif not std.get("body_lines"):
        add("본문", "", "본문 표준 없음", True, tip="서로 다른 사안의 문서가 2건 이상이어야 비교합니다")
    else:
        wset = {_bkey(ln) for ln in core}
        known = {x["k"] for x in std["body_lines"] if x["k"]} | {x["k"] for x in std.get("body_dropped", [])}
        new = [ln for ln in core if _bkey(ln) not in known]
        fixed = [x for x in std["body_lines"] if x["k"] and x["n"] == total]
        miss = [x for x in fixed if x["k"] not in wset]
        if not miss:
            add("본문", "", "표준 문장 {}줄 모두 있음 ({}건 공통)".format(len(fixed), total), True)
        for x in miss:
            near = difflib.get_close_matches(x["t"], new, n=1, cutoff=0.55)
            if near:
                new.remove(near[0])                 # 바뀐 줄은 '표준에 없는 줄'로 또 세지 않음
            add("본문", _clip(x["t"]), _clip(near[0]) if near else "(없음)", False,
                tip="{}건 모두에 있던 문장{}".format(total, "이 바뀜" if near else "이 빠짐"))
        for x in std["body_lines"]:
            if x["k"] and 1 < x["n"] < total and x["k"] not in wset:
                add("본문", "", "'{}' 없음".format(_clip(x["t"])), True,
                    tip="{}/{}건에 있던 문장".format(x["n"], total))
        for x in std.get("body_dropped", []):
            if x["k"] in wset:
                add("본문", "", "'{}' 있음".format(_clip(x["t"])), True,
                    tip="최근 문서 {}에선 뺀 문장 ({}/{}건)".format(std.get("body_ref", ""), x["n"], total))
        if new:
            add("본문", "", "표준에 없는 줄 {}개".format(len(new)), True, tip="이번 사안 내용이면 괜찮습니다")
    # 사안번호: 제목과 다른 사안번호가 본문·첨부에 남았나
    want = set(_CASENO_RE.findall(title or ""))
    if want:
        odd = sorted(set(_CASENO_RE.findall("\n".join(core))) - want)
        if odd:
            add("사안번호", "/".join(sorted(want)), "본문에 " + ", ".join(odd), False,
                tip="옛 사안번호가 남은 것 같습니다 → [바뀌는 값]에서 바꾸세요")
        bad_att = [a["name"] for a in atts if set(_CASENO_RE.findall(a["name"])) - want]
        if bad_att:
            add("사안번호", "/".join(sorted(want)), "첨부 " + ", ".join(bad_att), False,
                tip="옛 사안 첨부 같습니다 → [붙임 파일]에서 바꿔 끼우세요")
    # 본문 '붙임' 목록 ↔ 실제 첨부
    pairs, left = compare_butim(parse_butim(text), atts)
    if pairs or atts:
        miss_b = [b for b, hit in pairs if not hit]
        if not miss_b and not left:
            add("붙임↔첨부", "", "본문 붙임 {}개 = 첨부 {}개".format(len(pairs), len(atts)), True)
        for b in miss_b:
            add("붙임↔첨부", b, "맞는 첨부 없음", False, tip="본문 붙임에 적었는데 첨부가 없습니다")
        for n in left:
            add("붙임↔첨부", "(본문 붙임에 없음)", n, False, tip="첨부는 있는데 본문 붙임에 안 적혔습니다")


# --- 열린 기안 창 대조 ---

WIN_META_JS = r"""(function(){
  function sel(n){var e=document.getElementsByName(n);for(var i=0;i<e.length;i++) if(e[i].checked) return e[i].value; return '';}
  var ho=[]; for(var i=1;i<=8;i++){var c=document.getElementById('othbcAt'+i); if(c&&c.checked) ho.push(i);}
  var p=domHox1.selectNodes('/hox/approvalFlow/participant'), line=[];
  for(var i=0;i<p.length;i++) line.push(get_text(p[i],'position')+'|'+get_text(p[i],'name'));
  var t=domHox1.selectNodes('/hox/docInfo/taskInfo/taskCard'), task=[];
  for(var i=0;i<t.length;i++) task.push(get_text(t[i],'cardName')+' ('+get_text(t[i],'dcryName')+')');
  return [document.getElementById('Sj').value, sel('othbcLmtt'), ho.join(','), sel('readngLmtt'),
          (document.getElementById('ReadngTmlmtDe')||{}).value||'', line.join(';;'), task.join(';;'),
          get_text(domHox1,'/hox/docInfo/content/receiptInfo/recipientCount'),
          get_text(domHox1,'/hox/docInfo/content/receiptInfo/displayString')].join('\u0001');
})()"""


def read_window_meta(g):
    v = g.js(WIN_META_JS).split("\u0001")
    v += [""] * (9 - len(v))
    return {"title": v[0], "othbc": _WIN_OTHBC.get(v[1], ""), "ho": [int(x) for x in v[2].split(",") if x],
            "read": _WIN_READ.get(v[3], ""), "read_until": v[4],
            "line": [tuple(x.split("|", 1)) for x in v[5].split(";;") if x],
            "task": [x for x in v[6].split(";;") if x],
            "rcv_count": int(v[7] or 0) if (v[7] or "0").isdigit() else 0, "rcv": v[8]}


def next_mmdd(mmdd, today=None):
    """'03-02' → 오늘 이후 가장 가까운 그 날짜 (YYYY-MM-DD)."""
    from datetime import date
    today = today or date.today()
    try:
        m, d = (int(x) for x in mmdd.split("-"))
        cand = date(today.year, m, d)
        if cand <= today:
            cand = date(today.year + 1, m, d)
        return cand.isoformat()
    except Exception:
        return ""


def check_window(std, wm, atts, text=None):
    """표준 ↔ 열린 기안 창. → [{field, want, have, ok, fix(이 도구가 맞출 수 있나)}]  text=기안 창 본문 글자"""
    from datetime import date
    out = []
    F = std["fields"]

    def add(field, want, have, ok, fix=False, tip=""):
        out.append({"field": field, "want": want, "have": have, "ok": ok, "fix": fix and not ok, "tip": tip})

    want = F["line"]["value"]
    have = line_label(wm["line"])
    add("결재선", want, have, want == have, tip="" if want == have else "[결재경로]에서 직접 고치세요")
    want = F["task"]["value"]
    have = " / ".join(wm["task"])
    ok = _norm(want) == _norm(have)
    add("과제카드", want, have, ok, tip="" if ok else "[과제카드선택]에서 직접 고치세요")
    want = F["othbc"]["value"]
    have = othbc_label(wm["othbc"], wm["ho"])
    add("공개", want, have, want == have, fix=True)
    want = F["read"]["value"]
    have = READ.get(wm["read"], wm["read"])
    ok = want == have
    tip = ""
    if ok and wm["read"] == "2":
        try:
            if date.fromisoformat(wm["read_until"]) <= date.today():
                ok, tip = False, "지정날짜가 이미 지났습니다"
        except Exception:
            ok, tip = False, "지정날짜가 비어 있습니다"
        have += " " + (wm["read_until"] or "(날짜 없음)")
    add("열람제한", want + (" " + next_mmdd(std.get("read_mmdd", "")) if want == "지정날짜" and not ok else ""),
        have, ok, fix=True, tip=tip)
    want = F["rcv"]["value"]
    have = "내부결재" if wm["rcv_count"] == 0 else (wm["rcv"] or "수신자 {}곳".format(wm["rcv_count"]))
    ok = (want == "내부결재") == (wm["rcv_count"] == 0)
    add("수신", want, have, ok,
        tip="" if ok else "재작성하면 수신자가 빠집니다 → [수신자지정]에서 직접 넣으세요")
    have_norm = [_norm(att_stem(a["name"])) for a in atts]
    for st in std.get("atts_required", []):
        hit = _norm(st) in have_norm
        add("첨부", st, "있음" if hit else "없음", hit, tip="" if hit else "꼭 들어가던 첨부가 없습니다")
    known = {_norm(s) for s in std.get("atts_required", []) + std.get("atts_optional", [])}
    for a in atts:
        if _norm(att_stem(a["name"])) not in known:
            add("첨부", "(표준에 없음)", a["name"], True, tip="예전에 안 쓰던 첨부 — 맞는지 확인")
    if text is not None:
        body_findings(std, wm["title"], text, atts, add)
    return out


def fix_window(ie_hwnd, top_hwnd, std):
    """공개(구분+호수)·열람제한(+지정날짜)을 표준대로 맞춘다. 화면 버튼을 누르는 것과 같은 방식(click)."""
    with PromptGuard(top_hwnd):
        g = Gianki(ie_hwnd, top_hwnd)
        _mute_autosave_prompt(g)
        se, ho = parse_othbc_label(std["fields"]["othbc"]["value"])
        read = next((k for k, v in READ.items() if v == std["fields"]["read"]["value"]), "")
        until = next_mmdd(std.get("read_mmdd", "")) if read == "2" else ""
        js = r"""(function(se, ho, readId, until){
          var r=document.getElementById(%s[se]); if(r && !r.checked) r.click();
          for(var i=1;i<=8;i++){ var c=document.getElementById('othbcAt'+i); if(!c || c.disabled) continue;
            var want = ho.indexOf(i)>=0; if(c.checked!==want) c.click(); }
          if(readId){ var rr=document.getElementById(readId), clicked=false;
            if(rr && !rr.checked){ rr.click(); clicked=true; }
            var d=document.getElementById('ReadngTmlmtDe');
            if(readId==='readngLmtt1' && d){ var cur=d.value;
              var past = !cur || new Date(cur) <= new Date();
              if(until && (past || clicked)){ d.value=until; try{input_changed(d);}catch(e){} } } }
          return 'ok'; })(%s, %s, %s, %s)""" % (
            json.dumps(_OTHBC_RADIO), json.dumps(se), json.dumps(ho), json.dumps(_READ_RADIO.get(read, "")),
            json.dumps(until))
        g.js(js)
        wm = read_window_meta(g)
        atts = list_attachments(g)
        g.bring_front()
        return {"ok": True, "findings": check_window(std, wm, atts, g.body_text(1)), "wm": wm,
                "at": datetime.now().strftime("%H:%M:%S")}


def check_open_window(ie_hwnd, top_hwnd, stds):
    g = Gianki(ie_hwnd, top_hwnd)
    wm = read_window_meta(g)
    key = group_key(wm["title"])
    std = stds.get(key)
    if not std:
        return {"ok": False, "err": "이 기안 종류의 표준이 없습니다 ('{}')".format(wm["title"]), "key": key}
    return {"ok": True, "key": key, "name": std["name"], "confirmed": std.get("confirmed"),
            "findings": check_window(std, wm, list_attachments(g), g.body_text(1)), "wm": wm,
            "at": datetime.now().strftime("%H:%M:%S")}


# ───────────────────────── 7) 사안 카드 ─────────────────────────
#  사안번호(우리중-2026-N)마다 단계(접수→개최→결과 보고→자체해결/심의 요청→조치 이행)를 모아 보여 주고,
#  다음 단계 초안은 '그 단계의 가장 최근 기안'을 재작성해 만든다. 미리 채우는 것:
#   ① 사안번호  ② '1. 관련: 우리중학교-NNNN(날짜)' → 이 사안의 앞 단계 문서번호(결재완료일)  ③ 카드에 적어 둔 값(접수 일시 등)
#  원본이 가리킨 앞 단계 = 원본 사안에서 그 문서가 어느 단계였나로 정한다(원본마다 다를 수 있으므로).

CASE_FLOW = [
    ("학교폭력사안접수", "사안 접수"),
    ("학교폭력전담기구개최", "전담기구 개최"),
    ("학교폭력전담기구결과보고", "전담기구 결과 보고"),
    ("학교장자체해결결과보고", "자체해결 결과 보고"),
    ("학교폭력대책심의위원회개최요청", "심의위원회 개최 요청"),
    ("학교폭력대책심의위원회조치이행결과보고", "조치 이행 결과 보고"),
]
CASE_NEXT = {
    None: ["학교폭력사안접수"],
    "학교폭력사안접수": ["학교폭력전담기구개최"],
    "학교폭력전담기구개최": ["학교폭력전담기구결과보고"],
    "학교폭력전담기구결과보고": ["학교장자체해결결과보고", "학교폭력대책심의위원회개최요청"],
    "학교폭력대책심의위원회개최요청": ["학교폭력대책심의위원회조치이행결과보고"],
    "학교장자체해결결과보고": [],
    "학교폭력대책심의위원회조치이행결과보고": [],
}
CASE_LEARN_SKIP = {"사안번호", "사안 번호", "문서번호", "날짜", "시각", "관련 문서(앞 단계)", "관련 문서(직접 확인)"}
_REL_RE = re.compile(r"([가-힣]{2,}학교-\d{3,})\s?\((\d{4}\.\s?\d{1,2}\.\s?\d{1,2}\.)\)")
_EXT_REL_RE = re.compile(r"[가-힣]{2,}[가-힣A-Za-z0-9]*-\d{2,}\s?\(\d{4}\.\s?\d{1,2}\.\s?\d{1,2}\.\)")
_FLOW_NAME = dict(CASE_FLOW)
_FLOW_POS = {k: i for i, (k, _n) in enumerate(CASE_FLOW)}


def case_no(title):
    m = _CASENO_RE.findall(title or "")
    return m[0] if m else ""


def stage_name(key, title=""):
    return _FLOW_NAME.get(key) or _CASE_RE.sub("", _LEAD_RE.sub("", title or key)).strip()


def dot_date(yyyymmdd):
    """'20261116' → '2026. 11. 16.' (기안문 관련 줄 표기)."""
    d = re.sub(r"\D", "", yyyymmdd or "")[:8]
    if len(d) != 8:
        return ""
    return "{}. {}. {}.".format(d[:4], int(d[4:6]), int(d[6:8]))


def _case_sort_key(c):
    m = re.search(r"-(\d{4})-(\d+)$", c)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def next_case_no(cases, year=None):
    """가장 큰 번호 + 1 (같은 해). 접두어는 기존 사안을 따른다."""
    year = year or datetime.now().year
    nums = [(_case_sort_key(c)[1], c) for c in cases if _case_sort_key(c)[0] == year]
    prefix = (max(nums)[1].rsplit("-", 2)[0] if nums else
              (sorted(cases, key=_case_sort_key)[-1].rsplit("-", 2)[0] if cases else "우리중"))
    return "{}-{}-{}".format(prefix, year, (max(nums)[0] if nums else 0) + 1)


def build_cases(rows, saved):
    """rows(기안한문서 메타) + saved(카드 파일) → 사안 목록. 최근 사안 먼저, 끝난 사안은 뒤로."""
    from datetime import date
    groups = {}
    for r in rows or []:
        c = case_no(r["title"])
        if c:
            groups.setdefault(c, []).append(r)
    for c in saved or {}:
        groups.setdefault(c, [])
    out = []
    for c, rs in groups.items():
        rs = sorted(rs, key=lambda r: (r.get("draft", ""), r["docno"]))
        card = (saved or {}).get(c, {})
        flow_done = [r for r in rs if r["key"] in _FLOW_POS]
        last = flow_done[-1]["key"] if flow_done else None
        nxt = CASE_NEXT.get(last, [])
        closed = card.get("closed")
        if closed is None:
            closed = bool(last) and not nxt
        start = next((r for r in rs if r["key"] == "학교폭력사안접수"), rs[0] if rs else None)
        days = ""
        if start:
            try:
                d = start["draft"]
                days = (date.today() - date(int(d[:4]), int(d[4:6]), int(d[6:8]))).days
            except Exception:
                days = ""
        out.append({"case": c, "docs": rs, "last": last, "next": [] if closed else nxt, "closed": closed,
                    "days": days, "vars": dict(card.get("vars", {})), "memo": card.get("memo", "")})
    out.sort(key=lambda x: (x["closed"], [-v for v in _case_sort_key(x["case"])]))
    return out


def case_stages(case):
    """카드 한 장의 단계 표: 본 흐름 단계(완료/다음/—) + 그 밖에 올린 문서."""
    done = {}
    for r in case["docs"]:
        done.setdefault(r["key"], []).append(r)
    out = []
    for k, name in CASE_FLOW:
        rs = done.get(k, [])
        if rs:
            for r in rs:
                out.append({"key": k, "name": name, "docno": r["docno"], "draft": r["draft"], "done": r.get("done", ""),
                            "state": "완료"})
        else:
            out.append({"key": k, "name": name, "docno": "", "draft": "", "done": "",
                        "state": "다음" if k in case["next"] else ""})
    for r in case["docs"]:
        if r["key"] not in _FLOW_POS:
            out.append({"key": r["key"], "name": stage_name(r["key"], r["title"]), "docno": r["docno"],
                        "draft": r["draft"], "done": r.get("done", ""), "state": "완료(그 밖)"})
    return out


def _first_rel(r):
    for ln in (r.get("body") or [])[:5]:
        m = _REL_RE.search(ln)
        if m:
            return m
    return None


def ref_stage(rows, key):
    """예전 'key' 단계 문서들의 '관련:'이 같은 사안의 어느 단계를 가리켰나 (가장 많이 나온 것)."""
    by_doc = {r["docno"]: r for r in rows}
    cnt = {}
    for r in sorted(rows, key=lambda r: r.get("draft", ""), reverse=True):
        if r["key"] != key:
            continue
        m = _first_rel(r)
        ref = by_doc.get(m.group(1)) if m else None
        if ref and case_no(ref["title"]) and case_no(ref["title"]) == case_no(r["title"]):
            cnt[ref["key"]] = cnt.get(ref["key"], 0) + 1
    return max(cnt, key=lambda k: cnt[k]) if cnt else None      # 동률이면 최근 문서 쪽(먼저 센 것)


def plan_stage(rows, case, key):
    """사안 case 의 단계 key 초안 계획 → {src 원본 문서번호, src_title, pairs:[{label, old, new}], notes:[...]} 또는 {err}."""
    cands = sorted([r for r in rows if r["key"] == key], key=lambda r: (r.get("draft", ""), r["docno"]), reverse=True)
    if not cands:
        return {"err": "'{}' 단계로 올린 예전 기안이 기안한문서 목록에 없습니다.\n"
                       "처음 한 번은 에듀파인에서 직접 쓰시면 다음부터 카드로 만들 수 있습니다.".format(stage_name(key))}
    other = [r for r in cands if case_no(r["title"]) != case]     # 다른 사안 문서를 원본으로 (같은 사안 재기안이면 그것도 허용)
    src = (other or cands)[0]
    pairs, notes = [], []
    sc_ = case_no(src["title"])
    if sc_ and sc_ != case:
        pairs.append({"label": "사안번호", "old": sc_, "new": case})
    by_doc = {r["docno"]: r for r in rows}
    m = _first_rel(src)
    if m:
        ref = by_doc.get(m.group(1))
        ref_key = ref["key"] if ref and case_no(ref["title"]) == sc_ else ref_stage(rows, key)
        tgt = None
        if ref_key:
            mine = [r for r in rows if case_no(r["title"]) == case and r["key"] == ref_key]
            mine.sort(key=lambda r: (r.get("draft", ""), r["docno"]))
            tgt = mine[-1] if mine else None
        if tgt and dot_date(tgt.get("done") or tgt.get("draft")):
            new = "{}({})".format(tgt["docno"], dot_date(tgt.get("done") or tgt.get("draft")))
            pairs.append({"label": "관련 문서(앞 단계)", "old": m.group(0), "new": new})
            notes.append("관련: 이 사안의 '{}' 문서 {} (결재완료 {}){}".format(
                stage_name(ref_key, tgt["title"]), tgt["docno"], dot_date(tgt.get("done") or tgt.get("draft")),
                "" if ref else " — 예전 '{}' 문서들이 가리키던 단계로 정함".format(stage_name(key))))
        else:
            why = ("이 사안의 '{}' 문서가 목록에 없음".format(stage_name(ref_key)) if ref_key
                   else "원본이 가리킨 {}가 기안한문서 목록에 없음(받은 공문 등)".format(m.group(1)))
            pairs.append({"label": "관련 문서(직접 확인)", "old": m.group(0), "new": m.group(0)})
            notes.append("⚠ 관련 줄 '{}' — {} → 직접 고치세요".format(m.group(0), why))
    else:
        ext = None
        for ln in (src.get("body") or [])[:5]:
            ext = _EXT_REL_RE.search(ln)
            if ext:
                break
        if ext:                                   # 교육청 공문 등 받은 문서 → 사안마다 달라 직접 넣어야 함
            pairs.append({"label": "관련 문서(직접 확인)", "old": ext.group(0), "new": ext.group(0)})
            notes.append("⚠ 관련 줄 '{}' — 받은 공문 번호라 사안마다 다릅니다 → 이 사안 공문으로 고치세요".format(
                ext.group(0)))
    return {"src": src["docno"], "src_title": src["title"], "pairs": pairs, "notes": notes}


def load_cases(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cases(path, cases):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False, indent=1)
