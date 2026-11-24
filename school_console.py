# -*- coding: utf-8 -*-
"""
학교업무 통합 콘솔 (school_console.py)
=====================================================================
원본(sen_login_keepalive.ahk + session_keeper.py)을 GUI 하나로 통합한 버전.

설계 원칙
  1) "확실히 되는 것"만 자동화한다.
       - 로그인 자동화 실행, 사이트 열기, 세션 유지, 템플릿 → 클립보드
  2) 세션 유지를 검은 콘솔창이 아니라 GUI에서 실시간 상태로 보여준다.
  3) UI는 절대 멈추지 않는다 → 모든 CDP 통신은 백그라운드 스레드에서.
  4) 나이스 4세대는 eXbuilder6(window.cpr / .cl-*)다 — iframe 없는 진짜 DOM이라
     좌표를 추측하지 말고 선택자로 행·셀을 집는다. (K-에듀파인 쪽이 Nexacro다)
  5) 화면에서 값을 "읽기"만 하는 조회/관제는 입력이 없어 안전하다.

주의
  - 이 콘솔은 크롬을 직접 띄우지 않는다. AHK가 띄운 '디버그 모드 크롬'(포트 9222)에
    "붙어서" 동작한다. 따라서 먼저 [로그인 자동화 실행]으로 크롬을 띄워야 한다.
  - 출결은 [저장]까지만 하고, '출결마감'(확정)과 복무 '상신'은 누르지 않는다(설계상 의도).
    저장도 무조건 하지 않고, 입력이 끝난 뒤 사람에게 한 번 물어본 다음에 누른다.

필요 패키지:  pip install websocket-client  (plyer는 선택)
"""

import json
import os
import queue
import struct
import subprocess
import sys
import threading
import time
import re
import urllib.request
import urllib.parse
import webbrowser
from datetime import datetime, date, timedelta
from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

try:
    from websocket import create_connection  # pip install websocket-client
    HAS_WS = True
except ImportError:
    HAS_WS = False

try:
    import gian_draft  # K-에듀파인 반복 기안 초안 도우미 (comtypes 필요)
    HAS_GIAN = True
except Exception:
    HAS_GIAN = False

try:
    import pymysql  # pip install pymysql — 알림장 전달사항을 학교 MySQL에서 직접 읽기(순수 파이썬)
    HAS_PYMYSQL = True
except Exception:
    HAS_PYMYSQL = False

# exe(PyInstaller)로 실행 시엔 exe가 있는 폴더, .py로 실행 시엔 스크립트 폴더를 기준으로
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "school_console_config.json"
PROFILE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "SenKeepAliveProfile")

CHROME_CANDIDATES = [
    os.path.join(os.environ.get("ProgramFiles", ""), r"Google\Chrome\Application\chrome.exe"),
    os.path.join(os.environ.get("ProgramFiles(x86)", ""), r"Google\Chrome\Application\chrome.exe"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), r"Google\Chrome\Application\chrome.exe"),
]

DEFAULT_CONFIG = {
    "debug_port": 9222,
    "check_interval_sec": 300,          # 세션 갱신 주기(초). 300=5분 (서버 세션 sessionExtension.do)
    # 나이스 '연장하시겠습니까?' 팝업 감시 주기(초). 나이스는 절대 60분 카운트다운이라
    # 팝업을 놓치면 서버세션이 살아있어도 클라이언트가 스스로 로그아웃함 → 짧게 감시.
    "neis_guard_sec": 20,
    # 나이스 절대 60분 카운트다운 대비 — 잔여시간이 임박하면 페이지를 리로드해 세션을 '확실히' 리셋한다.
    #  (실측: reload는 카운트다운을 60분으로 복구+로그인 유지. 팝업 클릭보다 안정적.)
    #  neis_reload_below_min: 백그라운드 탭에서 잔여 이 분 이하면 리로드(작업 방해 없음).
    #  neis_reload_critical_min: 전면 탭이어도 이 분 이하면 리로드(로그아웃보다 리로드가 나음). 0=끔.
    # 나이스 접속시간이 이 분 이하로 남으면 '화면 새로고침 없이' 60분으로 되돌린다([연장] 버튼과 같은 동작). 0=끔.
    "neis_extend_below_min": 30,
    # 나이스가 "이 접속은 무효"라고 답하면(포털 재로그인 뒤 흔함) 옛 쿠키를 지우고 SSO로 자동 재접속. False=끔.
    "neis_auto_reenter": True,
    "neis_reload_below_min": 12,
    "neis_reload_critical_min": 4,
    # 콘솔이 켜질 때 자동으로 시작할 기능 (AHK가 콘솔을 띄우면 원클릭 운영)
    "autostart": {"keeper": True, "monitor": False},
    # 🔊 음성(TTS) 알림 — 결재/서류 알림을 소리로도 읽어줌
    "tts_enabled": False,
    # 🍚 급식·학사일정 (나이스 교육정보 개방 API — 로그인 불필요, 학교코드만)
    #   학교 바꾸려면: https://open.neis.go.kr 에서 학교 검색 후 코드 입력
    "neis_open": {"school": "○○중학교", "atpt": "B10", "scode": ""},
    # 🗓 마감 D-Day — {name, date "YYYY-MM-DD"} 목록. lead_days일 전부터 알림
    "deadline_lead_days": 3,
    "deadlines": [],
    "paths": {
        "ahk_script": "sen_login_keepalive.ahk",
        "ahk_exe": "",                   # 비우면 더블클릭 실행(os.startfile)
        "chrome_exe": "",                # 비우면 자동 탐지
        "alim_exe": "C:\\알림장\\ALIM.EXE",   # 알림장 프로그램 (없으면 '앱 열기' 비활성)
        # 컴시간 알림이가 시간표를 캐시해 두는 폴더 (교사/학급 시간표를 서버 없이 읽음)
        "comci_dir": "C:\\Program Files (x86)\\알림이\\dat\\tmp"
    },
    # 알림장 전달사항을 «앱을 띄우지 않고» 학교 MySQL에서 직접 읽기 위한 접속값.
    # (ALIM.EXE 자신이 쓰는 기본 접속정보 — 내 PC의 내 학교 자료. 학교망 안에서만 열림)
    #   host 비우면 MYSET.DBF의 SUIP에서 자동으로 찾음.
    "alim_db": {
        "enabled": True,
        "host": "",
        "port": 3308,                # 봉샘 알림장 MySQL 실제 포트(우리학교 실측). 3306 아님.
        "port_fallback": [3306],     # 학교마다 다를 수 있어 자동으로 한 번 더 시도
        "user": "",
        "password": "",
        "db": "alim",
        "charset": "euckr",
        "myset_path": "C:\\알림장\\MYSET.DBF",
        "agicho_path": "C:\\알림장\\AUTOBACK\\AGICHO.DBF",
        "timeout": 4
    },
    "urls": {
        "업무포털": "https://sen.eduptl.kr",
        "나이스": "https://sen.neis.go.kr/jsp/main.jsp",
        "K-에듀파인": "https://klef.sen.go.kr",
        "E알리미": "https://www.ealimi.com/Document/SignListReady"
    },
    # 교실 호출 — 교무실에서 보내면 교실 스마트폰(표시기.html)에 크게 뜸. 통로=ntfy.sh(무료).
    #   topic: 교실 표시기와 '똑같이' 맞춰야 함(채널 이름). presets: 빠른 장소 버튼.
    "classroom_call": {
        "topic": "myschool-call-CHANGEME",   # 기본 이름(base). 실제로 보내는 곳은 반별 토픽 base-학년-반 (전체 방송 없음)
        # 교실 표시기(표시기.html)를 올려둔 주소. 채우면 '교실 설치 도우미'가 반별 주소를 만들어 줌.
        #   예) https://아이디.github.io/call/표시기.html  → 2학년 8반: ...표시기.html?c=2-8
        "display_url": "",
        # 전자칠판 전용 앱(교실호출.apk)에 '학교망에서 바로' 보내기.
        #   send_mode: lan_first=학교망 직접 먼저, 안 되면 ntfy로 자동 전환(권장)
        #              ntfy_only=예전처럼 인터넷 중계만   lan_only=학교망 직접만(외부 서버 안 씀)
        "send_mode": "lan_first",
        "lan_port": 8787,
        "lan_key": "",                   # 앱에 암호를 넣었다면 같은 값
        "boards": {},                    # {"2-8": {"ip": "10.1.2.3", "port": 8787, "room": ""}}
        "sender": "",   # 보내는 사람(송신자) 기본값 — 표시기에 '~로부터'로 뜸. 보낼 때 마지막 값 저장됨.
        "class_counts": {"1": 6, "2": 8, "3": 6},   # 학년별 반 수(현장 맞게 수정 가능)
        "presets": ["교무실로 오세요", "6층 생활교육부로 오세요", "상담실로 오세요",
                    "보건실로 오세요", "교실로 들어오세요"]
    },
    # 세션을 유지할 대상 탭 (url에 match 문자열이 포함된 탭)
    #   mode neis=/sessionExtension.do 직접호출, edupine=Nexacro 연장버튼 신뢰클릭, button=글자클릭
    "keepalive_targets": [
        {"name": "나이스(NEIS)", "match": "sen.neis.go.kr", "mode": "neis"},
        {"name": "K-에듀파인", "match": "klef.sen.go.kr", "mode": "edupine"}
    ],
    # 결재 관제: 페이지에서 건수를 읽어 늘면 데스크톱 알림 (크롬 확장 2종을 콘솔로 통합)
    "monitor_interval_sec": 180,        # 관제 확인 주기(초). 180=3분 (확장 기본값과 동일)
    "monitors": [
        {"name": "나이스 승인사항", "match": "sen.eduptl.kr", "type": "neis", "refresh": True},
        {"name": "K-에듀파인 결재/공람", "match": "klef.sen.go.kr", "type": "edufine"},
        {"name": "E알리미 결재서류", "match": "ealimi.com", "type": "ealimi"}
    ],
    # '세션 연장' 버튼으로 인식할 텍스트(정확히 일치할 때만 클릭)
    "extend_keywords": ["연장", "세션연장", "시간연장", "세션 연장", "시간 연장",
                        "연장하기", "계속사용", "계속 사용", "시간연장하기"],
    # 복무 사유 기본 문구
    "leave_reason_presets": {
        "연가": "개인 사유로 연가를 신청합니다.",
        "병가": "진료 및 안정이 필요하여 병가를 신청합니다.",
        "조퇴": "개인 사정으로 조퇴를 신청합니다.",
        "지각": "부득이한 사유로 지각을 신청합니다.",
        "외출": "업무/개인 사유로 외출을 신청합니다.",
        "공가": "공무 수행 관련 사유로 공가를 신청합니다.",
        "출장": "업무 수행을 위해 출장을 신청합니다."
    },
    # 조회 대시보드 — 문서 목록을 읽어올 대상(열려 있는 '목록 화면' 기준)
    "doclist_sources": [
        {"name": "K-에듀파인", "match": "klef.sen.go.kr"},
        {"name": "E알리미", "match": "ealimi.com"},
        {"name": "나이스", "match": "sen.neis.go.kr"}
    ],
    # 출결 미마감 점검 — 나이스 '출결 현황' 화면을 연 상태에서 점검
    "attendance": {
        "match": "sen.neis.go.kr",
        "deadline": "15:00",                      # 이 시각 이후 미마감이면 알림
        "class_re": "(\\d학년\\s*\\d{1,2}반|\\d{1,2}\\s*-\\s*\\d{1,2})",
        "unclosed_words": ["미마감", "미입력", "미처리", "미완료"],
        "closed_words": ["마감", "완료", "처리완료", "입력완료"]
    }
}


# ============================================================
#  설정 로드/저장
# ============================================================
def load_config():
    if CONFIG_PATH.exists():
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    else:
        data = {}
    # 누락 키 보강
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for k, v in data.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k].update(v)
        else:
            merged[k] = v
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def save_config(cfg):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_path(raw):
    if not raw:
        return None
    p = Path(raw)
    if p.is_absolute():
        return p
    if (APP_DIR / raw).exists():
        return (APP_DIR / raw).resolve()
    if (APP_DIR.parent / raw).exists():
        return (APP_DIR.parent / raw).resolve()
    return (APP_DIR / raw).resolve()


def find_chrome(cfg):
    custom = cfg["paths"].get("chrome_exe", "").strip()
    if custom and os.path.exists(custom):
        return custom
    for c in CHROME_CANDIDATES:
        if c and os.path.exists(c):
            return c
    return None


# ============================================================
#  CDP (Chrome DevTools Protocol) — iframe까지 들어가는 엔진
# ============================================================
# 페이지 안에서 실행되는 공통 헬퍼: 동일 출처 iframe 안까지 재귀로 훑는다.
# (나이스 WebSquare 화면은 iframe 다중 구조라, top document만 보면 못 찾음)
FRAME_WALK = r"""
function __collectDocs(){
  var docs = [document];
  function dive(doc, depth){
    if (depth > 6) return;
    var frames = doc.querySelectorAll('iframe,frame');
    for (var i=0;i<frames.length;i++){
      var d = null;
      try { d = frames[i].contentDocument; } catch(e){ d = null; }
      if (d){ docs.push(d); dive(d, depth+1); }
    }
  }
  try { dive(document, 0); } catch(e){}
  return docs;
}
"""

# ── 세션 유지 스크립트 (session_keeper.py v2와 동일한 '진짜' 로직) ──
#  핵심: 옛날 방식(fetch(main)+가짜 마우스)은 진짜 연장 경로를 안 건드려 나이스가 만료됐음.
#  그래서 사이트별 전용 모드로 분리한다.

# 나이스: 자기 사이트가 쓰는 그대로 /sessionExtension.do 직접 호출 + 연장창 자동확인
NEIS_KEEPALIVE_JS = r"""
(async function () {
  var out = { ok:false, body:'', dialog:false, btnX:0, btnY:0 };
  try {
    var r = await fetch('/sessionExtension.do',
                        { method:'POST', credentials:'include', cache:'no-store' });
    out.body = (await r.text() || '').replace(/\s+/g,' ').trim();
    out.ok = (r.status === 200) && (out.body.indexOf('Y') !== -1);
  } catch (e) { out.body = 'ERR:' + e; }
  try {
    var dlgOpen = false;
    var texts = document.querySelectorAll('.cl-text');
    for (var i=0;i<texts.length;i++){
      var tt = (texts[i].innerText || '');
      if (/연장하시겠습니까|접속유지시간/.test(tt)) { dlgOpen = true; break; }
    }
    out.dialog = dlgOpen;
    if (dlgOpen) {
      var btns = document.querySelectorAll('.cl-button, button, a, .cl-text');
      for (var j=0;j<btns.length;j++){
        var t = (btns[j].innerText || btns[j].value || '').replace(/\s+/g,' ').trim();
        if (t === '확인' || t === '예' || t === '연장') {
          var b = btns[j].getBoundingClientRect();
          if (b.width > 0 && b.height > 0) {
            out.btnX = Math.round(b.left + b.width/2);
            out.btnY = Math.round(b.top + b.height/2);
            break;
          }
        }
      }
    }
  } catch (e) {}
  return JSON.stringify(out);
})();
"""

# 에듀파인(Nexacro): 타이머값 + '연장' 버튼 화면좌표 찾기 (DOM 클릭 안 먹힘 → 신뢰클릭 필요)
EDU_FIND_JS = r"""
(function () {
  var out = { timer:'', x:0, y:0 };
  var t = document.querySelector('[id$="staUseTime:text"]');
  if (t) out.timer = (t.innerText || '').trim();
  var b = document.querySelector('[id$="form.btnUseTimeExtn"]');
  if (!b) {
    var all = document.querySelectorAll('div');
    for (var i=0;i<all.length;i++){
      if ((all[i].innerText || '').trim() === '연장') { b = all[i]; break; }
    }
  }
  if (b) {
    var r = b.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) {
      out.x = Math.round(r.left + r.width/2);
      out.y = Math.round(r.top + r.height/2);
    }
  }
  return JSON.stringify(out);
})();
"""

# 일반(button) 모드: 화면의 '연장' 버튼을 글자로 찾아 DOM 클릭 + 서버 GET
BUTTON_KEEPALIVE_JS = r"""
(function () {
  var result = { clicked:false, fetched:false, found:[] };
  var keywords = %KEYWORDS%;
  try {
    var els = document.querySelectorAll('a,button,input[type=button],input[type=submit],span,div,td,li');
    for (var i=0;i<els.length;i++){
      var el = els[i];
      var txt = (el.innerText || el.value || '').replace(/\s+/g,' ').trim();
      if (!txt || txt.length > 12) continue;
      if (keywords.indexOf(txt) !== -1){
        try { el.click(); result.clicked = true; result.found.push(txt); } catch(e){}
      }
    }
  } catch (e) {}
  try { fetch(window.location.href, {method:'GET', cache:'no-store', credentials:'include'}); result.fetched = true; } catch(e){}
  return JSON.stringify(result);
})();
""".replace("%KEYWORDS%", json.dumps(DEFAULT_CONFIG["extend_keywords"], ensure_ascii=False))


def _mmss_to_sec(s):
    """'mm:ss' → 초. 실패 시 -1."""
    try:
        m, ss = s.split(":")
        return int(m) * 60 + int(ss)
    except Exception:
        return -1


def _infer_mode(target):
    """config에 mode가 없으면 match 문자열로 추론."""
    mode = target.get("mode")
    if mode:
        return mode
    m = (target.get("match") or "").lower()
    if "neis.go.kr" in m:
        return "neis"
    if "klef" in m or "edufine" in m or "edupine" in m:
        return "edupine"
    return "button"

# ── 결재 관제: 페이지에서 건수/목록 읽기 (확장 content.js의 읽기 로직을 CDP로 이식) ──
# 에듀파인: 상단 '결재(긴급)'·'공람' 숫자를 정규식으로 (iframe 내부까지 합쳐서)
EDUFINE_COUNT_JS = FRAME_WALK + r"""
(function(){
  var docs = __collectDocs();
  var text = '';
  for (var i=0;i<docs.length;i++){
    try { var b = docs[i].body; if (b) text += ' ' + (b.innerText || b.textContent || ''); } catch(e){}
  }
  var out = {};
  var m1 = text.match(/결재\s*(?:\(\s*긴급\s*\))?\s*[:：]?\s*(\d+)/);
  if (m1) out['결재'] = parseInt(m1[1], 10);
  var m2 = text.match(/공람\s*[:：]?\s*(\d+)/);
  if (m2) out['공람'] = parseInt(m2[1], 10);
  return JSON.stringify(out);
})();
"""

# 나이스 승인사항(업무포털 대시보드 또는 나이스 본사이트): '미결/협조함'·'공람함' 숫자
#  라벨→숫자 / 숫자→라벨 양방향 정규식으로 그리드/표 배치 차이에 대응 (iframe 내부까지)
NEIS_COUNT_JS = FRAME_WALK + r"""
(function(){
  var docs = __collectDocs();
  var text = '';
  for (var i=0;i<docs.length;i++){
    try { var b = docs[i].body; if (b) text += ' ' + (b.innerText || b.textContent || ''); } catch(e){}
  }
  text = text.replace(/\s+/g,' ');
  var out = {};
  function grab(key, res){
    for (var i=0;i<res.length;i++){ var m = text.match(res[i]); if (m){ out[key] = parseInt(m[1],10); return; } }
  }
  grab('미결/협조', [/미결\s*\/\s*협조함?\s*[:：]?\s*(\d+)/, /(\d+)\s*건?\s*미결\s*\/\s*협조/]);
  grab('예결함',   [/예결함\s*[:：]?\s*(\d+)/, /(\d+)\s*건?\s*예결함/]);
  grab('공람함',   [/공람함\s*[:：]?\s*(\d+)/, /(\d+)\s*건?\s*공람함/]);
  return JSON.stringify(out);
})();
"""

# E알리미: 결재 목록 표에서 날짜가 든 행을 결재서류로 수집 (iframe 내부까지)
EALIMI_DOCS_JS = FRAME_WALK + r"""
(function(){
  var DATE_RE = /\d{4}-\d{2}-\d{2}/;
  var docs = __collectDocs();
  var rows = [];
  for (var d=0; d<docs.length; d++){
    var trs = docs[d].querySelectorAll('tr');
    for (var i=0;i<trs.length;i++){
      var cells = Array.prototype.map.call(trs[i].querySelectorAll('td'), function(td){
        return (td.innerText || td.textContent || '').replace(/\s+/g,' ').trim();
      });
      if (cells.length < 2) continue;
      var joined = cells.join(' ');
      if (!DATE_RE.test(joined)) continue;
      var title = null;
      for (var c=0;c<cells.length;c++){ if (/신고서|신청서|결석|체험학습|결재/.test(cells[c])){ title = cells[c]; break; } }
      if (!title){ var sorted = cells.slice().sort(function(a,b){return b.length-a.length;}); title = sorted[0] || ''; }
      var date = '';
      for (var e=0;e<cells.length;e++){ if (DATE_RE.test(cells[e])){ date = cells[e]; break; } }
      if (!title) continue;
      rows.push({title:title, date:date});
    }
  }
  return JSON.stringify(rows);
})();
"""


# ── 조회 대시보드: 결재/문서 '목록 화면'을 읽어 행 단위로 수집 (클릭 좌표 포함) ──
#  각 행의 화면 좌표(x,y)를 함께 돌려줘, 콘솔에서 '선택 항목 열기' 시 그 좌표에 신뢰클릭.
DOCLIST_JS = FRAME_WALK + r"""
(function(){
  var DATE_RE=/\d{4}[-.\/]\d{1,2}[-.\/]\d{1,2}|\d{1,2}[-.\/]\d{1,2}/;
  var KW=/신청|신고|결석|체험학습|기안|결재|공람|보고|품의|지출|계획|협조|승인|반려|수신|공문|문서|상신/;
  var docs=__collectDocs();
  var rows=[]; var seen={};
  function vis(el){ try{ var r=el.getBoundingClientRect(); return r.width>3 && r.height>3; }catch(e){ return false; } }
  for (var d=0; d<docs.length; d++){
    var trs=docs[d].querySelectorAll('tr,[class*="grid"] [class*="row"],[role="row"],li');
    for (var i=0;i<trs.length;i++){
      var tr=trs[i];
      var cellEls=tr.querySelectorAll('td,th,[class*="cell"],[role="gridcell"]');
      var cells=Array.prototype.map.call(cellEls,function(c){ return (c.innerText||c.textContent||'').replace(/\s+/g,' ').trim(); });
      if (!cells.length){ var t0=(tr.innerText||'').replace(/\s+/g,' ').trim(); if (t0) cells=[t0]; }
      cells=cells.filter(function(c){ return c; });
      if (!cells.length) continue;
      var joined=cells.join(' ');
      if (joined.length<4 || joined.length>200) continue;
      if (!DATE_RE.test(joined) && !KW.test(joined)) continue;
      if (!vis(tr)) continue;
      if (seen[joined]) continue; seen[joined]=1;
      var date=''; for (var c=0;c<cells.length;c++){ if (DATE_RE.test(cells[c])){ date=cells[c]; break; } }
      var title=''; var best=-1;
      for (var c2=0;c2<cells.length;c2++){ var cc=cells[c2]; if (DATE_RE.test(cc)) continue; if (KW.test(cc) && cc.length>best){ title=cc; best=cc.length; } }
      if (!title){ var srt=cells.filter(function(c){ return !DATE_RE.test(c); }).sort(function(a,b){ return b.length-a.length; }); title=srt[0]||cells[0]; }
      var sub=''; for (var c3=0;c3<cells.length;c3++){ var s=cells[c3]; if (s && s!==title && !DATE_RE.test(s) && s.length<=12){ sub=s; break; } }
      var rc=tr.getBoundingClientRect();
      rows.push({ title:(title||'').slice(0,90), sub:sub, date:date,
                  x:Math.round(rc.left+Math.min(rc.width/2,180)), y:Math.round(rc.top+rc.height/2) });
      if (rows.length>=80) break;
    }
    if (rows.length>=80) break;
  }
  return JSON.stringify(rows);
})();
"""

# ── 출결 미마감 점검: '학급'과 '마감/미마감' 상태가 같이 든 행을 찾음 (패턴 설정 가능) ──
ATTENDANCE_JS_TMPL = FRAME_WALK + r"""
(function(){
  var re=new RegExp(%CLASS_RE%);
  var unclosed=%UNCLOSED%, closed=%CLOSED%;
  var docs=__collectDocs(); var out=[]; var seen={};
  for (var d=0; d<docs.length; d++){
    var rows=docs[d].querySelectorAll('tr,[class*="row"],[role="row"],li');
    for (var i=0;i<rows.length;i++){
      var t=(rows[i].innerText||rows[i].textContent||'').replace(/\s+/g,' ').trim();
      if (!t || t.length>140) continue;
      var m=t.match(re); if (!m) continue;
      var cls=m[0].replace(/\s+/g,'');
      var status='';
      for (var u=0;u<unclosed.length;u++){ if (t.indexOf(unclosed[u])!==-1){ status='미마감'; break; } }
      if (!status){ for (var c=0;c<closed.length;c++){ if (t.indexOf(closed[c])!==-1){ status='마감'; break; } } }
      if (!status) continue;
      var key=cls+'|'+status; if (seen[key]) continue; seen[key]=1;
      out.push({ cls:cls, status:status });
    }
  }
  return JSON.stringify(out);
})();
"""


# ── 수행평가 누락 확인: 나이스 '수행평가성적관리' 표를 읽어 점수 빈 학생 찾기 ──
#  안정적인 한글 머리글 '점수'(각 수행평가의 점수 열)·'성명'(학생 열)을 기준점으로
#  좌표를 잡아 표를 격자로 복원한다(클래스명 추측 X, 좌표 기반이라 덜 깨짐).
#  주의: 나이스가 스크롤 시 행을 동적 생성하면 '화면에 그려진 행'만 읽힐 수 있음.
PERF_MISSING_JS = FRAME_WALK + r"""
(function(){
  var docs=__collectDocs();
  function vis(el){ try{ var r=el.getBoundingClientRect(); return r.width>3 && r.height>3; }catch(e){ return false; } }
  function rc(el){ var r=el.getBoundingClientRect(); return {x:r.left+r.width/2, y:r.top+r.height/2}; }
  function leaf(el){ return !(el.children && el.children.length>0); }

  // 1) '점수' 서브헤더 = 각 수행평가의 점수 열 x좌표
  var scoreCols=[], subY=null;
  for (var d=0; d<docs.length; d++){
    var cs=docs[d].querySelectorAll('td,th,div,span');
    for (var i=0;i<cs.length;i++){
      var c=cs[i]; if(!leaf(c)) continue;
      var t=(c.innerText||c.textContent||'').replace(/\s+/g,' ').trim();
      if (t!=='점수' || !vis(c)) continue;
      var p=rc(c); scoreCols.push(p.x); if(subY===null || p.y<subY) subY=p.y;
    }
  }
  if(!scoreCols.length) return JSON.stringify({error:'no-score-header'});
  scoreCols.sort(function(a,b){return a-b;});
  // 가까운 중복 제거
  var sc=[]; for(var s=0;s<scoreCols.length;s++){ if(!sc.length||Math.abs(scoreCols[s]-sc[sc.length-1])>20) sc.push(scoreCols[s]); }
  scoreCols=sc;

  // 2) 수행평가 제목(점수 헤더보다 위쪽의 텍스트)
  var titles=[];
  var EXCL=/^(점수|결시명칭|학적변동|만점|합계|평균|성명|반|번호|학년도|학기|학년|과목|영역|강의실|순위|수강생|응시생|결시생|학적변동수)/;
  for (var d2=0; d2<docs.length; d2++){
    var hs=docs[d2].querySelectorAll('td,th,div,span');
    for (var j=0;j<hs.length;j++){
      var h=hs[j]; if(!leaf(h)) continue;
      var ht=(h.innerText||h.textContent||'').replace(/\s+/g,' ').trim();
      if (ht.length<3 || ht.length>45) continue;
      if (EXCL.test(ht) || /^만점\(\d+\)$/.test(ht)) continue;
      if (!vis(h)) continue;
      var hp=rc(h); if(hp.y>=subY-3) continue;
      titles.push({text:ht, x:hp.x});
    }
  }
  function titleFor(x){ var b='',bd=1e9; for(var k=0;k<titles.length;k++){var dd=Math.abs(titles[k].x-x); if(dd<bd){bd=dd;b=titles[k].text;}} return bd<150?b:''; }

  // 3) '성명' 열 x
  var nameColX=null;
  for (var d3=0; d3<docs.length; d3++){
    var ns=docs[d3].querySelectorAll('td,th,div,span');
    for (var m=0;m<ns.length;m++){
      var e3=ns[m]; if(!leaf(e3)) continue;
      var tt=(e3.innerText||e3.textContent||'').replace(/\s+/g,' ').trim();
      if (tt==='성명' && vis(e3)){ nameColX=rc(e3).x; break; }
    }
    if(nameColX!==null) break;
  }

  // 4) 학생 이름 행 (성명 열 근처의 한글 이름)
  var NAME_RE=/^[가-힣]{2,4}$/, rows=[];
  for (var d4=0; d4<docs.length; d4++){
    var es=docs[d4].querySelectorAll('td,div,span,a');
    for (var n=0;n<es.length;n++){
      var e=es[n]; if(!leaf(e)) continue;
      var nt=(e.innerText||e.textContent||'').replace(/\s+/g,' ').trim();
      if (!NAME_RE.test(nt) || !vis(e)) continue;
      var ep=rc(e);
      if (ep.y<=subY) continue;
      if (nameColX!==null && Math.abs(ep.x-nameColX)>80) continue;
      rows.push({name:nt, y:ep.y});
    }
  }
  rows.sort(function(a,b){return a.y-b.y;});
  var ur=[]; for(var r=0;r<rows.length;r++){ if(!ur.length||Math.abs(rows[r].y-ur[ur.length-1].y)>6) ur.push(rows[r]); }
  rows=ur;
  if(!rows.length) return JSON.stringify({error:'no-rows', columns:scoreCols.length});

  var gap=18;
  if(rows.length>1){ var gs=[]; for(var g=1;g<rows.length;g++) gs.push(rows[g].y-rows[g-1].y); gs.sort(function(a,b){return a-b;}); gap=gs[Math.floor(gs.length/2)]||18; }
  var tol=Math.max(8, gap*0.6);
  function rowFor(y){ var bi=-1,bd=1e9; for(var i=0;i<rows.length;i++){var dd=Math.abs(rows[i].y-y); if(dd<bd){bd=dd;bi=i;}} return bd<=tol?bi:-1; }
  function colFor(x){ var bi=-1,bd=1e9; for(var i=0;i<scoreCols.length;i++){var dd=Math.abs(scoreCols[i]-x); if(dd<bd){bd=dd;bi=i;}} return bd<=28?bi:-1; }

  // 5) 점수 input 읽기 → 격자 배치
  var grid={};
  for (var d5=0; d5<docs.length; d5++){
    var ins=docs[d5].querySelectorAll('input');
    for (var ii=0;ii<ins.length;ii++){
      var el2=ins[ii]; var ty=(el2.type||'text').toLowerCase();
      if (ty==='checkbox'||ty==='radio'||ty==='button'||ty==='hidden'||ty==='submit'||ty==='image') continue;
      if (!vis(el2)) continue;
      var ip=rc(el2);
      var ci=colFor(ip.x); if(ci<0) continue;
      var ri=rowFor(ip.y); if(ri<0) continue;
      grid[ci]=grid[ci]||{};
      grid[ci][ri]=(el2.value||'').trim();
    }
  }

  // 6) 수행평가별 누락 집계
  var items=[];
  for (var ci2=0; ci2<scoreCols.length; ci2++){
    var nm=titleFor(scoreCols[ci2]) || ('수행평가'+(ci2+1));
    var colmap=grid[ci2]||{}; var miss=[], filled=0, read=0;
    for (var ri2=0; ri2<rows.length; ri2++){
      if(!(ri2 in colmap)) continue;
      read++;
      if(colmap[ri2]==='') miss.push(rows[ri2].name); else filled++;
    }
    items.push({assessment:nm, total:rows.length, read:read, filled:filled, missing:miss});
  }
  return JSON.stringify({students:rows.length, columns:scoreCols.length, items:items});
})();
"""


# ── 수행평가 누락 확인 (확정판): 화면 하단 '합계 행'을 읽어 수행평가별 누락 인원 산출 ──
#  나이스 그리드는 가상스크롤(보이는 행만 DOM) + cl-grid 프레임워크라 셀 직접 읽기는 불완전.
#  대신 '수강생/응시생/결시생/학적변동수' 합계(예: 22/21/0/1)를 읽어 22명 전체 기준으로 계산.
#  누락 = 수강 - 응시(입력) - 결시 - 학적변동.  (라이브 검증: 목제품 22/0/0/0 → 누락 22 ✓)
PERF_SUMMARY_JS = r"""
(function(){
  function vis(el){try{var r=el.getBoundingClientRect();return r.width>2&&r.height>2;}catch(e){return false;}}
  function leaf(el){return !(el.children&&el.children.length>0);}
  function cx(el){var r=el.getBoundingClientRect();return Math.round(r.left+r.width/2);}
  function cy(el){var r=el.getBoundingClientRect();return Math.round(r.top+r.height/2);}
  var all=document.querySelectorAll("div,span,td,th");
  // '점수' 헤더 y로 수행평가 영역 존재 확인 + 이름 행 위치
  var scoreY=null;
  for(var j=0;j<all.length;j++){var e2=all[j];if(!leaf(e2))continue;var t2=(e2.innerText||e2.textContent||"").replace(/\s+/g," ").trim();if(t2==="점수"&&vis(e2)){if(scoreY===null||cy(e2)<scoreY)scoreY=cy(e2);}}
  if(scoreY===null) return JSON.stringify({error:"no-score-header"});
  // 수행평가 이름(점수 헤더 바로 위 띠)
  var EXCL=/^(점수|결시명칭|학적변동|만점|합계|평균|성명|반|번호|학년도|학기|학년|과목|영역|강의실|순위|수강생|응시생|결시생)/;
  var names=[];
  for(var k=0;k<all.length;k++){var e3=all[k];if(!leaf(e3))continue;var t3=(e3.innerText||e3.textContent||"").replace(/\s+/g," ").trim();if(t3.length<3||t3.length>45)continue;if(EXCL.test(t3)||/^만점\(\d+\)$/.test(t3))continue;if(!vis(e3))continue;var y3=cy(e3);if(y3<scoreY-20&&y3>scoreY-90){names.push({name:t3,x:cx(e3)});}}
  // 합계 행 셀: 수강/응시/결시/학적변동 (예: 22/21/0/1)
  var SUM=/^(\d+)\s*\/\s*(\d+)\s*\/\s*(\d+)\s*\/\s*(\d+)$/;
  var sums=[];
  for(var i=0;i<all.length;i++){var e=all[i];if(!leaf(e))continue;var t=(e.innerText||e.textContent||"").replace(/\s+/g,"").trim();var m=t.match(SUM);if(m&&vis(e)){sums.push({x:cx(e), su:parseInt(m[1],10), eu:parseInt(m[2],10), gy:parseInt(m[3],10), ha:parseInt(m[4],10)});}}
  if(!names.length||!sums.length) return JSON.stringify({error:"no-summary", names:names.length, sums:sums.length});
  function nearest(x,arr){var bi=-1,bd=1e9;for(var z=0;z<arr.length;z++){var dd=Math.abs(arr[z].x-x);if(dd<bd){bd=dd;bi=z;}}return bd<60?bi:-1;}
  var items=[];
  for(var n=0;n<names.length;n++){
    var si=nearest(names[n].x, sums); if(si<0) continue;
    var s=sums[si]; var miss=s.su-s.eu-s.gy-s.ha; if(miss<0)miss=0;
    items.push({assessment:names[n].name, total:s.su, filled:s.eu, gyeolsi:s.gy, hakjeok:s.ha, missing:miss});
  }
  // 현재 '반' 추정: '반' 헤더 컬럼 바로 아래 첫 숫자
  var banX=null, banY=null;
  for(var p=0;p<all.length;p++){var e5=all[p];if(!leaf(e5))continue;var t5=(e5.innerText||e5.textContent||"").replace(/\s+/g,"").trim();if(t5==="반"&&vis(e5)){banX=cx(e5);banY=cy(e5);break;}}
  var ban="";
  if(banX!==null){var bd=1e9;for(var q=0;q<all.length;q++){var e6=all[q];if(!leaf(e6))continue;var t6=(e6.innerText||e6.textContent||"").replace(/\s+/g,"").trim();if(/^[1-9]$/.test(t6)&&vis(e6)){var yy=cy(e6);var dd=Math.abs(cx(e6)-banX);if(yy>banY+5&&dd<45&&dd<bd){bd=dd;ban=t6;}}}}
  return JSON.stringify({items:items, ban:ban});
})();
"""


def list_pages(port):
    url = "http://127.0.0.1:{}/json/list".format(port)
    with urllib.request.urlopen(url, timeout=4) as r:
        return json.loads(r.read().decode("utf-8"))


def toast(title, message):
    """데스크톱 알림 (plyer 없으면 조용히 무시)."""
    try:
        from plyer import notification
        notification.notify(title=title, message=message, timeout=6)
    except Exception:
        pass


TTS_ENABLED = False  # 앱이 설정값으로 갱신


def speak(text):
    """윈도우 SAPI 한국어 음성 알림 (TTS_ENABLED일 때만, 비차단)."""
    if not TTS_ENABLED or not text:
        return

    def _run():
        path = None
        try:
            import tempfile
            fd, path = tempfile.mkstemp(suffix=".txt")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            ps = ("Add-Type -AssemblyName System.Speech; "
                  "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                  "$s.Speak([IO.File]::ReadAllText('{}',[Text.Encoding]::UTF8))").format(path)
            subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           creationflags=(0x08000000 if os.name == "nt" else 0), timeout=25)
        except Exception:
            pass
        finally:
            if path:
                try:
                    os.remove(path)
                except Exception:
                    pass
    threading.Thread(target=_run, daemon=True).start()


def notify(title, message, say=None):
    """데스크톱 알림 + (켜져 있으면) 음성."""
    toast(title, message)
    speak(say if say is not None else "{} {}".format(title, message))


# ── 결재 진행 추적: 열려 있는 에듀파인 '문서카드' 화면의 결재경로 표를 읽음 ──
APPROVAL_PATH_JS = r"""
(function(){
  function vis(el){try{var r=el.getBoundingClientRect();return r.width>2&&r.height>2;}catch(e){return false;}}
  function leaf(el){return !(el.children&&el.children.length>0);}
  var docs=[document];
  try{var ifr=document.querySelectorAll('iframe,frame');for(var i=0;i<ifr.length;i++){try{var d=ifr[i].contentDocument;if(d)docs.push(d);}catch(e){}}}catch(e){}
  var rows=[];
  var STATE=/(완료|대기|진행|반려|승인|검토중|미결)/;
  var METHOD=/(기안|검토|결재|합의|협조|전결|대결|공람)/;
  for(var di=0; di<docs.length; di++){
    var trs=docs[di].querySelectorAll('tr,[class*="row"],[role="row"]');
    for(var r=0;r<trs.length;r++){
      var cells=Array.prototype.map.call(trs[r].querySelectorAll('td,th,[class*="cell"],[role="gridcell"]'),function(c){return (c.innerText||c.textContent||'').replace(/\s+/g,' ').trim();}).filter(function(x){return x;});
      if(cells.length<3||cells.length>10) continue;
      var joined=cells.join(' ');
      if(!METHOD.test(joined)) continue;
      if(!/^\d{1,2}$/.test(cells[0]) && !METHOD.test(cells[0]) && !METHOD.test(cells[1]||'')) continue;
      var method=''; for(var m=0;m<cells.length;m++){ if(METHOD.test(cells[m])&&cells[m].length<=6){method=cells[m];break;} }
      var state=''; for(var s=0;s<cells.length;s++){ if(STATE.test(cells[s])&&cells[s].length<=6){state=cells[s];break;} }
      if(!method) continue;
      // 처리자/직위: 이름 같은 셀(한글, 날짜/숫자 아님)
      var who=''; for(var w=0;w<cells.length;w++){ var cc=cells[w]; if(cc!==method&&cc!==state&&/[가-힣]/.test(cc)&&!/\d{4}-\d{2}/.test(cc)&&cc.length<=14){ who=cc; } }
      rows.push({no:(/^\d{1,2}$/.test(cells[0])?cells[0]:''), method:method, who:who, state:state, raw:joined.slice(0,80)});
      if(rows.length>=20) break;
    }
    if(rows.length) break;
  }
  return JSON.stringify(rows);
})();
"""


def do_read_approval_path(port, match):
    page = find_target_page(port, match)
    if not page:
        return None, "탭없음"
    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        return None, "no ws"
    cdp = CDP(ws_url)
    try:
        cdp.send("Page.enable")
        cdp.send("Page.setWebLifecycleState", {"state": "active"})
        return json.loads(cdp.eval_value(APPROVAL_PATH_JS) or "[]"), "ok"
    except Exception as e:
        return None, str(e)
    finally:
        cdp.close()


class CDP:
    """탭 1개의 DevTools 웹소켓과 통신하는 최소 클라이언트."""

    def __init__(self, ws_url, auto_accept_dialogs=True):
        self.ws = create_connection(ws_url, timeout=8)
        self._id = 0
        # 알림창(alert/confirm)이 뜨면 — True : 바로 [확인] (잠깐 붙었다 떼는 작업용, 예전 동작)
        #                              False: 건드리지 않고 문구만 기억 (탭 지킴이용)
        self.auto_accept_dialogs = auto_accept_dialogs
        self.dialog = None        # 지금 떠 있는 알림창 문구 (auto_accept_dialogs=False 일 때만 추적)
        self.crashed = False

    def _note(self, data):
        """응답이 아닌 '이벤트'를 처리한다. 이벤트였으면 True."""
        m = data.get("method")
        if m == "Page.javascriptDialogOpening":
            if self.auto_accept_dialogs:
                self.ws.send(json.dumps({"id": 999990, "method": "Page.handleJavaScriptDialog",
                                         "params": {"accept": True}}))
            else:
                self.dialog = (((data.get("params") or {}).get("message")) or "").strip() or "(내용 없음)"
            return True
        if m == "Page.javascriptDialogClosed":
            self.dialog = None
            return True
        if m == "Inspector.targetCrashed":
            self.crashed = True
            return True
        return False

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
            if self._note(data):
                continue
            if data.get("id") == mid:
                return data.get("result", {})
        return {}

    def alive(self, timeout=4):
        """탭 안의 스크립트가 실제로 대답하는가? (크롬이 탭을 버렸거나 얼렸으면 False)
           ※ 버려진(discard) 탭은 목록에도 있고 연결도 되지만, 스크립트만 영영 대답이 없다."""
        try:
            self.ws.settimeout(timeout)
            res = self.send("Runtime.evaluate", {"expression": "1+1"}, timeout=timeout)
            return (res.get("result") or {}).get("value") == 2
        except Exception:
            return False
        finally:
            try:
                self.ws.settimeout(8)
            except Exception:
                pass

    def pump(self, seconds):
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

    def evaluate(self, expr):
        return self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True})

    def eval_value(self, expr, await_promise=False):
        res = self.send("Runtime.evaluate", {"expression": expr}, await_promise=await_promise)
        try:
            return res.get("result", {}).get("value")
        except Exception:
            return None

    def trusted_click(self, x, y):
        """실제 커서는 안 움직이고, 그 좌표에 신뢰된 클릭을 보냄(Nexacro/연장창용)."""
        self.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
        self.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y,
                                               "button": "left", "clickCount": 1})
        self.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y,
                                               "button": "left", "clickCount": 1})

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def find_target_page(port, match):
    for p in list_pages(port):
        if p.get("type") == "page" and match in (p.get("url") or ""):
            return p
    return None


# 나이스가 "이 접속은 무효"라고 답했을 때 붙이는 표식 (세션 유지 워커가 보고 자동 재접속)
NEIS_ZOMBIE_TAG = "나이스가 이 접속을 무효 처리"


def _keep_neis(cdp):
    """나이스: /sessionExtension.do 직접 호출 + 연장창 자동확인. (state, detail).

    ★ 응답의 뜻 (나이스 화면 소스 Index.clx.js 의 session_extention() 에서 확인, 2026-11-13)
       "Y"   = 서버에 내 '세션 등록 정보'가 있음(정상)
       그 외 = 등록 정보가 없음 → 나이스 스스로 "세션 DB정보가 없어 시스템을 종료합니다"를 띄운다.
               시간 초과가 아니라 '같은 사용자가 다른 세션으로 로그인'했을 때 생긴다.
               대표적인 경우: 업무포털을 로그아웃·재로그인한 뒤, 예전 나이스 쿠키가 남은 채
               나이스를 다시 열면 화면은 멀쩡한데 등록 정보만 지워진 '빈 껍데기 접속'이 된다.
               → 새로고침으로는 못 고친다. 옛 쿠키를 지우고 SSO로 다시 들어가야 한다(neis_reenter)."""
    raw = None
    info = {}
    try:
        raw = cdp.eval_value(NEIS_KEEPALIVE_JS, await_promise=True)
        info = json.loads(raw or "{}")
    except Exception:
        info = {}
    responsive = isinstance(info, dict) and ("ok" in info)   # JS가 실제로 값을 돌려줬는가

    extra = ""
    if info.get("dialog") and info.get("btnX"):
        cdp.trusted_click(info["btnX"], info["btnY"])
        cdp.eval_value("fetch('/sessionExtension.do',{method:'POST',credentials:'include',cache:'no-store'})")
        extra = " · 연장창 자동확인"

    if info.get("ok"):
        # 주의: 이건 '서버 세션' 유지 성공일 뿐. 화면의 60분 카운트다운은 감시자(NeisGuardWorker)가 따로 챙긴다.
        return ("정상", "서버세션 유지 OK (sessionExtension.do=Y)" + extra)

    if not responsive:
        # 탭은 살아 있는데(do_keepalive가 먼저 확인) 이번 요청만 답을 못 받음 → 다음 주기에 다시
        return ("경고", "나이스 응답을 받지 못함(일시적일 수 있음) — 다음 주기에 다시 확인")

    body = (info.get("body") or "").strip().strip('"')
    if body.startswith("ERR"):
        return ("경고", "나이스 서버에 닿지 못함({}) — 네트워크 확인".format(body[:40]))
    if len(body) > 8:      # 'N' 이 아니라 로그인 화면(HTML)이 돌아온 경우 = 서버 세션 자체가 끝남
        return ("경고", NEIS_ZOMBIE_TAG + "함(로그인 화면 응답) — 서버 세션이 끝남")
    return ("경고", NEIS_ZOMBIE_TAG + "함(응답:{}) — 포털 재로그인·다른 곳 로그인으로 세션 등록이 지워짐".format(body or "없음"))


def _keep_edupine(cdp):
    """에듀파인(Nexacro): '연장' 버튼 좌표에 신뢰클릭 + 타이머 검증. (state, detail)."""
    info = {}
    try:
        info = json.loads(cdp.eval_value(EDU_FIND_JS) or "{}")
    except Exception:
        pass
    before = info.get("timer", "")
    x, y = info.get("x"), info.get("y")
    if not x:
        # 탭은 대답하는데(do_keepalive가 먼저 확인) 버튼이 없다 = 메인 화면이 아니거나 로그아웃된 것
        where = ""
        try:
            where = (cdp.eval_value("location.pathname") or "")[:40]
        except Exception:
            pass
        return ("실패", "'연장' 버튼이 화면에 없음 — 에듀파인 메인이 아니거나 로그아웃됨 ({})".format(where or "?"))
    cdp.trusted_click(x, y)
    time.sleep(1.0)
    after = ""
    try:
        after = json.loads(cdp.eval_value(EDU_FIND_JS) or "{}").get("timer", "")
    except Exception:
        pass
    b_sec, a_sec = _mmss_to_sec(before), _mmss_to_sec(after)
    if (a_sec > b_sec) or (b_sec >= 30 * 60):
        return ("정상", "세션연장 OK ({} → {})".format(before, after))
    return ("경고", "연장 안 먹힘 — 타이머 감소 ({} → {})".format(before, after))


def _keep_button(cdp):
    """기타: 화면의 '연장' 버튼 글자 클릭 + 서버 GET."""
    info = {}
    try:
        info = json.loads(cdp.eval_value(BUTTON_KEEPALIVE_JS) or "{}")
    except Exception:
        pass
    cdp.pump(1.0)
    detail = []
    if info.get("clicked"):
        detail.append("연장버튼({})".format(",".join(info.get("found", []))))
    if info.get("fetched"):
        detail.append("서버요청")
    return ("정상", ", ".join(detail) if detail else "연장버튼 없음")


def do_keepalive(port, target):
    """대상 탭 1개 세션 갱신. target은 {name,match,mode?}. (state, detail) 반환."""
    match = target["match"] if isinstance(target, dict) else target
    page = find_target_page(port, match)
    if not page:
        return ("탭없음", "사이트 탭을 열고 로그인했는지 확인")
    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        return ("오류", "webSocket 주소 없음")
    cdp = CDP(ws_url)
    try:
        # ★ 핵심: 백그라운드로 얼어붙은 탭을 깨운다 (없으면 타이머/연장이 멈춰 있음)
        cdp.send("Page.setWebLifecycleState", {"state": "active"}, timeout=5)
        # ★ 그래도 대답이 없으면 크롬이 탭을 '버린(discard)' 것 (메모리 부족 때 흔함, 2026-11-13 실측).
        #    깨우기로는 안 돌아오고 새로고침해야 한다 → 탭 지킴이(TabAnchorWorker)에게 맡긴다.
        if not cdp.alive():
            _poke_anchor()
            nm = target.get("name", match) if isinstance(target, dict) else match
            return ("경고", "{} 탭이 멈춰 있음(크롬이 탭을 재웠거나 버림) → 되살리는 중".format(nm))
        cdp.send("Page.enable")
        # ★ 실측(2026-07-07): setWebLifecycleState만으론 배경탭 타이머가 분당 1회로 throttle됨.
        #   포커스 에뮬레이션을 켜야 나이스 카운트다운/연장팝업 타이머가 정상속도로 돈다.
        try:
            cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        except Exception:
            pass
        cdp.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 7, "y": 7})
        mode = _infer_mode(target) if isinstance(target, dict) else "button"
        if mode == "neis":
            return _keep_neis(cdp)
        elif mode == "edupine":
            return _keep_edupine(cdp)
        return _keep_button(cdp)
    except Exception as e:
        return ("실패", str(e))
    finally:
        cdp.close()


# ── 나이스 '연장하시겠습니까?' 팝업 감지 + 확인버튼 좌표 ──
#  나이스 60분 절대 카운트다운은 이 팝업 클릭으로만 연장됨(서버 sessionExtension.do로는 리셋 안 됨).
#  엄격 화이트리스트로 확인/연장 버튼만 클릭(아니오/취소/로그아웃은 절대 안 누름).
#  ※ 나이스 4세대는 iframe 없음(라이브 실측 확인, frames=0) → 최상위 document만 본다.
NEIS_POPUP_JS = r"""
(function(){
  function vis(el){try{var r=el.getBoundingClientRect();return r.width>2&&r.height>2;}catch(e){return false;}}
  function leaf(el){return !(el.children&&el.children.length>0);}
  function ctr(el){var r=el.getBoundingClientRect();return [Math.round(r.left+r.width/2),Math.round(r.top+r.height/2)];}
  var KW=/(연장하시겠|접속유지|시간연장|세션\s*연장|자동\s*로그아웃|로그아웃\s*됩)/;
  var all=document.querySelectorAll(".cl-text,div,span,td,p,label");
  var found=false, dtext="", dialogEl=null;
  for(var i=0;i<all.length;i++){var e=all[i];if(!leaf(e))continue;var t=(e.innerText||e.textContent||"").replace(/\s+/g," ").trim();
    if(t && t.length<70 && KW.test(t) && vis(e)){ found=true; dtext=t; dialogEl=e; break; }}
  if(!found) return JSON.stringify({found:false});
  var OK=/^(확인|예|연장|연장하기|시간연장|연장확인|유지|계속)$/;
  var NO=/(아니오|아니요|취소|닫기|로그아웃|나가기)/;
  var btns=document.querySelectorAll(".cl-button,button,a,input[type=button],input[type=submit],.cl-text,div,span");
  var bx=0,by=0,btext="";
  for(var j=0;j<btns.length;j++){var b=btns[j];if(!leaf(b))continue;var bt=(b.innerText||b.value||"").replace(/\s+/g," ").trim();
    if(OK.test(bt) && !NO.test(bt) && vis(b)){ var c=ctr(b); bx=c[0]; by=c[1]; btext=bt; break; }}
  var box=dialogEl; for(var k=0;k<6&&box&&box.parentElement;k++){ box=box.parentElement; }
  var html=""; try{ html=(box&&box.outerHTML||"").slice(0,3500); }catch(e){}
  return JSON.stringify({found:true, dtext:dtext, btnX:bx, btnY:by, btnText:btext, html:html});
})();
"""


# 나이스 세션 잔여시간("N분 M초") 읽기 — 진단/로그용
NEIS_REMAIN_JS = r"""
(function(){var t=document.querySelectorAll('.cl-text');
 for(var i=0;i<t.length;i++){var s=(t[i].innerText||'').replace(/\s+/g,'').trim();
   if(/^\d{1,2}분\d{1,2}초$/.test(s)) return s;} return '';})()
"""


# 나이스 접속시간(60분 카운트다운)을 '화면 새로고침 없이' 처음으로 되돌린다.
#  · 나이스 메인 앱(app/com/main/Index)이 내보내는 setSessionTimerInit() 을 부른다.
#    = 10분 남았을 때 뜨는 "연장하시겠습니까?"에서 [연장]을 누른 것과 똑같은 동작(setSessionTimer 재시작).
#  · 새로고침과 달리 작성 중이던 화면이 날아가지 않는다. (2026-11-13 실측: 47분 → 60분)
NEIS_EXTEND_JS = r"""
(function(){
  try {
    var apps = cpr.core.Platform.INSTANCE.getAllRunningAppInstances();
    for (var i = 0; i < apps.length; i++) {
      var a = apps[i];
      if (a && a.app && /app\/com\/main\/Index$/.test(a.app.id) && a.hasAppMethod('setSessionTimerInit')) {
        a.callAppMethod('setSessionTimerInit');
        return 'ok';
      }
    }
    return 'nomethod';
  } catch (e) { return 'err:' + e; }
})()
"""


def _neis_remain_min(remain):
    """'N분M초' → 남은 분(float). 파싱 실패 시 None."""
    m = re.match(r"(\d+)분(\d+)초", (remain or "").replace(" ", ""))
    if not m:
        return None
    return int(m.group(1)) + int(m.group(2)) / 60.0


def do_neis_guard(port, match, capture_path=None, reload_below_min=0, critical_min=0, extend_below_min=0):
    """나이스 탭 유지 + 접속시간 연장 + 연장 팝업 자동확인(부차). dict 반환.
       extend_below_min : 잔여 이 분 이하면 '새로고침 없이' 접속시간을 처음으로 되돌림(1순위, 작업 안 날아감).
       reload_below_min : (연장이 안 될 때만) 백그라운드 탭에서 잔여 이 분 이하면 Page.reload로 리셋.
       critical_min     : (연장이 안 될 때만) 전면 탭이어도 이 분 이하면 리로드."""
    page = find_target_page(port, match)
    if not page:
        return {"ok": False, "reason": "탭없음"}
    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        return {"ok": False, "reason": "no ws"}
    cdp = CDP(ws_url)
    try:
        cdp.send("Page.setWebLifecycleState", {"state": "active"}, timeout=5)  # freeze 방지
        if not cdp.alive():
            # 크롬이 탭을 버렸거나 얼림 → 잔여시간을 읽을 수 없다. 탭 지킴이가 새로고침으로 되살린다.
            return {"ok": False, "reason": "무응답"}
        cdp.send("Page.enable")
        # ★ 백그라운드 여부는 포커스 에뮬레이션 켜기 '전에' 읽어야 함(에뮬 켜면 visible로 뒤바뀜)
        hidden = False
        try:
            hidden = bool(cdp.eval_value("document.hidden"))
        except Exception:
            hidden = False
        # ★ 배경탭에서는 화면 글자가 다시 그려지지 않아 잔여시간이 '옛 값'으로 읽힌다 → 포커스 에뮬로 갱신시킴
        try:
            cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        except Exception:
            pass
        remain = ""
        try:
            remain = cdp.eval_value(NEIS_REMAIN_JS) or ""
        except Exception:
            remain = ""
        rmin = _neis_remain_min(remain)
        # ★★ 1순위: 새로고침 없이 접속시간 연장 (나이스의 [연장] 버튼과 같은 동작)
        if rmin is not None and extend_below_min and rmin <= extend_below_min:
            try:
                if cdp.eval_value(NEIS_EXTEND_JS) == "ok":
                    after, amin = "", None
                    for _ in range(4):               # 화면 글자가 바뀔 때까지 잠깐 기다림
                        time.sleep(0.7)
                        after = cdp.eval_value(NEIS_REMAIN_JS) or ""
                        amin = _neis_remain_min(after)
                        if amin is not None and amin > rmin + 1:
                            return {"ok": True, "popup": False, "extended": True,
                                    "remain": remain, "after": after, "hidden": hidden}
            except Exception:
                pass
            # 연장이 확인되지 않으면 아래 '새로고침' 방식으로 넘어간다
        # ★★ 2순위(예비): 잔여가 임박하면 페이지 리로드 → 카운트다운 60분 복구(로그인 유지, 실측 검증).
        #    작업 방해 방지를 위해 백그라운드일 때만(또는 아주 임박 critical이면 전면이어도) 리로드.
        if rmin is not None and (
                (reload_below_min and rmin <= reload_below_min and hidden)
                or (critical_min and rmin <= critical_min)):
            try:
                cdp.send("Page.reload", {"ignoreCache": False})
            except Exception as e:
                return {"ok": False, "reason": "reload실패:" + str(e), "remain": remain}
            return {"ok": True, "popup": False, "reloaded": True, "remain": remain, "hidden": hidden}
        info = {}
        try:
            info = json.loads(cdp.eval_value(NEIS_POPUP_JS) or "{}")
        except Exception:
            info = {}
        if not info.get("found"):
            return {"ok": True, "popup": False, "remain": remain, "hidden": hidden}
        # 진단용: 팝업 HTML 최초 1회 저장
        if capture_path and info.get("html"):
            try:
                if not Path(capture_path).exists():
                    Path(capture_path).write_text(info["html"], encoding="utf-8")
            except Exception:
                pass
        if info.get("btnX"):
            cdp.trusted_click(info["btnX"], info["btnY"])
            return {"ok": True, "popup": True, "clicked": True, "btn": info.get("btnText"),
                    "dtext": info.get("dtext"), "remain": remain}
        return {"ok": True, "popup": True, "clicked": False, "dtext": info.get("dtext"), "remain": remain}
    except Exception as e:
        return {"ok": False, "reason": str(e)}
    finally:
        cdp.close()


# 업무포털 상단 [나이스] 메뉴의 id 에 들어 있는 SSO 주소(…/cmc_fcm_lg01_000.do?data=토큰)
NEIS_SSO_URL_JS = r"""
(function(){
  function strip(s){ return (s || '').replace(/[\[\]\s\-]/g, ''); }
  var c = document.querySelectorAll('a.menuBtn');
  for (var i = 0; i < c.length; i++) {
    if (strip(c[i].innerText) === '나이스' && (c[i].id || '').indexOf('http') === 0) return c[i].id;
  }
  return '';
})()
"""


def neis_session_ok(port, match="neis.go.kr"):
    """열려 있는 나이스 탭의 접속이 유효한가(sessionExtension.do=Y). 탭이 없거나 멈췄으면 False."""
    page = find_target_page(port, match)
    if not page or not page.get("webSocketDebuggerUrl"):
        return False
    cdp = CDP(page["webSocketDebuggerUrl"])
    try:
        if not cdp.alive():
            return False
        info = json.loads(cdp.eval_value(NEIS_KEEPALIVE_JS, await_promise=True) or "{}")
        return bool(info.get("ok"))
    except Exception:
        return False
    finally:
        cdp.close()


def neis_reenter(port, chrome=None, portal_match="eduptl.kr"):
    """나이스 '깨끗한 재접속'. (ok, detail)
       ① 업무포털을 새로 읽어 최신 SSO 주소를 얻고  ② 예전 나이스 접속 쿠키를 지운 뒤
       ③ 나이스 탭(없으면 새 탭)을 그 SSO 주소로 보낸다.
       ②가 핵심: 쿠키가 남아 있으면 나이스가 정식 로그인 절차를 건너뛰고 '등록 정보 없는 옛 접속'을
       그대로 써서, 화면은 떠도 곧 "세션 DB정보가 없어 시스템을 종료합니다"로 끝난다(2026-11-13 규명)."""
    portal = find_target_page(port, portal_match)
    if not portal or not portal.get("webSocketDebuggerUrl"):
        return (False, "업무포털 탭이 없음 — ① 로그인 자동화 실행이 필요합니다")
    url = ""
    cdp = CDP(portal["webSocketDebuggerUrl"])
    try:
        # 포털 화면이 오래됐으면 주소 속 토큰이 낡았을 수 있다 → 새로 읽는다(버려진 탭도 이때 되살아남)
        cdp.send("Page.reload", {"ignoreCache": False}, timeout=5)
        deadline = time.time() + 15
        while time.time() < deadline and not url.startswith("http"):
            time.sleep(1.2)
            try:
                url = cdp.eval_value(NEIS_SSO_URL_JS) or ""
            except Exception:
                url = ""
        if not url.startswith("http"):
            return (False, "업무포털 로그인이 풀려 있음 — ① 로그인 자동화 실행이 필요합니다")
        host = urllib.parse.urlparse(url).netloc.split(":")[0]
        # 예전 나이스 접속 쿠키 삭제 (WMONID 는 접속과 무관한 서버 구분용이라 둔다)
        try:
            cks = cdp.send("Network.getCookies", {"urls": ["https://" + host + "/"]}).get("cookies", [])
            for c in cks:
                if c.get("name") == "WMONID" or (c.get("domain") or "").lstrip(".") != host:
                    continue
                cdp.send("Network.deleteCookies", {"name": c["name"], "domain": c["domain"],
                                                   "path": c.get("path", "/")})
        except Exception:
            pass
    finally:
        cdp.close()
    neis = find_target_page(port, host)
    if neis and neis.get("webSocketDebuggerUrl"):
        c2 = CDP(neis["webSocketDebuggerUrl"])
        try:
            c2.send("Page.navigate", {"url": url}, timeout=5)   # 브라우저가 처리 → 탭이 멈춰 있어도 됨
        finally:
            c2.close()
        return (True, "나이스 탭을 SSO로 다시 접속시킴")
    if chrome and PROFILE_DIR:
        subprocess.Popen([chrome, "--user-data-dir=" + PROFILE_DIR, url])
        return (True, "나이스를 SSO로 새 탭에 열었음")
    return (False, "나이스 탭이 없고 크롬 경로도 못 찾음")


def do_read_monitor(port, mon):
    """관제 대상 1개에서 건수/목록을 읽는다. (kind, data, status) 반환.
       edufine → ('counts', {결재:n, 공람:n}, status)
       ealimi  → ('docs',   [{title,date}, ...],   status)"""
    page = find_target_page(port, mon["match"])
    if not page:
        return (mon["type"], None, "탭없음")
    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        return (mon["type"], None, "webSocket 주소 없음")
    cdp = CDP(ws_url)
    try:
        cdp.send("Page.enable")
        cdp.send("Page.setWebLifecycleState", {"state": "active"})  # 백그라운드 freeze 해제
        if mon["type"] == "edufine":
            data = json.loads(cdp.eval_value(EDUFINE_COUNT_JS) or "{}")
            return ("counts", data, "ok")
        elif mon["type"] == "ealimi":
            data = json.loads(cdp.eval_value(EALIMI_DOCS_JS) or "[]")
            return ("docs", data, "ok")
        elif mon["type"] == "neis":
            # 업무포털/나이스 대시보드 숫자는 '새로고침(↻)'해야 갱신됨
            #  → 탭을 reload 하고, 값이 나올 때까지(최대 12초) 폴링 후 읽는다.
            if mon.get("refresh", True):
                cdp.send("Page.reload", {"ignoreCache": False})
                data = {}
                deadline = time.time() + 12
                while time.time() < deadline:
                    time.sleep(1.3)
                    try:
                        data = json.loads(cdp.eval_value(NEIS_COUNT_JS) or "{}")
                    except Exception:
                        data = {}
                    if data:
                        break
            else:
                data = json.loads(cdp.eval_value(NEIS_COUNT_JS) or "{}")
            return ("counts", data, "ok")
        return (mon["type"], None, "알 수 없는 type")
    except Exception as e:
        return (mon["type"], None, str(e))
    finally:
        cdp.close()


def do_read_doclist(port, match):
    """열려 있는 목록 화면에서 문서 행을 읽는다. (rows, status). rows: [{title,sub,date,x,y}]."""
    page = find_target_page(port, match)
    if not page:
        return None, "탭없음"
    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        return None, "webSocket 주소 없음"
    cdp = CDP(ws_url)
    try:
        cdp.send("Page.enable")
        cdp.send("Page.setWebLifecycleState", {"state": "active"})
        return json.loads(cdp.eval_value(DOCLIST_JS) or "[]"), "ok"
    except Exception as e:
        return None, str(e)
    finally:
        cdp.close()


# ── 나이스 '일일출결관리(담임용)' 그리드 ────────────────────────────────
# 나이스 4세대는 Nexacro가 아니라 eXbuilder6(클립소프트)다 — 진짜 DOM이라
# 좌표를 추측하지 않고 `.cl-*` 선택자로 행·셀을 정확히 집을 수 있다.
#   행 .cl-grid-row / 셀 = 그 자식 div / 글자 .cl-text
#   달력 .cl-dateinput-button / 대화상자 .cl-dialog-wrapper
# (2026-09-21 sen.neis.go.kr 실측: window.cpr 존재, cl-* 5278개, nexacro 0개)
#
# 헬퍼를 탭에 한 번 심어두고(__sc), 파이썬은 '어디를 누를지' 좌표만 받아
# CDP 신뢰된 클릭으로 누른다. 클릭 뒤 화면이 다시 그려지므로 좌표는
# 매번 다시 물어본다 — 한꺼번에 받아두면 엉뚱한 곳을 누른다.
NEIS_HELPER_JS = r"""
(function(){
  function vis(el){
    if(!el) return false;
    var r = el.getBoundingClientRect();
    if(r.width < 2 || r.height < 2) return false;
    if(!el.getClientRects().length) return false;
    var s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  }
  function txt(el){
    if(!el) return '';
    var t = (el.innerText || el.textContent || '').trim();
    return t.split('\n')[0].trim();
  }
  function head(el){ return (txt(el).split(/\s+/)[0] || ''); }
  function xy(el){
    var r = el.getBoundingClientRect();
    return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
  }
  function rows(){
    return Array.prototype.slice.call(document.querySelectorAll('.cl-grid-row'));
  }

  // 헤더행 = 첫 칸이 '번호'이고 '성명'이 들어 있는 행
  function header(){
    var rs = rows();
    for(var i=0;i<rs.length;i++){
      var k = rs[i].children;
      if(!k.length || head(k[0]) !== '번호') continue;
      var cols = [];
      for(var j=0;j<k.length;j++) cols.push(head(k[j]));
      if(cols.indexOf('성명') !== -1) return {el: rs[i], cols: cols};
    }
    return null;
  }

  // 데이터행. 행마다 앞쪽에 '숨은 열'이 붙는 경우가 있어 보정값(off)을 행별로 구한다.
  // 고정값(+1)으로 박아두면 나이스가 열 구성을 바꾼 날 조용히 옆 칸을 누른다.
  function data(cols){
    var iNo = cols.indexOf('번호'), iNm = cols.indexOf('성명');
    var out = [], rs = rows();
    for(var i=0;i<rs.length;i++){
      var k = rs[i].children;
      if(k.length < cols.length) continue;              // 공지 그리드(3칸) 등은 제외
      var cand = [k.length - cols.length, 0, 1], picked = null;
      for(var c=0;c<cand.length;c++){
        var off = cand[c];
        if(off < 0 || off + cols.length > k.length) continue;
        var no = head(k[off + iNo]), nm = txt(k[off + iNm]);
        if(!/^\d{1,3}$/.test(no)) continue;             // 헤더행·합계행 걸러짐
        if(!nm || /^\d+$/.test(nm)) continue;
        picked = {el: rs[i], off: off, no: no, name: nm};
        break;
      }
      if(picked) out.push(picked);
    }
    return out;
  }

  function pick(cols, no, name){
    var d = data(cols);
    var hit = d.filter(function(r){ return r.no === String(no) && r.name === name; });
    if(!hit.length) hit = d.filter(function(r){ return r.name === name; });
    if(!hit.length) return {err: no + '번 ' + name + ' 행을 못 찾음'};
    if(hit.length > 1) return {err: name + ' 행이 ' + hit.length + '개입니다(동명이인) — 넣지 않았습니다'};
    return {row: hit[0]};
  }

  // 화면에 '보이는' 대화상자. 인증서용 [role=dialog]가 늘 숨은 채 붙어 있어
  // 보이는 것만 골라야 한다. 맨 마지막(제일 위)이 방금 뜬 창이다.
  function dialogs(){
    var els = document.querySelectorAll('.cl-dialog-wrapper, .cl-dialog, [role=dialog]');
    var out = [];
    for(var i=0;i<els.length;i++) if(vis(els[i])) out.push(els[i]);
    return out;
  }
  function topDialog(word){
    var d = dialogs();
    for(var i=d.length-1;i>=0;i--){
      if(!word || (d[i].innerText || '').indexOf(word) !== -1) return d[i];
    }
    return null;
  }

  // 글자가 정확히 label인 '제일 작은' 요소 — 바깥 상자 말고 진짜 버튼을 집는다
  function findXY(scope, label, sel){
    var els = scope.querySelectorAll(sel || 'a,button,[role=button],div,span,li,td,label');
    var best = null, area = 1e12;
    for(var i=0;i<els.length;i++){
      var e = els[i];
      if(((e.innerText || e.textContent || '').trim()) !== label) continue;
      if(!vis(e)) continue;
      var r = e.getBoundingClientRect(), a = r.width * r.height;
      if(a < area){ area = a; best = e; }
    }
    return best ? xy(best) : null;
  }

  function radioXY(scope, label){
    var els = scope.querySelectorAll('[role=radio], input[type=radio]');
    for(var i=0;i<els.length;i++){
      var e = els[i], nm = (e.getAttribute('aria-label') || '').trim();
      if(!nm && e.id){
        var lb = scope.querySelector('label[for="' + e.id + '"]');
        if(lb) nm = (lb.innerText || '').trim();
      }
      if(nm !== label) continue;
      var t = e;
      if(!vis(t) && e.parentElement) t = e.parentElement;   // 숨은 input이면 그려진 부모를 누른다
      if(!vis(t)) continue;
      return xy(t);
    }
    return null;
  }

  window.__sc = {
    // ── 읽기 ────────────────────────────────────────────────
    read: function(){
      var h = header();
      if(!h) return JSON.stringify({err: "그리드에서 '번호·성명' 헤더를 못 찾았습니다 — 일일출결관리(담임용) 화면인지 확인하세요"});
      var cols = h.cols, d = data(cols);
      var iM = cols.indexOf('마감'), iR = cols.indexOf('사유');
      var marky = [];
      for(var c=0;c<cols.length;c++){
        if(cols[c] === '조회' || cols[c] === '종례' || /교시$/.test(cols[c])) marky.push(c);
      }
      var out = [];
      for(var i=0;i<d.length;i++){
        var r = d[i], marks = [];
        for(var m=0;m<marky.length;m++){
          var t = txt(r.el.children[r.off + marky[m]]);
          if(t) marks.push(cols[marky[m]] + ':' + t);
        }
        out.push({no: r.no, name: r.name, off: r.off,
                  magam: iM >= 0 ? txt(r.el.children[r.off + iM]) : '',
                  reason: iR >= 0 ? txt(r.el.children[r.off + iR]) : '',
                  marks: marks.join(' ')});
      }
      return JSON.stringify({cols: cols, rows: out});
    },

    // ── 날짜칸 ──────────────────────────────────────────────
    dateXY: function(){
      var ins = document.querySelectorAll('input');
      for(var i=0;i<ins.length;i++){
        var v = (ins[i].value || '');
        if(/^\d{4}\.\d{2}\.\d{2}/.test(v) && vis(ins[i])) return JSON.stringify(xy(ins[i]));
      }
      return JSON.stringify({err: '날짜 입력칸을 못 찾았습니다'});
    },
    dates: function(){
      var out = [], ins = document.querySelectorAll('input');
      for(var i=0;i<ins.length;i++){
        var v = (ins[i].value || '').trim();
        if(/^\d{4}\.\d{2}\.\d{2}\.?$/.test(v) && vis(ins[i])) out.push(v.replace(/\.$/, ''));
      }
      return JSON.stringify(out);
    },

    // ── 셀·버튼 좌표 ────────────────────────────────────────
    cellXY: function(no, name, colName){
      var h = header();
      if(!h) return JSON.stringify({err: '그리드 헤더를 못 찾았습니다'});
      var ci = h.cols.indexOf(colName);
      if(ci < 0) return JSON.stringify({err: "이 날 화면에 '" + colName + "' 칸이 없습니다"});
      var p = pick(h.cols, no, name);
      if(p.err) return JSON.stringify({err: p.err});
      var cell = p.row.el.children[p.row.off + ci];
      if(!cell) return JSON.stringify({err: '셀 위치 계산 실패'});
      try { cell.scrollIntoView({block: 'center'}); } catch(e) {}
      var t = cell.querySelector('.cl-text') || cell;
      if(!vis(t)) return JSON.stringify({err: "'" + colName + "' 칸이 화면에 안 보입니다(가로 스크롤 확인)"});
      var c = xy(t); c.col = colName;
      return JSON.stringify(c);
    },
    // 버튼은 a/button 만 본다 — 그리드에 '조회'라는 '열 제목'이 있어서
    // div까지 훑으면 조회 버튼 대신 열 제목을 누른다
    btnXY: function(label){
      var c = findXY(document, label, 'a,button,[role=button]');
      return JSON.stringify(c || {err: "'" + label + "' 버튼을 못 찾았습니다"});
    },

    // ── 출결 구분 팝업 ──────────────────────────────────────
    popupXY: function(label, kind){
      var d = topDialog('출결마감구분') || topDialog('구분');
      if(!d) return JSON.stringify({err: '출결 구분 팝업이 열리지 않았습니다'});
      var c = (kind === 'radio') ? (radioXY(d, label) || findXY(d, label))
            : (kind === 'btn')   ? (findXY(d, label, 'a,button,[role=button]') || findXY(d, label))
            :                      findXY(d, label);
      return JSON.stringify(c || {err: "팝업에서 '" + label + "' 을(를) 못 찾았습니다"});
    },
    // 사유칸: 팝업 안에 '비어 있는 보이는 글자칸이 딱 하나'일 때만 알려준다.
    // 둘 이상이면 어느 칸인지 확신할 수 없으므로 건드리지 않는다.
    reasonXY: function(){
      var d = topDialog('출결마감구분') || topDialog('구분');
      if(!d) return JSON.stringify({err: '팝업 없음'});
      var ins = d.querySelectorAll('input[type=text], input:not([type]), textarea');
      var cand = [];
      for(var i=0;i<ins.length;i++){
        var e = ins[i];
        if(!vis(e) || e.readOnly || e.disabled) continue;
        if((e.value || '').trim()) continue;
        cand.push(e);
      }
      if(cand.length !== 1) return JSON.stringify({err: '사유칸을 특정할 수 없어 건너뜀(빈 칸 ' + cand.length + '개)'});
      return JSON.stringify(xy(cand[0]));
    },

    // ── 대화상자 ────────────────────────────────────────────
    dlg: function(){
      var d = topDialog(null);
      if(!d) return JSON.stringify({open: false});
      return JSON.stringify({open: true,
        text: (d.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 300)});
    },
    dlgBtnXY: function(label){
      var d = topDialog(null);
      if(!d) return JSON.stringify({err: '열린 대화상자가 없습니다'});
      var c = findXY(d, label, 'a,button,[role=button]') || findXY(d, label);
      return JSON.stringify(c || {err: "대화상자에서 '" + label + "' 을(를) 못 찾았습니다"});
    }
  };
})(); '__sc_v1'
"""


def _att_open(port, match):
    """나이스 탭에 붙고 헬퍼(__sc)를 심는다. (cdp, status)."""
    page = find_target_page(port, match)
    if not page:
        return None, "탭없음 — 나이스를 먼저 여세요"
    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        return None, "webSocket 주소 없음"
    cdp = CDP(ws_url)
    try:
        cdp.send("Page.enable")
        cdp.send("Page.setWebLifecycleState", {"state": "active"})
        if cdp.eval_value(NEIS_HELPER_JS) != "__sc_v1":
            cdp.close()
            return None, "화면 읽기 스크립트를 심지 못했습니다"
        return cdp, "ok"
    except Exception as e:
        try:
            cdp.close()
        except Exception:
            pass
        return None, str(e)


def _sc(cdp, call):
    """__sc.<call> 실행 → dict/list. 실패해도 예외 대신 {'err': ...}."""
    try:
        raw = cdp.eval_value("__sc." + call)
        if raw is None:
            return {"err": "화면이 응답하지 않습니다(탭이 얼었을 수 있음)"}
        return json.loads(raw)
    except Exception as e:
        return {"err": str(e)}


def _a(*vals):
    """값들을 JS 인자 문자열로."""
    return ", ".join(json.dumps(v, ensure_ascii=False) for v in vals)


def _click_at(cdp, pos, settle=0.45):
    cdp.trusted_click(pos["x"], pos["y"])
    time.sleep(settle)


def do_read_roster(port, match="sen.neis.go.kr"):
    """지금 떠 있는 출결 화면을 그대로 읽는다(변경 없음). (dict, status)."""
    cdp, st = _att_open(port, match)
    if not cdp:
        return None, st
    try:
        got = _sc(cdp, "read()")
        if got.get("err"):
            return None, got["err"]
        return got, "ok"
    finally:
        cdp.close()


def do_load_roster_by_date(port, date8, match="sen.neis.go.kr"):
    """날짜(YYYYMMDD)를 넣고 조회한 뒤 명단을 읽는다. 조회는 기록 변경이 아니다.
       (dict, status) — dict = {cols: [...], rows: [...]}"""
    cdp, st = _att_open(port, match)
    if not cdp:
        return None, st
    try:
        pos = _sc(cdp, "dateXY()")
        if pos.get("err"):
            return None, pos["err"] + " — '일일출결관리(담임용)' 화면인지 확인하세요"
        want = "{}.{}.{}".format(date8[:4], date8[4:6], date8[6:])
        cdp.trusted_click(pos["x"], pos["y"])
        time.sleep(0.3)
        cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "modifiers": 2, "key": "a",
                                            "code": "KeyA", "windowsVirtualKeyCode": 65})
        cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "modifiers": 2, "key": "a",
                                            "code": "KeyA", "windowsVirtualKeyCode": 65})
        cdp.send("Input.insertText", {"text": date8})
        for t in ("keyDown", "keyUp"):
            cdp.send("Input.dispatchKeyEvent", {"type": t, "key": "Tab", "code": "Tab",
                                                "windowsVirtualKeyCode": 9})
        time.sleep(0.4)

        # 화면의 날짜칸을 '읽어서' 그날이 맞는지 본다.
        # 엉뚱한 날짜 화면에 출결을 적는 것이 제일 나쁘다.
        seen = _sc(cdp, "dates()")
        if isinstance(seen, list) and seen and want not in seen:
            return None, "날짜가 안 바뀌었습니다 — 화면: {} / 넣으려던 날: {}".format(", ".join(seen), want)

        btn = _sc(cdp, "btnXY('조회')")
        if not btn.get("err"):
            _click_at(cdp, btn, 0.3)
        # 날짜를 바꾸면 알림창이 뜨는 경우가 있다 — 남아 있으면 조회·저장 클릭을 막는다
        dlg = _sc(cdp, "dlg()")
        if dlg.get("open") and "저장하시겠습니까" not in (dlg.get("text") or ""):
            ok = _sc(cdp, "dlgBtnXY('확인')")
            if not ok.get("err"):
                _click_at(cdp, ok, 0.4)
                btn = _sc(cdp, "btnXY('조회')")
                if not btn.get("err"):
                    _click_at(cdp, btn, 0.3)
        time.sleep(2.0)

        got = _sc(cdp, "read()")
        if got.get("err"):
            return None, got["err"]
        return got, "ok"
    except Exception as e:
        return None, str(e)
    finally:
        cdp.close()


# 저장 결과창 판정 — '저장'이라는 글자만 보면 「저장하지 못했습니다」도 성공이 된다.
# 아는 성공 문구일 때만 성공으로 본다.
SAVE_OK_WORDS = ("저장되었습니다", "저장하였습니다", "저장했습니다",
                 "처리되었습니다", "정상적으로 저장", "반영되었습니다")
SAVE_BAD_WORDS = ("실패", "오류", "에러", "못했습니다", "않았습니다",
                  "할 수 없", "불가", "권한")


def _judge_save(body):
    if not body:
        return False, "저장 결과창을 못 봤습니다 — 저장됐는지 확인할 수 없습니다. 나이스 화면을 직접 보세요"
    if "변경된 내용이 없습니다" in body:
        return False, "나이스: 「변경된 내용이 없습니다」 — 입력이 반영되지 않았을 수 있습니다"
    if any(w in body for w in SAVE_BAD_WORDS) or not any(w in body for w in SAVE_OK_WORDS):
        return False, "저장 결과창: 「{}」".format(body[:120])
    return True, body[:120]


def do_write_attendance(port, match, tasks, logf=None):
    """예외 학생들을 출결 화면에 '입력'한다. 저장은 하지 않는다.

    tasks: [{no, name, gubun, jongryu, gyosi(int|None), reason}]
    반환: ({"done": [...], "fail": [{name, why}]}, status)

    ⚠️ 교시 없는 조퇴·지각은 넣지 않는다. 나이스는 교시 없는 조퇴·지각이 한 줄이라도
       있으면 그날 저장을 통째로 거부해서, 같은 날 다른 학생까지 빠진다.
    """
    def say(s):
        if logf:
            logf(s)

    cdp, st = _att_open(port, match)
    if not cdp:
        return None, st
    done, fail = [], []
    try:
        head = _sc(cdp, "read()")
        if head.get("err"):
            return None, head["err"]
        cols = head.get("cols") or []

        for t in tasks:
            no, name = t["no"], t["name"]
            gubun, jong = t["gubun"], t["jongryu"]
            who = "{}번 {}".format(no, name)

            # 교시 확인은 '누르기 전에' 끝낸다
            col = None
            if jong in ("조퇴", "지각"):
                g = t.get("gyosi")
                if g is None:
                    fail.append({"name": who, "why": "{}인데 교시가 비어 있습니다 — 넣으면 그날 저장이 통째로 막힙니다".format(jong)})
                    say("  ❌ {} — 교시 없음, 건너뜀".format(who))
                    continue
                col = "조회" if g == 0 else "{}교시".format(g)
                if cols and col not in cols:
                    have = [c for c in cols if c.endswith("교시")]
                    fail.append({"name": who, "why": "이 날 화면에 '{}' 칸이 없습니다(있는 교시: {})".format(
                        col, ", ".join(have) or "없음")})
                    say("  ⛔ {} — 없는 교시({}), 건너뜀".format(who, col))
                    continue

            applied = False
            try:
                # 1) 마감 칸 클릭 → 구분/종류 팝업
                cell = _sc(cdp, "cellXY({})".format(_a(no, name, "마감")))
                if cell.get("err"):
                    raise RuntimeError(cell["err"])
                _click_at(cdp, cell, 0.6)

                p = _sc(cdp, "popupXY({})".format(_a(gubun, "text")))
                if p.get("err"):
                    raise RuntimeError(p["err"])
                _click_at(cdp, p, 0.35)

                p = _sc(cdp, "popupXY({})".format(_a(jong, "radio")))
                if p.get("err"):
                    raise RuntimeError(p["err"])
                _click_at(cdp, p, 0.35)

                reason = (t.get("reason") or "").strip()
                if reason:
                    rp = _sc(cdp, "reasonXY()")
                    if rp.get("err"):
                        say("  · {} 사유칸 건너뜀 ({})".format(who, rp["err"]))
                    else:
                        cdp.trusted_click(rp["x"], rp["y"])
                        time.sleep(0.2)
                        cdp.send("Input.insertText", {"text": reason})
                        time.sleep(0.2)

                p = _sc(cdp, "popupXY({})".format(_a("적용", "btn")))
                if p.get("err"):
                    raise RuntimeError(p["err"])
                _click_at(cdp, p, 0.6)
                applied = True

                # 2) 조퇴·지각이면 교시 칸까지
                if col:
                    cell = _sc(cdp, "cellXY({})".format(_a(no, name, col)))
                    if cell.get("err"):
                        raise RuntimeError(cell["err"])
                    _click_at(cdp, cell, 0.5)
                    # 교시 칸을 누르면 팝업이 다시 열린다. 이때 창 제목이 다를 수 있어
                    # '출결마감구분' 팝업으로 못 찾으면 '지금 떠 있는 창'에서 [적용]을 찾는다.
                    p = _sc(cdp, "popupXY({})".format(_a("적용", "btn")))
                    if p.get("err"):
                        p = _sc(cdp, "dlgBtnXY('적용')")
                    if not p.get("err"):
                        _click_at(cdp, p, 0.4)

                label = "{}{}{}".format(gubun, jong, " " + col if col else "")
                done.append({"name": who, "what": label})
                say("  ✅ {} : {}".format(who, label))
            except Exception as e:
                why = str(e)
                if applied:
                    why = "구분·종류는 넣었는데 그 뒤에서 막혔습니다 — {}".format(why)
                fail.append({"name": who, "why": why})
                say("  ❌ {} 실패 — {}".format(who, why))

        return {"done": done, "fail": fail}, "ok"
    except Exception as e:
        return {"done": done, "fail": fail}, str(e)
    finally:
        cdp.close()


def do_save_attendance(port, match, logf=None):
    """출결 화면의 [저장]을 누르고 결과창 '글자'로 성공 여부를 가린다.
       마감(확정)은 누르지 않는다. (ok, 메시지)."""
    def say(s):
        if logf:
            logf(s)

    cdp, st = _att_open(port, match)
    if not cdp:
        return False, st
    try:
        # 남은 알림창이 저장 클릭을 막는다 — 먼저 치운다
        dlg = _sc(cdp, "dlg()")
        if dlg.get("open"):
            ok = _sc(cdp, "dlgBtnXY('확인')")
            if not ok.get("err"):
                _click_at(cdp, ok, 0.4)

        btn = _sc(cdp, "btnXY('저장')")
        if btn.get("err"):
            return False, "[저장] 버튼을 못 찾았습니다 — " + btn["err"]
        _click_at(cdp, btn, 0.8)
        say("  · [저장] 클릭")

        # 1차: 「저장하시겠습니까?」 → 확인
        asked = ""
        for _ in range(10):
            d = _sc(cdp, "dlg()")
            if d.get("open") and "저장하시겠습니까" in (d.get("text") or ""):
                asked = d["text"]
                break
            time.sleep(0.4)
        if asked:
            ok = _sc(cdp, "dlgBtnXY('확인')")
            if ok.get("err"):
                return False, "「저장하시겠습니까?」 창의 [확인]을 못 찾았습니다"
            _click_at(cdp, ok, 0.8)
            say("  · 저장 확인")

        # 2차: 결과창. '떴다'가 아니라 '무슨 글자냐'로 가린다.
        body = ""
        for _ in range(20):
            d = _sc(cdp, "dlg()")
            txt = (d.get("text") or "") if d.get("open") else ""
            if txt and "저장하시겠습니까" not in txt and "처리 중" not in txt:
                body = txt
                break
            time.sleep(0.4)

        ok_btn = _sc(cdp, "dlgBtnXY('확인')")
        if not ok_btn.get("err"):
            _click_at(cdp, ok_btn, 0.4)

        good, msg = _judge_save(body)
        say(("  💾 " if good else "  ⚠️ ") + msg)
        return good, msg
    except Exception as e:
        return False, str(e)
    finally:
        cdp.close()


def do_click_xy(port, match, x, y):
    """대상 탭의 (x,y) 좌표에 신뢰된 클릭 → 해당 문서 열기."""
    page = find_target_page(port, match)
    if not page:
        return False, "탭없음"
    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        return False, "webSocket 주소 없음"
    cdp = CDP(ws_url)
    try:
        cdp.send("Page.enable")
        cdp.send("Page.setWebLifecycleState", {"state": "active"})
        cdp.trusted_click(x, y)
        return True, "ok"
    except Exception as e:
        return False, str(e)
    finally:
        cdp.close()


def do_attendance_check(port, att):
    """나이스 '출결 현황' 화면에서 학급별 마감/미마감 상태를 읽는다. (list, status)."""
    match = att.get("match", "neis.go.kr")
    page = find_target_page(port, match)
    if not page:
        return None, "탭없음"
    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        return None, "webSocket 주소 없음"
    js = (ATTENDANCE_JS_TMPL
          .replace("%CLASS_RE%", json.dumps(att.get("class_re", "(\\d학년\\s*\\d{1,2}반|\\d{1,2}\\s*-\\s*\\d{1,2})")))
          .replace("%UNCLOSED%", json.dumps(att.get("unclosed_words", ["미마감"]), ensure_ascii=False))
          .replace("%CLOSED%", json.dumps(att.get("closed_words", ["마감"]), ensure_ascii=False)))
    cdp = CDP(ws_url)
    try:
        cdp.send("Page.enable")
        cdp.send("Page.setWebLifecycleState", {"state": "active"})
        return json.loads(cdp.eval_value(js) or "[]"), "ok"
    except Exception as e:
        return None, str(e)
    finally:
        cdp.close()


def do_check_perf(port, match):
    """나이스 수행평가성적관리 표를 읽어 점수 빈 학생을 수행평가별로 집계. (data, status)."""
    page = find_target_page(port, match)
    if not page:
        return None, "탭없음"
    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        return None, "webSocket 주소 없음"
    cdp = CDP(ws_url)
    try:
        cdp.send("Page.enable")
        cdp.send("Page.setWebLifecycleState", {"state": "active"})
        return json.loads(cdp.eval_value(PERF_SUMMARY_JS) or "{}"), "ok"
    except Exception as e:
        return None, str(e)
    finally:
        cdp.close()


# 관제 상태 영속화 (재시작해도 '직전 건수/이미 본 서류' 유지 → 첫 확인 때 헛알림 방지)
STATE_PATH = APP_DIR / "school_console_state.json"


def load_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state):
    try:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ============================================================
#  세션 유지 백그라운드 워커
# ============================================================
# ============================================================
#  탭 지킴이 — 세션 유지 대상 탭이 '멈추지 않게' 지키고, 멈추면 되살린다
# ============================================================
#  왜 필요한가 (2026-11-13 규명):
#    PC 메모리가 모자라면 크롬은 안 보는 탭을 '버린다(discard)'. 버려진 탭은 목록에도 있고
#    연결도 되지만 스크립트가 영영 대답하지 않는다. 그 사이 콘솔은 연장 버튼도, 남은 시간도
#    읽지 못해서 세션이 그대로 끝났다. (사용자가 그 탭을 누르면 크롬이 다시 불러와 '갑자기 되는' 것)
#    크롬 실행 옵션(얼림 방지 4종)은 '얼림'만 막고 '버림'은 못 막는다.
#  하는 일:
#    ① 대상 탭마다 CDP 연결을 '계속' 붙여 둔다 → 디버거가 붙어 있는 탭은 크롬이 버리는 대상에서 빠진다.
#    ② 탭 안에 Web Lock 을 쥐여 둔다 → Web Lock 을 쥔 페이지는 크롬이 얼리지 않는다(크롬 공식 예외).
#    ③ 15초마다 대답하는지 확인 → 두 번 연속 대답이 없으면 새로고침으로 되살리고, 바로 세션 갱신을 다시 시킨다.
#       (알림창이 떠서 멈춘 것이면 건드리지 않고 알려만 준다. 방금까지 보고 있던 탭은 90초를 더 기다린다.)
ANCHOR_JS = r"""
(function(){
  try {
    if (!window.__scKeep && navigator.locks && navigator.locks.request) {
      window.__scKeep = 1;
      navigator.locks.request('school-console-keepalive', function(){ return new Promise(function(){}); });
    }
  } catch (e) {}
  return document.visibilityState;
})()
"""

TAB_ANCHOR = None      # 실행 중인 탭 지킴이(있으면). 갱신 쪽에서 '지금 봐 달라'고 깨울 때 쓴다.


def _poke_anchor():
    a = TAB_ANCHOR
    if a is not None:
        try:
            a.poke()
        except Exception:
            pass


class TabAnchorWorker(threading.Thread):
    PING_SEC = 15

    def __init__(self, port, targets, ui_post, on_revived=None):
        super().__init__(daemon=True)
        self.port = port
        self.targets = [t for t in targets if isinstance(t, dict) and t.get("match")]
        self.ui_post = ui_post
        self.on_revived = on_revived
        self._st = {}
        self._stop = threading.Event()
        self._wake = threading.Event()

    # ---- 바깥에서 부르는 것 ----
    def poke(self):
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()

    # ---- 내부 ----
    def _log(self, msg):
        self.ui_post("log", "[{}] {}".format(datetime.now().strftime("%H:%M:%S"), msg))

    def _drop(self, st):
        c = st.get("cdp")
        st["cdp"] = None
        st["id"] = None
        if c is not None:
            c.close()

    def _attach(self, st, page):
        cdp = CDP(page["webSocketDebuggerUrl"], auto_accept_dialogs=False)
        cdp.ws.settimeout(5)
        cdp.send("Page.enable", timeout=4)                       # 알림창이 뜨고 닫히는 것을 알기 위해
        cdp.send("Page.addScriptToEvaluateOnNewDocument", {"source": ANCHOR_JS}, timeout=4)  # 새로고침 뒤에도 유지
        st["cdp"] = cdp
        st["id"] = page.get("id")

    def run(self):
        while not self._stop.is_set():
            for t in self.targets:
                if self._stop.is_set():
                    break
                try:
                    self._tend(t)
                except Exception:
                    pass
            self._wake.wait(timeout=self.PING_SEC)
            self._wake.clear()
        for st in self._st.values():
            self._drop(st)

    def _tend(self, t):
        match = t["match"]
        name = t.get("name", match)
        st = self._st.setdefault(match, {"cdp": None, "id": None, "fail": 0, "vis": "",
                                         "last_revive": 0.0, "dlg_warned": False})
        try:
            page = find_target_page(self.port, match)
        except Exception:
            self._drop(st)          # 크롬이 꺼짐
            return
        if not page or not page.get("webSocketDebuggerUrl"):
            self._drop(st)
            st["fail"] = 0
            return
        if st["cdp"] is None or st["id"] != page.get("id") or not st["cdp"].ws.connected:
            self._drop(st)
            try:
                self._attach(st, page)
            except Exception:
                self._drop(st)
                return
        cdp = st["cdp"]
        vis = None
        try:
            cdp.ws.settimeout(5)
            vis = cdp.eval_value(ANCHOR_JS)
        except Exception:
            self._drop(st)          # 연결이 끊김(탭이 닫혔거나 바뀜) → 다음 번에 다시 붙는다
            return
        if vis in ("visible", "hidden", "prerender"):
            st["fail"] = 0
            st["vis"] = vis
            st["dlg_warned"] = False
            return
        if not cdp.ws.connected:
            self._drop(st)
            return
        # ── 대답이 없다 ──
        st["fail"] += 1
        if cdp.dialog:
            # 알림창이 떠서 멈춘 것. 내용도 모르고 누르면 위험하니 건드리지 않는다.
            if not st["dlg_warned"]:
                st["dlg_warned"] = True
                self._log("⚠ {} 탭에 알림창이 떠 있어 멈춰 있습니다: “{}” → 그 탭에서 확인해 주세요".format(
                    name, cdp.dialog[:80]))
                try:
                    notify(name + " 확인 필요", "알림창이 떠 있습니다: " + cdp.dialog[:60])
                except Exception:
                    pass
            return
        looked_recently = (st["vis"] == "visible")     # 방금까지 보고 있던 탭이면 잠깐 바쁜 것일 수 있다
        if st["fail"] < 2 or (looked_recently and st["fail"] < 6):
            return
        if time.time() - st["last_revive"] < 60:
            return
        st["last_revive"] = time.time()
        self._revive(name, st)

    def _revive(self, name, st):
        cdp = st["cdp"]
        self._log("⟳ {} 탭이 대답하지 않음(크롬이 탭을 재웠거나 메모리 부족으로 버림) → 새로고침으로 되살리는 중".format(name))
        ok = False
        try:
            cdp.send("Page.reload", {"ignoreCache": False}, timeout=4)    # 브라우저가 처리 → 멈춘 탭에도 통한다
            deadline = time.time() + 25
            while time.time() < deadline and not self._stop.is_set():
                time.sleep(2)
                if not cdp.ws.connected:
                    break
                if cdp.alive(3):
                    ok = True
                    break
        except Exception:
            ok = False
        if ok:
            st["fail"] = 0
            st["vis"] = "hidden"
            time.sleep(3)             # 화면이 다 뜰 시간
            self._log("✓ {} 탭을 되살렸습니다 — 세션 갱신을 바로 다시 합니다".format(name))
            self.ui_post("stat", {"k": "revive"})
            if self.on_revived:
                try:
                    self.on_revived()
                except Exception:
                    pass
        else:
            self._drop(st)
            self._log("✗ {} 탭을 되살리지 못했습니다 — 크롬에서 그 탭을 한 번 눌러 주세요".format(name))
            try:
                notify(name + " 탭 멈춤", "자동으로 되살리지 못했습니다. 크롬에서 그 탭을 한 번 눌러 주세요.")
            except Exception:
                pass


class KeepAliveWorker(threading.Thread):
    def __init__(self, port, targets, interval, ui_post):
        super().__init__(daemon=True)
        self.port = port
        self.targets = targets
        self.interval = max(60, int(interval))
        self.ui_post = ui_post            # (kind, payload) → GUI 큐로 전달
        self._stop = threading.Event()
        self._wake = threading.Event()

    def run(self):
        while not self._stop.is_set():
            self.cycle()
            # 다음 주기까지 대기(중간에 '지금 갱신' 또는 중지 시 깨어남)
            self._wake.wait(timeout=self.interval)
            self._wake.clear()

    auto_reenter = True       # 나이스가 '무효'라고 답하면 깨끗한 재접속을 자동으로 해 볼지
    chrome_path = None

    def cycle(self):
        now = datetime.now().strftime("%H:%M:%S")
        last = getattr(self, "_last_state", None)
        if last is None:
            last = self._last_state = {}
        for t in self.targets:
            if self._stop.is_set():
                return
            try:
                state, detail = do_keepalive(self.port, t)
            except Exception as e:
                state, detail = "실패", str(e)
            # 나이스가 "이 접속은 무효"라고 답함 → 옛 쿠키 지우고 SSO로 다시 들어가 본다
            if state == "경고" and NEIS_ZOMBIE_TAG in detail:
                state, detail = self._heal_neis(t, state, detail)
            self.ui_post("status", {
                "name": t["name"], "state": state, "last": now, "detail": detail
            })
            self.ui_post("log", "[{}] {} → {} {}".format(now, t["name"], state, detail))
            if state == "정상":
                self.ui_post("stat", {"k": "keepalive"})
            # '정상'이던 것이 두 번 연속 정상이 아니면 한 번만 알림 (자리를 비웠다 와도 알 수 있게)
            prev = last.get(t["name"], {"state": None, "bad": 0, "told": False})
            if state == "정상":
                prev = {"state": state, "bad": 0, "told": False}
            else:
                prev = {"state": state, "bad": prev["bad"] + 1, "told": prev["told"]}
                if prev["bad"] >= 2 and not prev["told"] and state != "탭없음":
                    prev["told"] = True
                    try:
                        notify("세션 유지 문제 — " + t["name"], detail[:90])
                    except Exception:
                        pass
            last[t["name"]] = prev
        nxt_sec = self.interval
        self.ui_post("next", nxt_sec)

    def _heal_neis(self, t, state, detail):
        """나이스 '빈 껍데기 접속' 자동 복구. 10분에 1번, 실행 중 최대 3번까지만(다른 PC와 서로 밀어내기 방지)."""
        if not self.auto_reenter:
            return state, detail + " → [나이스 열기]를 누르면 다시 접속합니다"
        hist = getattr(self, "_heal_times", None)
        if hist is None:
            hist = self._heal_times = []
        now = time.time()
        if len(hist) >= 3 or (hist and now - hist[-1] < 600):
            return state, detail + " → 자동 재접속은 이미 시도함. 다른 PC·기기에서 같은 계정으로 접속 중인지 확인하세요"
        hist.append(now)
        self.ui_post("log", "[{}] ⟳ {} — 옛 접속 흔적을 지우고 업무포털 SSO로 다시 접속합니다".format(
            datetime.now().strftime("%H:%M:%S"), detail))
        try:
            ok, d = neis_reenter(self.port, self.chrome_path)
        except Exception as e:
            ok, d = False, str(e)
        if not ok:
            return "경고", "나이스 자동 재접속 실패: " + d
        deadline = time.time() + 30
        while time.time() < deadline and not self._stop.is_set():
            time.sleep(3)
            try:
                st2, d2 = do_keepalive(self.port, t)
            except Exception:
                continue
            if st2 == "정상":
                return "정상", "나이스 자동 재접속 완료 · " + d2
        return "경고", "나이스 재접속을 시도했지만 아직 확인되지 않음 — 다음 주기에 다시 확인"

    def trigger_now(self):
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()


class NeisGuardWorker(threading.Thread):
    """나이스 전용 빠른 감시자: 탭을 얼지 않게 유지 + 연장 팝업 자동 확인.
       (나이스 절대 60분 카운트다운은 팝업 클릭으로만 연장되므로 짧은 주기로 감시)"""

    def __init__(self, port, match, interval, ui_post, capture_path,
                 reload_below_min=0, critical_min=0, extend_below_min=0):
        super().__init__(daemon=True)
        self.port = port
        self.match = match
        self.interval = max(10, int(interval))
        self.ui_post = ui_post
        self.capture_path = capture_path
        self.reload_below_min = float(reload_below_min or 0)
        self.critical_min = float(critical_min or 0)
        self.extend_below_min = float(extend_below_min or 0)
        self._dead_told = False       # '탭 무응답' 로그를 한 번만 찍기 위한 표시
        self._stop = threading.Event()
        self._last_remain = None      # 진단: 잔여시간 로그 스로틀용
        self._reload_cd_until = 0.0   # 리로드 직후 재리로드 방지(쿨다운)

    def run(self):
        while not self._stop.is_set():
            try:
                in_cd = time.time() < self._reload_cd_until
                r = do_neis_guard(
                    self.port, self.match, self.capture_path,
                    reload_below_min=(0 if in_cd else self.reload_below_min),
                    critical_min=(0 if in_cd else self.critical_min),
                    extend_below_min=self.extend_below_min)
                now = datetime.now().strftime("%H:%M:%S")
                remain = (r.get("remain") or "").strip()
                if r.get("reason") == "무응답":
                    _poke_anchor()                 # 탭 지킴이에게 '지금 되살려 달라'
                    if not self._dead_told:
                        self._dead_told = True
                        self.ui_post("log", "[{}] · 나이스 탭이 대답하지 않아 남은 시간을 읽지 못함 → 되살리는 중".format(now))
                    self._stop.wait(timeout=self.interval)
                    continue
                self._dead_told = False
                if r.get("extended"):
                    self._last_remain = None
                    self.ui_post("log", "[{}] ⟳ 나이스 접속시간 연장 (잔여 {} → {}) — 화면 새로고침 없이".format(
                        now, remain or "?", r.get("after") or "?"))
                    self.ui_post("stat", {"k": "popup"})
                elif r.get("reloaded"):
                    self._reload_cd_until = time.time() + 90   # 로드+여유 동안 재리로드 금지
                    self._last_remain = None
                    self.ui_post("log", "[{}] ⟳ 나이스 세션 임박(잔여 {}) → 페이지 새로고침으로 세션 리셋".format(
                        now, remain or "?"))
                    self.ui_post("stat", {"k": "popup"})
                    speak("나이스 세션을 갱신했습니다")
                elif r.get("popup"):
                    if r.get("clicked"):
                        self.ui_post("log", "[{}] ★ 나이스 연장창 감지(잔여 {}) → '{}' 자동 클릭".format(
                            now, remain or "?", r.get("btn") or "확인"))
                        self.ui_post("stat", {"k": "popup"})
                        speak("나이스 세션을 연장했습니다")
                    else:
                        self.ui_post("log", "[{}] ★ 나이스 연장창 감지(확인 버튼 못 찾음) → 팝업 HTML 캡처 저장".format(now))
                elif remain:
                    # 진단용: 잔여시간이 '분' 단위로 바뀔 때만 로그(스팸 방지). 임박(≤11분)하면 항상.
                    mm = remain.split("분")[0]
                    imminent = mm.isdigit() and int(mm) <= 11
                    if mm != self._last_remain or imminent:
                        self._last_remain = mm
                        self.ui_post("log", "[{}] · 나이스 세션 잔여 {}".format(now, remain))
            except Exception:
                pass
            self._stop.wait(timeout=self.interval)

    def stop(self):
        self._stop.set()


class MonitorWorker(threading.Thread):
    """결재 관제: 주기적으로 건수를 읽어, 늘면 데스크톱 알림. (확장 2종 통합)"""

    def __init__(self, port, monitors, interval, ui_post, state):
        super().__init__(daemon=True)
        self.port = port
        self.monitors = monitors
        self.interval = max(30, int(interval))
        self.ui_post = ui_post
        self.state = state                # 영속 상태(dict) — 참조 공유
        self._stop = threading.Event()
        self._wake = threading.Event()

    def run(self):
        while not self._stop.is_set():
            self.cycle()
            self._wake.wait(timeout=self.interval)
            self._wake.clear()

    def cycle(self):
        now = datetime.now().strftime("%H:%M:%S")
        snapshot = []
        today_res = {}                    # '오늘의 업무' 숫자판에도 같은 값을 뿌리기 위한 누적
        for mon in self.monitors:
            name = mon["name"]
            st = self.state.setdefault(name, {})
            try:
                kind, data, status = do_read_monitor(self.port, mon)
            except Exception as e:
                kind, data, status = mon["type"], None, str(e)

            # refresh_today()와 동일한 형태로 담아 두면 _show_today가 그대로 처리
            today_res[mon["type"]] = {"data": data, "status": status, "name": name}

            if data is None:
                snapshot.append({"src": name, "label": "-", "value": "-",
                                 "delta": "", "last": "{} ({})".format(now, status)})
                continue

            if mon["type"] == "edufine":
                last = st.get("counts", {})
                first = "counts" not in st
                for label in ("결재", "공람"):
                    if label not in data:
                        continue
                    cur = data[label]
                    prev = last.get(label)
                    delta = (cur - prev) if isinstance(prev, int) else 0
                    snapshot.append({"src": name, "label": label, "value": cur,
                                     "delta": ("+{}".format(delta) if delta > 0 else
                                               (str(delta) if delta else "")),
                                     "last": now})
                    if (not first) and isinstance(prev, int) and cur > prev:
                        notify("K-에듀파인", "{} {}건 증가 (현재 {}건)".format(label, cur - prev, cur),
                               say="에듀파인 {} {}건 도착".format(label, cur - prev))
                        self.ui_post("log", "[{}] 🔔 에듀파인 {} +{} → {}건".format(now, label, cur - prev, cur))
                        self.ui_post("stat", {"k": "alert"})
                st["counts"] = data

            elif mon["type"] == "neis":
                last = st.get("counts", {})
                first = "counts" not in st
                for label in ("미결/협조", "예결함", "공람함"):
                    if label not in data:
                        continue
                    cur = data[label]
                    prev = last.get(label)
                    delta = (cur - prev) if isinstance(prev, int) else 0
                    snapshot.append({"src": name, "label": label, "value": cur,
                                     "delta": ("+{}".format(delta) if delta > 0 else
                                               (str(delta) if delta else "")),
                                     "last": now})
                    if (not first) and isinstance(prev, int) and cur > prev:
                        toast("나이스", "{} {}건 증가 (현재 {}건)".format(label, cur - prev, cur))
                        self.ui_post("log", "[{}] 🔔 나이스 {} +{} → {}건".format(now, label, cur - prev, cur))
                st["counts"] = data

            elif mon["type"] == "ealimi":
                seen = set(st.get("seen", []))
                first = "seen" not in st
                fresh = []
                for r in data:
                    k = (r.get("title") or "") + " @ " + (r.get("date") or "")
                    if k not in seen:
                        seen.add(k)
                        if not first:
                            fresh.append(r)
                st["seen"] = list(seen)[-800:]
                snapshot.append({"src": name, "label": "결재서류(목록)", "value": len(data),
                                 "delta": ("+{}".format(len(fresh)) if fresh else ""), "last": now})
                for r in fresh:
                    notify("E알리미", "새 서류: {}".format(r.get("title") or ""), say="이알리미 새 서류 도착")
                    self.ui_post("log", "[{}] 🔔 E알리미 새 서류: {} ({})".format(now, r.get("title"), r.get("date")))
                    self.ui_post("stat", {"k": "alert"})

        save_state(self.state)
        self.ui_post("monitor", snapshot)
        self.ui_post("today", today_res)   # '오늘의 업무' 큰 숫자판도 함께 자동 갱신

    def trigger_now(self):
        self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()


def read_alim_notice():
    """실행 중인 알림장(ALIM.EXE) 창의 전달사항 본문(Edit 컨트롤)을 읽어 반환. (text, status).
    - 웹이 아니라 네이티브 앱이라 Win32 WM_GETTEXT로 크로스-프로세스 읽기.
    - 알림장 앱이 켜져 있고 날짜(전달사항)가 보이는 상태여야 함."""
    if os.name != "nt":
        return None, "윈도우 전용"
    import ctypes
    import ctypes.wintypes as wt
    import re as _re
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    WM_GETTEXT, WM_GETTEXTLENGTH, PQI = 0x000D, 0x000E, 0x1000
    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def pid_of(h):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(p))
        return p.value

    def exe_of(pid):
        h = kernel32.OpenProcess(PQI, False, pid)
        if not h:
            return ""
        try:
            size = wt.DWORD(260)
            buf = ctypes.create_unicode_buffer(260)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return buf.value
            return ""
        finally:
            kernel32.CloseHandle(h)

    def clsname(h):
        b = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, b, 256)
        return b.value

    def gettext(h):
        ln = user32.SendMessageW(h, WM_GETTEXTLENGTH, 0, 0)
        if ln <= 0:
            return ""
        b = ctypes.create_unicode_buffer(ln + 1)
        user32.SendMessageW(h, WM_GETTEXT, ln + 1, ctypes.cast(b, ctypes.c_void_p))
        return b.value

    tops = []

    def top_cb(h, l):
        try:
            if exe_of(pid_of(h)).upper().endswith("ALIM.EXE"):
                tops.append(h)
        except Exception:
            pass
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(top_cb), 0)
    except Exception as e:
        return None, str(e)
    if not tops:
        return None, "실행안됨"

    texts = []

    def child_cb(ch, l):
        try:
            if clsname(ch) == "Edit":
                t = gettext(ch)
                if t and len(t) >= 40:
                    texts.append(t)
        except Exception:
            pass
        return True

    for top in tops:
        try:
            user32.EnumChildWindows(top, WNDENUMPROC(child_cb), 0)
        except Exception:
            pass
    if not texts:
        return None, "본문 없음"
    dated = [t for t in texts if _re.search(r"\d{4}\s*년\s*\d+\s*월", t)]
    best = max(dated or texts, key=len)
    return best.replace("\r\n", "\n"), "ok"


# ── 알림장 전달사항: 학교 MySQL 서버에서 직접 읽기 (앱 창을 띄우지 않음) ──
# 알림장(ALIM.EXE)은 전달사항을 학교 MySQL 테이블 ALIM 에 두고
#   SELECT * FROM ALIM WHERE ALDATE = <날짜>
# 로 읽어 화면에 보여준다. 로컬에 그날 전달사항을 담아두는 파일은 없다 —
# 그래서 예전에는 앱을 띄운 뒤 창 글자(WM_GETTEXT)를 긁어야 했다.
# 여기서는 앱이 쓰는 «그 SELECT 를 그대로» 실행해 앱 없이 내용만 가져온다.
# 접속값은 ALIM.EXE 가 쓰는 값과 같아야 한다(host=MYSET.SUIP, 사용자/비번/DB/인코딩은 설정 파일에).
# 서버는 학교 내부망에서만 열리므로 학교 밖에서는 실패 → 호출부가 창 읽기로 폴백한다.
# ALIM 스키마: PCODE(부서) · ALDATE · ALME01..ALME20(본문) · ALFI01..ALFI07(첨부명).
def _dbf_rows(path, want_fields):
    """DBF(xBase)에서 지정 필드만 뽑아 [dict,...] 로. cp949 텍스트."""
    with open(path, "rb") as f:
        b = f.read()
    nrec, hlen, rlen = struct.unpack("<IHH", b[4:12])
    fields, off = [], 32
    while off < hlen - 1 and b[off] != 0x0D:
        raw = b[off:off + 32]
        name = raw[:11].split(b"\x00")[0].decode("cp949", "replace")
        fields.append((name, raw[16]))
        off += 32
    out = []
    for i in range(nrec):
        rec = b[hlen + i * rlen: hlen + (i + 1) * rlen]
        if not rec or rec[:1] == b"*":
            continue
        pos, row = 1, {}
        for name, flen in fields:
            v = rec[pos:pos + flen]; pos += flen
            if name in want_fields:
                row[name] = v.decode("cp949", "replace").strip()
        out.append(row)
    return out


def alim_server_ip(myset_path):
    """MYSET.DBF 의 SUIP(서버 IP). 못 읽으면 ''."""
    try:
        for r in _dbf_rows(myset_path, {"SUIP"}):
            if (r.get("SUIP") or "").strip():
                return r["SUIP"].strip()
    except Exception:
        pass
    return ""


def _dept_map_dbf(agicho_path):
    """로컬 AGICHO.DBF -> {pcode: 부서명}. 못 읽으면 {}."""
    out = {}
    try:
        for r in _dbf_rows(agicho_path, {"PCODE", "ALBU"}):
            try:
                pc = int(float(r.get("PCODE") or 0))
            except Exception:
                continue
            nm = " ".join((r.get("ALBU") or "").split())
            if pc and nm:
                out[pc] = nm
    except Exception:
        pass
    return out


def _diagnose_alim_net(host):
    """왜 알림장 서버에 못 닿는지 사람 말로. Windows 전용, 실패하면 조용히 일반 문구.
       핵심: «출발 IP가 서버와 같은 망인지» + «그 랜카드가 켜져 있는지» 를 본다.
       (연결 끊긴 랜카드도 IP·경로는 남아 있어서, 상태를 안 보면 오진한다)."""
    net3 = ".".join(host.split(".")[:3])
    try:
        ps = ("Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object { "
              "$s=(Get-NetAdapter -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue).Status; "
              "\"$($_.IPAddress)|$($_.InterfaceAlias)|$s\" }")
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, timeout=6,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        lines = [l.strip() for l in (out.stdout or b"").decode("cp949", "replace").splitlines() if "|" in l]
    except Exception:
        return ""
    up_same, down_same, up_any = [], [], []
    for l in lines:
        ip, alias, st = (l.split("|") + ["", "", ""])[:3]
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        same = ip.split(".")[:3] == host.split(".")[:3]
        up = st.strip().lower() == "up"
        if up:
            up_any.append(alias)
        if same and up:
            up_same.append(alias)
        if same and not up:
            down_same.append(alias)
    if up_same:
        return "이 PC는 학교 랜({}.x)에 붙어 있는데 알림장 서버가 응답하지 않습니다 — 서버가 꺼졌거나 방화벽일 수 있어요.".format(net3)
    if down_same:
        return "학교 랜 랜카드 '{}'(가) 연결 끊김 상태입니다 — 랜선/도킹을 다시 꽂은 뒤 [불러오기]를 누르세요.".format(down_same[0])
    if up_any:
        return "지금은 '{}'(으)로만 연결돼 있어 학교 랜({}.x)에 못 닿습니다 — 학교에서 랜선을 꽂거나 EVPN을 켠 뒤 다시 하세요.".format(", ".join(up_any), net3)
    return "네트워크에 연결돼 있지 않은 것 같습니다."


def read_alim_from_db(date8, cfg_db):
    """학교 MySQL 에서 그날(YYYYMMDD) 전달사항을 읽는다. (text, status). 변경 없음(SELECT만).
       status: 'ok'(내용 있음) · '빈날'(연결됐으나 그날 없음) · 그 외 = 실패 사유."""
    if not HAS_PYMYSQL:
        return None, "pymysql 없음 - requirements.txt 설치 필요"
    c = dict(cfg_db or {})
    host = (c.get("host") or "").strip() or alim_server_ip(c.get("myset_path", ""))
    if not host:
        return None, "서버 주소를 찾지 못했습니다 (MYSET.DBF 확인)"
    ymd = "{}-{}-{}".format(date8[:4], date8[4:6], date8[6:])

    # 포트는 학교마다 다를 수 있다(우리학교=3308). 설정 포트 → 대체 포트 순으로 시도.
    ports, seen = [], set()
    for p in [c.get("port", 3308)] + list(c.get("port_fallback", []) or []):
        try:
            p = int(p)
        except Exception:
            continue
        if p not in seen:
            seen.add(p); ports.append(p)
    conn, last_err = None, ""
    for p in ports:
        try:
            conn = pymysql.connect(
                host=host, port=p,
                user=c.get("user", ""), password=str(c.get("password", "")),
                database=c.get("db", "alim"), charset=c.get("charset", "euckr"),
                connect_timeout=int(c.get("timeout", 4)), read_timeout=6, write_timeout=6)
            break
        except Exception as e:
            last_err = str(e).split(chr(10))[0][:80]
    if conn is None:
        why = _diagnose_alim_net(host)
        base = "서버 연결 실패({}:{})".format(host, "/".join(str(p) for p in ports))
        return None, (base + " — " + why) if why else (base + " — 학교망 안(또는 EVPN)에서만 됩니다. " + last_err)
    try:
        dept = {}
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT PCODE, ALBU FROM AGICHO")
                for pc, nm in cur.fetchall():
                    try:
                        dept[int(pc)] = " ".join((nm or "").split())
                    except Exception:
                        pass
        except Exception:
            pass
        if not dept:
            dept = _dept_map_dbf(c.get("agicho_path", ""))

        me = ["ALME%02d" % i for i in range(1, 21)]
        fi = ["ALFI%02d" % i for i in range(1, 8)]
        cols = ["PCODE"] + me + fi
        with conn.cursor() as cur:
            cur.execute("SELECT {} FROM ALIM WHERE ALDATE=%s ORDER BY PCODE".format(
                ", ".join(cols)), (ymd,))
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return "", "빈날"
    blocks = []
    for row in rows:
        d = dict(zip(cols, row))
        try:
            pc = int(d.get("PCODE") or 0)
        except Exception:
            pc = 0
        body = [str(d[k]).rstrip() for k in me if (d.get(k) or "").strip()]
        files = [str(d[k]).strip() for k in fi if (d.get(k) or "").strip()]
        if not body and not files:
            continue
        # 부서명은 «실제로 매핑될 때만» 붙인다. ALIM.PCODE는 부서가 아니라
        # 게시판/작성자 코드일 수 있어(우리학교 오늘=629), 억지 라벨을 달면 오히려 헷갈린다.
        seg = []
        if pc in dept and dept[pc]:
            seg.append("[{}]".format(dept[pc]))
        elif len(rows) > 1:
            seg.append("· 전달사항")
        seg += body
        if files:
            seg.append("  · 첨부: " + ", ".join(files))
        blocks.append("\n".join(seg))
    if not blocks:
        return "", "빈날"
    return "\n\n".join(blocks), "ok"



# ── 급식·학사일정: 나이스 교육정보 개방 API (open.neis.go.kr, 로그인·크롬 불필요) ──
def _neis_open(svc, params):
    q = urllib.parse.urlencode(dict(params, Type="json", pIndex=1, pSize=100))
    url = "https://open.neis.go.kr/hub/%s?%s" % (svc, q)
    with urllib.request.urlopen(url, timeout=8) as r:
        data = json.loads(r.read().decode("utf-8"))
    try:
        return data[svc][1]["row"]
    except Exception:
        return []


def do_fetch_meal(nopen, from_ymd, to_ymd):
    """급식 식단. (list[{date,meal,dishes}], status)."""
    p = {"ATPT_OFCDC_SC_CODE": nopen.get("atpt", ""), "SD_SCHUL_CODE": nopen.get("scode", ""),
         "MLSV_FROM_YMD": from_ymd, "MLSV_TO_YMD": to_ymd}
    try:
        out = []
        for r in _neis_open("mealServiceDietInfo", p):
            dishes = (r.get("DDISH_NM", "") or "").replace("<br/>", "\n")
            dishes = re.sub(r"\s*\([0-9.]+\)", "", dishes)   # 알레르기 번호 제거
            out.append({"date": r.get("MLSV_YMD", ""), "meal": r.get("MMEAL_SC_NM", ""), "dishes": dishes.strip()})
        return out, "ok"
    except Exception as e:
        return None, str(e)


def do_fetch_schedule(nopen, from_ymd, to_ymd):
    """학사일정. (list[{date,event}], status)."""
    p = {"ATPT_OFCDC_SC_CODE": nopen.get("atpt", ""), "SD_SCHUL_CODE": nopen.get("scode", ""),
         "AA_FROM_YMD": from_ymd, "AA_TO_YMD": to_ymd}
    try:
        out = []
        for r in _neis_open("SchoolSchedule", p):
            ev = (r.get("EVENT_NM", "") or "").strip()
            if not ev or ev == "토요휴업일":
                continue
            out.append({"date": r.get("AA_YMD", ""), "event": ev})
        return out, "ok"
    except Exception as e:
        return None, str(e)


class ReminderWorker(threading.Thread):
    """교시·업무 시간표 + 마감 D-Day 알림 (30초마다 점검, 하루 1회 중복방지)."""

    def __init__(self, cfg, ui_post):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.ui_post = ui_post
        self._stop = threading.Event()
        self._fired = set()
        self._day = None

    def run(self):
        while not self._stop.is_set():
            try:
                self.cycle()
            except Exception:
                pass
            self._stop.wait(timeout=30)

    def cycle(self):
        now = datetime.now()
        today = now.date().isoformat()
        if self._day != today:
            self._day, self._fired = today, set()
        hhmm = now.strftime("%H:%M")
        lead_days = int(self.cfg.get("deadline_lead_days", 3))
        for d in self.cfg.get("deadlines", []):
            nm, ds = d.get("name", ""), d.get("date", "")
            try:
                dd = datetime.strptime(ds, "%Y-%m-%d").date()
            except Exception:
                continue
            remain = (dd - now.date()).days
            if 0 <= remain <= lead_days and hhmm == "08:30":
                key = today + "|dday|" + nm + ds
                if key not in self._fired:
                    self._fired.add(key)
                    label = "오늘 마감" if remain == 0 else "D-{}".format(remain)
                    notify("마감 알림", "{} ({})".format(nm, label), say="{} {}".format(nm, label))
                    self.ui_post("log", "[{}] 🗓 {} — {}".format(now.strftime("%H:%M:%S"), nm, label))

    def stop(self):
        self._stop.set()


# ============================================================
#  컴시간(알림이) 로컬 캐시 시간표 리더
#    - alrimi.exe가 dat\tmp\*.kim 에 캐시해 둔 시간표를 서버 없이 직접 읽는다.
#    - 포맷(리버스 엔지니어링 검증 완료):
#        · 교사명.kim  = CP949 텍스트, '^' 구분 (순번 = 위치, 1-based)
#        · 과목명.kim  = '$정식^표시$...' , 과목 index = 1-based(표시명 사용)
#        · 격자 .kim   = int32 LE 배열, stride 63(=9교시*7요일)
#              index = unit*63 + 교시*7 + 요일   (교시 1..8, 요일 1..5=월~금)
#        · 기본교사시간표.kim: unit=교사idx(0based), 셀 = 과목*1000 + 학급라벨(학년*100+반)
#        · 기본학급시간표.kim: unit=학급,           셀 = 과목*1000 + 교사순번(1based)
#              학급 unit = 학년묶음*30 + (반-1)   (묶음0=1학년 …)
#    - 기본*.kim은 '교환 전 원본'. 화면 빨간칸(수업교환)은 주차별 dayhour.kim에 별도 저장.
#    - 주차 데이터(dayhour.kim): 학급표와 동일 인코딩. 한 주 블록 = 180*63 = 11340 int32.
#          index = (주차-1)*11340 + unit*63 + 교시*7 + 요일   (검증: 주차18 == 화면 교환 반영)
#      교사표는 주차 파일이 커서, 주차별 학급데이터를 '역산'해 교환 반영 교사표를 만든다(검증완료).
#    - 주차1 월요일 = base 파일 생성주의 월요일(학기 시작). 주차 범위/오늘 주차가 화면과 일치 검증.
# ============================================================
def find_comci_dir(configured=""):
    """컴시간 알림이 캐시 폴더(...\dat\tmp)를 자동 탐지.
       설치 위치가 PC마다 달라도 .kim 마커 파일이 있는 폴더를 찾아준다.
       탐지 순서: 설정값 → 표준 위치 → Program Files 하위 1단계 스캔.
       못 찾으면 표준 경로를 그대로 반환(로드 시 '읽기 실패'로 안내)."""
    markers = ("교사명.kim", "기본학급시간표.kim")

    def has_kim(d):
        try:
            return bool(d) and all(os.path.exists(os.path.join(d, m)) for m in markers)
        except Exception:
            return False

    pfx = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    cands = []
    if configured:
        cands.append(configured)                                   # 1) 설정값 우선
    for base in (pfx, pf):                                          # 2) 표준 위치(알림이)
        cands.append(os.path.join(base, "알림이", "dat", "tmp"))
    for base in (pfx, pf):                                          # 3) 설치폴더명이 다를 때: 1단계 스캔
        try:
            for name in os.listdir(base):
                tmp = os.path.join(base, name, "dat", "tmp")
                if os.path.isdir(tmp):
                    cands.append(tmp)
        except Exception:
            pass
    for d in cands:
        if has_kim(d):
            return d
    return configured or os.path.join(pfx, "알림이", "dat", "tmp")


def new_call_id():
    """호출 한 건의 번호. 칠판이 '받았음/답장'을 돌려줄 때 이 번호로 어느 호출인지 맞춘다."""
    import secrets
    return secrets.token_hex(3)          # 예: 'a3f09c'


def do_send_call(topic, text, cid=None):
    """교실 호출 전송: ntfy.sh 토픽으로 메시지 POST → 교실 표시기.html에 뜸. (ok, detail).
       cid 를 주면 태그(cid-xxxx)로 실어 보낸다 → 받는 쪽이 수신확인·답장에 그 번호를 붙여 돌려줌.
       보낸 시각은 ntfy가 메시지에 'time' 으로 붙여주므로 따로 안 보낸다."""
    topic = (topic or "").strip()
    if not topic:
        return False, "토픽(채널)이 설정되지 않았습니다 (설정에서 classroom_call.topic 확인)"
    if not (text or "").strip():
        return False, "보낼 내용이 없습니다"
    url = "https://ntfy.sh/" + urllib.parse.quote(topic, safe="")
    try:
        # Priority: urgent → 전자칠판 ntfy 앱이 '긴급'으로 처리(소리+heads-up/전체화면 인텐트).
        #   (HTML 표시기·윈도우 수신기는 이 헤더를 무시하므로 아무 영향 없음.)
        #   ※ Title은 한글이면 헤더 인코딩 오류 → 넣지 않음(본문 한글이 알림 본문으로 그대로 뜸).
        headers = {"Priority": "urgent", "Tags": "bell" + (",cid-" + cid if cid else "")}
        req = urllib.request.Request(url, data=text.encode("utf-8"), method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            if 200 <= getattr(r, "status", 200) < 300:
                return True, "ok"
            return False, "HTTP {}".format(r.status)
    except Exception as e:
        return False, str(e)


def do_send_lan(ip, port, text, key="", cid=None):
    """학교망 직접 전송: 교실 전자칠판의 '교실 호출' 앱(포트 8787)에 바로 POST.
       ntfy(인터넷 중계)를 거치지 않으므로 외부 서버 없이 학교 안에서만 돈다. (ok, detail).
       성공(200) 응답 자체가 '칠판 앱이 받았다'는 수신확인이다."""
    if not ip:
        return False, "칠판 주소가 등록되지 않았습니다"
    url = "http://{}:{}/".format(ip, int(port or 8787))
    try:
        headers = {"Content-Type": "text/plain; charset=utf-8",
                   "X-Sent": str(int(time.time()))}          # 보낸 시각 → 칠판 화면에 표시
        if cid:
            headers["X-Call-Id"] = cid
        if key:
            headers["X-Key"] = key
        req = urllib.request.Request(url, data=text.encode("utf-8"), method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=3) as r:
            if 200 <= getattr(r, "status", 200) < 300:
                return True, "ok"
            return False, "HTTP {}".format(r.status)
    except Exception as e:
        return False, str(e)


def do_ping(base, pid):
    """연결 확인: <base>-ping 채널로 핑 한 번. 칠판 앱(v1.4+)은 화면에 아무것도 안 띄우고
       <base>-reply 로 {"t":"pong","pid",...} 만 돌려준다. (ok, detail)"""
    if not base:
        return False, "채널(base)이 설정되지 않았습니다"
    url = "https://ntfy.sh/" + urllib.parse.quote(base + "-ping", safe="")
    body = json.dumps({"t": "ping", "pid": pid, "at": int(time.time())}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=8) as r:
            return (200 <= getattr(r, "status", 200) < 300), "ok"
    except Exception as e:
        return False, str(e)


def norm_mac(s):
    """'aa:bb:cc:dd:ee:ff' / 'AA-BB-…' / 'aabbccddeeff' → 'AA:BB:CC:DD:EE:FF'. 못 알아보면 ''."""
    h = re.sub(r"[^0-9A-Fa-f]", "", str(s or ""))
    return ":".join(h[i:i + 2] for i in range(0, 12, 2)).upper() if len(h) == 12 else ""


def send_wol(mac, ip=None):
    """Wake-on-LAN '매직 패킷'(FF×6 + MAC×16)을 보낸다. 칠판이 '랜으로 켜기'를 지원하고
       그 설정이 켜져 있고, 이 PC와 같은 네트워크 구간에 있을 때 대기 상태의 칠판이 켜진다.
       반환: 보낸 횟수(0이면 MAC 형식 오류)."""
    import socket
    mac = norm_mac(mac)
    if not mac:
        return 0
    pkt = b"\xff" * 6 + bytes.fromhex(mac.replace(":", "")) * 16
    targets = [("255.255.255.255", 9), ("255.255.255.255", 7)]
    if ip and re.match(r"^\d+\.\d+\.\d+\.\d+$", ip):
        targets += [(ip.rsplit(".", 1)[0] + ".255", 9), (ip, 9)]      # 그 칠판 대역의 방송 + 직접
    sent = 0
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sk.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    for _ in range(3):                                            # 잘 유실되니 몇 번 반복
        for t in targets:
            try:
                sk.sendto(pkt, t)
                sent += 1
            except Exception:
                pass
        time.sleep(0.2)
    sk.close()
    return sent


def arp_mac(ip):
    """윈도우 ARP 표에서 그 IP의 MAC 주소를 찾는다(같은 네트워크 구간이고 방금 통신했을 때만 나옴)."""
    if not ip:
        return ""
    try:
        raw = subprocess.run(["arp", "-a", ip], capture_output=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        out = raw.decode("cp949", "ignore")      # 한글 윈도우 arp 출력은 cp949
        m = re.search(re.escape(ip) + r"\s+([0-9a-fA-F]{2}(?:[-:][0-9a-fA-F]{2}){5})", out)
        return norm_mac(m.group(1)) if m else ""
    except Exception:
        return ""


def do_wake_relay(base, macs, wid):
    """켜져 있는 칠판(앱 v1.5+)들에게 '이 MAC 들을 너희 구간에서 깨워 달라'고 부탁한다.
       교무실 PC와 칠판이 다른 네트워크 구간이라 PC의 Wake-on-LAN 신호가 안 닿기 때문.
       핑 채널로 보내므로 칠판 화면엔 아무것도 뜨지 않는다. (ok, detail)"""
    if not base or not macs:
        return False, "대상 없음"
    url = "https://ntfy.sh/" + urllib.parse.quote(base + "-ping", safe="")
    body = json.dumps({"t": "wake", "wid": wid, "macs": list(macs), "at": int(time.time())}).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=8) as r:
            return (200 <= getattr(r, "status", 200) < 300), "ok"
    except Exception as e:
        return False, str(e)


def probe_lan(ip, port=8787, timeout=2.0):
    """학교망 직접: 칠판 앱의 상태 주소(GET /)를 조회. 살아 있으면 정보 dict, 아니면 None.
       (앱 v1.0부터 있던 기능이라 구버전 칠판도 학교망으로는 확인된다)"""
    if not ip:
        return None
    try:
        req = urllib.request.Request("http://{}:{}/".format(ip, int(port or 8787)))
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def discover_boards(port=8787, wait=1.5):
    """학교망에 있는 '교실 호출' 앱(전자칠판)을 UDP 브로드캐스트로 찾는다.
       반환: [{"ip","class","label","room","port"}, ...]  (같은 망/서브넷에 있을 때만 찾아짐)"""
    import socket
    found = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.3)
        msg = b"CLASSCALL?"
        for addr in ("255.255.255.255", "<broadcast>"):
            try:
                s.sendto(msg, (addr, int(port)))
            except Exception:
                pass
        end = time.time() + wait
        while time.time() < end:
            try:
                data, src = s.recvfrom(2048)
            except Exception:
                continue
            txt = data.decode("utf-8", "replace")
            if not txt.startswith("CLASSCALL!"):
                continue
            try:
                info = json.loads(txt[len("CLASSCALL!"):])
            except Exception:
                continue
            info["ip"] = info.get("ip") or src[0]
            found[info["ip"]] = info
        s.close()
    except Exception:
        pass
    return sorted(found.values(), key=lambda d: d.get("class", ""))


class CallReplyListener(threading.Thread):
    """교실 쪽(칠판 앱·표시기·교실수신기)이 돌려보내는 신호를 듣는다.  채널 = <base>-reply
         {"t":"ack",   "cid":"a3f09c", "cls":"3-6", "via":"app"}            ← 화면에 떴음(수신확인)
         {"t":"reply", "cid":"a3f09c", "cls":"3-6", "reply":"네, 보낼게요"}  ← 선생님이 누른 답장
       끊기면 마지막으로 받은 메시지 뒤부터(since=) 다시 받아, 그 사이 온 답장을 놓치지 않는다."""

    def __init__(self, topic, ui_post):
        super().__init__(daemon=True)
        self.topic = topic
        self.ui_post = ui_post
        self.last_id = None
        self.verify = True

    def _ctx(self):
        import ssl
        if not self.verify:
            return ssl._create_unverified_context()
        try:
            import truststore
            truststore.inject_into_ssl()
        except Exception:
            pass
        return ssl.create_default_context()

    def run(self):
        import ssl
        while True:
            url = "https://ntfy.sh/" + urllib.parse.quote(self.topic, safe="") + "/json"
            if self.last_id:
                url += "?since=" + urllib.parse.quote(self.last_id)
            else:
                url += "?since=12h"      # 처음 켤 때: 오늘 수신확인·답장을 보낸 반을 기억해 두기 위해
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "school-console-reply"})
                with urllib.request.urlopen(req, timeout=90, context=self._ctx()) as r:
                    for raw in r:
                        line = raw.decode("utf-8", "replace").strip()
                        if not line:
                            continue
                        try:
                            d = json.loads(line)
                        except Exception:
                            continue
                        if d.get("event") != "message":
                            continue
                        self.last_id = d.get("id") or self.last_id
                        try:
                            body = json.loads(d.get("message", ""))
                        except Exception:
                            continue
                        if isinstance(body, dict) and (body.get("cid") or body.get("t") in ("pong", "hello")):
                            body["_time"] = d.get("time") or int(time.time())
                            self.ui_post("call_reply", body)
            except Exception as e:
                # urllib 은 SSL 오류를 URLError 로 감싸서 던진다 → 문자열로 판별
                if self.verify and ("CERTIFICATE_VERIFY_FAILED" in str(e) or isinstance(e, ssl.SSLError)):
                    self.verify = False          # 학교망 SSL 가로채기 → 검증 없이 재시도
                    continue
            time.sleep(3)


class ComciReader:
    STRIDE = 63
    WEEK = 180 * 63           # 한 주차 블록 크기(int 개수) = 11340
    DAYS = ("월", "화", "수", "목", "금")

    def __init__(self, tmp_dir):
        self.dir = tmp_dir
        self.teachers = []       # 0-based; 순번 = idx+1
        self.subjects = [""]     # 1-based; subjects[1]=첫 과목 표시명
        self.t_arr = []          # 기본교사시간표
        self.c_arr = []          # 기본학급시간표
        self.day = []            # dayhour.kim (주차별 학급 시간표)
        self.classes = []        # [(unit, label, "1학년 1반"), ...] 데이터 있는 학급만
        self.unit_lab = {}       # 학급 unit -> 라벨(학년*100+반)
        self.nweeks = 0          # 사용 가능한 주차 수
        self.week1_monday = None # 주차1 월요일(date)
        self.mtime = None        # 캐시 파일 갱신 시각(datetime)
        self.max_period = 7

    # ---- 로우레벨 ----
    def _read(self, name):
        with open(os.path.join(self.dir, name), "rb") as f:
            return f.read()

    @staticmethod
    def _ints(b):
        n = len(b) // 4
        return list(struct.unpack("<%di" % n, b[:n * 4]))

    def _cell(self, arr, unit, period, day):
        j = unit * self.STRIDE + period * 7 + day
        return arr[j] if 0 <= j < len(arr) else 0

    # ---- 로드 ----
    def load(self):
        self.teachers = [x for x in self._read("교사명.kim").decode("cp949", "replace").split("^") if x]
        self.subjects = [""]
        for ch in self._read("과목명.kim").decode("cp949", "replace").split("$"):
            if ch:
                self.subjects.append(ch.split("^")[-1])
        self.t_arr = self._ints(self._read("기본교사시간표.kim"))
        self.c_arr = self._ints(self._read("기본학급시간표.kim"))
        self.mtime = datetime.fromtimestamp(
            os.path.getmtime(os.path.join(self.dir, "기본학급시간표.kim")))
        # 주차별 학급 시간표(있으면). 없으면 기본만 사용.
        try:
            self.day = self._ints(self._read("dayhour.kim"))
            self.nweeks = (len(self.day) + self.WEEK - 1) // self.WEEK
        except Exception:
            self.day, self.nweeks = [], 0
        # 주차1 월요일 = 기본시간표 파일이 만들어진 주의 월요일(학기 시작)
        d0 = self.mtime.date()
        self.week1_monday = d0 - timedelta(days=d0.weekday())
        self._build_class_list()
        self._detect_max_period()
        return self

    # ---- 주차 ↔ 날짜 ----
    def week_range(self, w):
        """1-based 주차 → (월요일, 토요일) date. 실패 시 (None, None)."""
        if not self.week1_monday:
            return None, None
        mon = self.week1_monday + timedelta(days=(w - 1) * 7)
        return mon, mon + timedelta(days=5)

    def week_label(self, w):
        s, e = self.week_range(w)
        if s:
            return "{}주차 ({:%m-%d}~{:%m-%d})".format(w, s, e)
        return "{}주차".format(w)

    def current_week(self):
        """오늘이 속한 주차(범위 clamp). 데이터 없으면 마지막 주차."""
        if not self.week1_monday or self.nweeks == 0:
            return 1
        w = (date.today() - self.week1_monday).days // 7 + 1
        return max(1, min(self.nweeks, w))

    def _build_class_list(self):
        # 데이터가 있는 학급 unit만 골라 라벨(학년*100+반)을 교차대조로 확정
        self.classes = []
        nC = len(self.c_arr) // self.STRIDE
        for u in range(nC):
            blk = self.c_arr[u * self.STRIDE:(u + 1) * self.STRIDE]
            if not any(blk):
                continue
            votes = {}
            for p in range(1, 9):
                for d in range(1, 6):
                    v = self._cell(self.c_arr, u, p, d)
                    if not v:
                        continue
                    ti = v % 1000 - 1
                    if 0 <= ti < len(self.teachers):
                        tv = self._cell(self.t_arr, ti, p, d)
                        if tv:
                            lab = tv % 1000
                            votes[lab] = votes.get(lab, 0) + 1
            if votes:
                lab = max(votes, key=votes.get)
                self.classes.append((u, lab, "{}학년 {}반".format(lab // 100, lab % 100)))
                self.unit_lab[u] = lab

    def _detect_max_period(self):
        mp = 1
        for arr, n in ((self.t_arr, len(self.teachers)),
                       (self.c_arr, len(self.c_arr) // self.STRIDE)):
            for u in range(n):
                for p in range(1, 9):
                    if any(self._cell(arr, u, p, d) for d in range(1, 6)):
                        mp = max(mp, p)
        self.max_period = mp

    # ---- 셀 → 표시문자열 ----
    def _subj(self, si):
        return self.subjects[si] if 0 <= si < len(self.subjects) else "?"

    def _weekly_wunit(self, unit):
        """base 학급 unit → dayhour.kim 안에서의 within-week unit.
        주차 파일은 [학년][30반][2](짝수=실데이터), 학년 stride 60·반 stride 2."""
        lab = self.unit_lab.get(unit)
        if lab is None:
            return None
        return (lab // 100 - 1) * 60 + (lab % 100 - 1) * 2

    def _cval(self, unit, period, day, week=None):
        """학급 셀 값. week=None이면 기본(교환 전), 1-based 주차면 dayhour.kim에서."""
        if week is None or not self.day:
            return self._cell(self.c_arr, unit, period, day)
        wu = self._weekly_wunit(unit)
        if wu is None:
            return 0
        j = (week - 1) * self.WEEK + wu * self.STRIDE + period * 7 + day
        return self.day[j] if 0 <= j < len(self.day) else 0

    def teacher_grid(self, ti, week=None):
        """교사 idx → {(교시,요일): '105국어A'}. week 지정 시 그 주 학급데이터를 역산(교환 반영)."""
        if week is None or not self.day:
            g = {}
            for p in range(1, self.max_period + 1):
                for d in range(1, 6):
                    v = self._cell(self.t_arr, ti, p, d)
                    if v:
                        g[(p, d)] = "{}{}".format(v % 1000, self._subj(v // 1000))
            return g
        # 주차: 모든 학급을 스캔해 이 교사가 든 칸을 모은다
        g = {}
        for unit, lab in self.unit_lab.items():
            for p in range(1, self.max_period + 1):
                for d in range(1, 6):
                    v = self._cval(unit, p, d, week)
                    if v and v % 1000 - 1 == ti:
                        g[(p, d)] = "{}{}".format(lab, self._subj(v // 1000))
        return g

    def class_grid(self, unit, week=None):
        """학급 unit → {(교시,요일): '과목\\n교사'}. week 지정 시 교환 반영."""
        g = {}
        for p in range(1, self.max_period + 1):
            for d in range(1, 6):
                v = self._cval(unit, p, d, week)
                if v:
                    tno = v % 1000
                    tname = self.teachers[tno - 1] if 0 <= tno - 1 < len(self.teachers) else str(tno)
                    g[(p, d)] = "{}\n{}".format(self._subj(v // 1000), tname)
        return g


# ============================================================
#  GUI
# ============================================================
class ConsoleApp:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.worker = None
        self.monitor_worker = None
        self.neis_guard = None
        self.reminder = None
        self.state = load_state()
        self.ui_queue = queue.Queue()
        self.status_rows = {}   # name → tree item id
        self.next_at = None

        root.title("학교업무 통합 콘솔")
        root.geometry("1040x720")
        root.minsize(900, 620)

        global TTS_ENABLED
        TTS_ENABLED = bool(self.cfg.get("tts_enabled"))

        self._build_ui()
        self._drain_ui()
        self._refresh_conn_status()
        if not HAS_WS:
            self.log("⚠ websocket-client 미설치 → 세션 유지/조회 기능 사용 불가. "
                     "requirements.txt 설치 필요 (pip install websocket-client)")
        self.reminder = ReminderWorker(self.cfg, self.ui_post)
        self.reminder.start()
        self.call_rows = {}          # 교실 호출 보낸 내역: "cid|반" → 수신/답장 상태
        self._start_call_listener()  # 칠판의 수신확인·답장 듣기(ntfy <base>-reply)
        self._maybe_autostart()

    def _maybe_autostart(self):
        """config의 autostart에 따라 세션유지/관제를 자동 시작 (AHK 원클릭 운영용)."""
        auto = self.cfg.get("autostart", {})
        if HAS_WS and auto.get("keeper"):
            self.root.after(900, self.toggle_keeper)    # UI 안정 후 시작
            self.log("자동 시작: 세션 유지")
        if HAS_WS and auto.get("monitor"):
            self.root.after(1600, self.toggle_monitor)
            self.log("자동 시작: 결재 관제")

    # ---------- UI 구성 ----------
    def _build_ui(self):
        top = ttk.Frame(self.root, padding=(12, 10, 12, 4))
        top.pack(fill="x")
        ttk.Label(top, text="학교업무 통합 콘솔", font=("Malgun Gothic", 16, "bold")).pack(side="left")
        self.conn_var = tk.StringVar(value="크롬 디버그: 확인 중…")
        ttk.Label(top, textvariable=self.conn_var, foreground="#37597a").pack(side="right")

        # ---- 상단 빠른 실행 버튼 (기능별 색 구분) ----
        quick = ttk.Frame(self.root, padding=(12, 0, 12, 6))
        quick.pack(fill="x")
        self._mk_action_btn(quick, "① 로그인 자동화 실행", self.run_login,
                            "#2e8b57", "#256f46").pack(side="left", padx=3)
        ttk.Separator(quick, orient="vertical").pack(side="left", fill="y", padx=6)
        site_colors = {
            "업무포털":   ("#3b6fb0", "#2f5990"),
            "나이스":     ("#2a9d8f", "#218075"),
            "K-에듀파인": ("#e08a3a", "#c0742c"),
            "E알리미":    ("#8155a7", "#6a458a"),
        }
        for name in self.cfg["urls"]:
            bg, act = site_colors.get(name, ("#5a6b7b", "#48555f"))
            self._mk_action_btn(quick, name + " 열기",
                                lambda n=name: self.open_site(n),
                                bg, act).pack(side="left", padx=3)

        # ---- 색상 탭바 (ttk.Notebook 대신 커스텀: 탭마다 고유색) ----
        tabbar = ttk.Frame(self.root, padding=(12, 2, 12, 0))
        tabbar.pack(fill="x")
        container = ttk.Frame(self.root)
        container.pack(fill="both", expand=True, padx=12, pady=6)

        self.tab_today = ttk.Frame(container, padding=12)
        self.tab_query = ttk.Frame(container, padding=12)
        self.tab_naeis = ttk.Frame(container)     # '나이스 업무' (하위 탭 포함)
        self.tab_dday = ttk.Frame(container, padding=12)
        self.tab_meal = ttk.Frame(container, padding=12)
        self.tab_alim = ttk.Frame(container, padding=12)
        self.tab_comci = ttk.Frame(container, padding=12)
        self.tab_call = ttk.Frame(container, padding=12)
        self.tab_set = ttk.Frame(container, padding=12)
        self.tab_gian = ttk.Frame(container, padding=10)

        # (탭이름, 프레임, 선택색(진), 평상색(연))
        tab_defs = [
            ("오늘",           self.tab_today, "#c0504d", "#f6e0de"),
            ("기안 도우미",    self.tab_gian,  "#d35400", "#f8e1d0"),
            ("컴시간 시간표",  self.tab_comci, "#8e44ad", "#ecdff3"),
            ("알림장",         self.tab_alim,  "#3f8f4f", "#d8ecdb"),
            ("급식·일정",      self.tab_meal,  "#c0693a", "#f4e0d3"),
            ("교실 호출",      self.tab_call,  "#c0392b", "#f6dcd8"),
            ("나이스 업무",    self.tab_naeis, "#7b5ea7", "#e6dff0"),
            ("조회 대시보드",  self.tab_query, "#2a9d8f", "#d4ece9"),
            ("마감 D-Day",     self.tab_dday,  "#b5892f", "#f0e6cf"),
            ("설정",           self.tab_set,   "#5a6b7b", "#dde2e6"),
        ]
        self._tab_btns = {}
        self._tab_meta = {}
        for text, frame, solid, pale in tab_defs:
            b = tk.Button(tabbar, text=text, relief="flat", bd=0, cursor="hand2",
                          font=("Malgun Gothic", 10, "bold"),
                          bg=pale, fg="#333", activebackground=solid,
                          activeforeground="white", padx=14, pady=7,
                          command=lambda f=frame: self._select_tab(f))
            b.pack(side="left", padx=(0, 3))
            self._tab_btns[frame] = b
            self._tab_meta[frame] = (solid, pale)

        # '나이스 업무' 안에 하위 탭(복무·기안 템플릿 / 출결 마감) 구성
        self._build_naeis_group()

        self._build_today()          # 오늘 = 건수 카드 + 세션 유지 + 결재 관제
        self._build_query()
        self._build_template()       # → self.tab_tpl (나이스 업무 하위)
        self._build_attendance()     # → self.tab_att (나이스 업무 하위)
        self._build_dday()
        self._build_meal()
        self._build_alim()
        self._build_comci()
        self._build_call()
        self._build_settings()
        self._build_gian()

        self._select_tab(self.tab_today)      # 기본 상위 탭
        self._select_subtab(self.tab_tpl)     # 기본 하위 탭

        log_wrap = ttk.LabelFrame(self.root, text="실행 로그", padding=6)
        log_wrap.pack(fill="both", expand=False, padx=12, pady=(0, 10))
        self.log_text = tk.Text(log_wrap, height=8, font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    # ===================== 오늘의 업무 (통합 대시보드) =====================
    def _build_today(self):
        frm = self.tab_today
        top = ttk.Frame(frm)
        top.pack(fill="x")
        ttk.Label(top, text="오늘의 업무", font=("Malgun Gothic", 15, "bold")).pack(side="left")
        ttk.Button(top, text="새로고침", command=self.refresh_today).pack(side="left", padx=10)
        self.today_updated = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.today_updated, foreground="#777").pack(side="left")
        self.tts_var = tk.BooleanVar(value=bool(self.cfg.get("tts_enabled")))
        ttk.Checkbutton(top, text="🔊 음성 알림", variable=self.tts_var, command=self.toggle_tts).pack(side="right")

        cards = ttk.Frame(frm)
        cards.pack(fill="x", pady=12)
        self.today_vars = {}
        for label, key, color in [("결재 대기(에듀파인)", "결재", "#e0663a"), ("공람(에듀파인)", "공람", "#7b5ea7"),
                                  ("나이스 미결/협조", "neis", "#3a8fbf"), ("E알리미 새 서류", "ealimi", "#2a9d8f")]:
            c = tk.Frame(cards, bg="#f4f6f8")
            c.pack(side="left", expand=True, fill="both", padx=6)
            tk.Label(c, text=label, bg="#f4f6f8", fg="#555", font=("Malgun Gothic", 11)).pack(anchor="w", padx=12, pady=(10, 0))
            v = tk.StringVar(value="-")
            self.today_vars[key] = v
            tk.Label(c, textvariable=v, bg="#f4f6f8", fg=color, font=("Malgun Gothic", 32, "bold")).pack(anchor="w", padx=12, pady=(0, 10))

        self.today_session = tk.StringVar(value="세션 유지: -   ·   관제: -")   # (아래 칸에 상태가 보이므로 표시는 안 함)

        self._build_dashboard(frm)               # 예전 '세션·관제' 탭을 오늘 탭 아래쪽으로 합침
        ttk.Label(frm, foreground="#888", justify="left", text=(
            "· 결재/공람·E알리미는 해당 사이트 탭이 디버그 크롬에 열려 로그인돼 있어야 읽힙니다.\n"
            "· 출결 입력은 '나이스 업무 → 출결 마감', 미마감·수행평가 누락 점검은 '조회 대시보드' 탭에서 하세요.")
        ).pack(anchor="w", pady=(2, 0))

    def toggle_tts(self):
        global TTS_ENABLED
        TTS_ENABLED = bool(self.tts_var.get())
        self.cfg["tts_enabled"] = TTS_ENABLED
        save_config(self.cfg)
        self.log("음성 알림 " + ("켜짐" if TTS_ENABLED else "꺼짐"))
        if TTS_ENABLED:
            speak("음성 알림을 켰습니다")

    def refresh_today(self):
        if not HAS_WS:
            messagebox.showerror("오류", "websocket-client가 필요합니다.")
            return
        self.today_updated.set("· 읽는 중…")

        def work():
            port = self._port()
            res = {}
            for mon in self.cfg.get("monitors", []):
                try:
                    kind, data, status = do_read_monitor(port, mon)
                except Exception as e:
                    kind, data, status = mon.get("type"), None, str(e)
                res[mon.get("type")] = {"data": data, "status": status, "name": mon.get("name")}
            self.ui_post("today", res)
        threading.Thread(target=work, daemon=True).start()

    def _show_today(self, res):
        ed = (res.get("edufine") or {}).get("data")
        self.today_vars["결재"].set(str(ed.get("결재", "-")) if isinstance(ed, dict) else "-")
        self.today_vars["공람"].set(str(ed.get("공람", "-")) if isinstance(ed, dict) else "-")
        nd = (res.get("neis") or {}).get("data")
        self.today_vars["neis"].set(str(nd.get("미결/협조", "-")) if isinstance(nd, dict) else "-")
        ea = (res.get("ealimi") or {})
        docs = ea.get("data")
        if isinstance(docs, list):
            ename = ea.get("name")
            seen = set(((self.state.get(ename, {}) or {}).get("seen", [])) if ename else [])
            fresh = sum(1 for r in docs if ((r.get("title") or "") + " @ " + (r.get("date") or "")) not in seen)
            self.today_vars["ealimi"].set(str(fresh))
        else:
            self.today_vars["ealimi"].set("-")
        sess = "실행 중" if (self.worker and self.worker.is_alive()) else "중지"
        mon = "실행 중" if (self.monitor_worker and self.monitor_worker.is_alive()) else "중지"
        self.today_session.set("세션 유지: {}   ·   관제: {}".format(sess, mon))
        self.today_updated.set("· 갱신 " + datetime.now().strftime("%H:%M:%S"))

    # ===================== 기안 도우미 (K-에듀파인 반복 기안) =====================
    #  예전 기안문을 [재작성]으로 열고 바뀌는 부분만 바꿔 '결재올림 직전'까지 만든다.
    #  결재올림·임시저장은 코드가 누르지 않는다(gian_draft.FORBIDDEN_JS로 막아 둠).
    def _build_gian(self):
        frm = self.tab_gian
        self.gian_groups, self.gian_win, self.gian_vars = [], None, []
        self._gian_orig_title = ""

        top = ttk.Frame(frm)
        top.pack(fill="x")
        ttk.Button(top, text="① 기안한문서 읽기", command=self.gian_load).pack(side="left")
        ttk.Button(top, text="② 재작성 창 열기", command=self.gian_open).pack(side="left", padx=4)
        ttk.Button(top, text="열린 기안 창 읽기", command=self.gian_read_open).pack(side="left", padx=4)
        self.gian_status = tk.StringVar(value="에듀파인 [기안한문서] 화면을 한 번 열어 둔 뒤 ①을 누르세요.")
        ttk.Label(top, textvariable=self.gian_status, foreground="#8a4b14").pack(side="left", padx=10)

        pw = ttk.PanedWindow(frm, orient="horizontal")
        pw.pack(fill="both", expand=True, pady=(6, 0))

        # 왼쪽: 반복 기안 묶음 + 원본 문서 선택
        left = ttk.Frame(pw)
        pw.add(left, weight=2)
        self.gian_gtree = ttk.Treeview(left, columns=("n", "name"), show="headings", height=9)
        self.gian_gtree.heading("n", text="횟수")
        self.gian_gtree.heading("name", text="자주 올린 기안 (최근 문서를 원본으로)")
        self.gian_gtree.column("n", width=40, anchor="center", stretch=False)
        self.gian_gtree.column("name", width=300)
        self.gian_gtree.pack(fill="both", expand=True)
        self.gian_gtree.bind("<<TreeviewSelect>>", self._gian_pick_group)
        ttk.Label(left, text="원본 문서").pack(anchor="w", pady=(6, 0))
        self.gian_src = tk.StringVar()
        self.gian_src_cb = ttk.Combobox(left, textvariable=self.gian_src, state="readonly")
        self.gian_src_cb.pack(fill="x")

        # 오른쪽: [바뀌는 값] / [붙임 파일] 두 장
        nb = ttk.Notebook(pw)
        pw.add(nb, weight=3)
        self.gian_nb = nb
        right = ttk.Frame(nb, padding=4)
        nb.add(right, text=" 바뀌는 값 ")
        attf = ttk.Frame(nb, padding=4)
        nb.add(attf, text=" 붙임 파일 ")
        self._build_gian_att(attf)
        stdf = ttk.Frame(nb, padding=4)
        nb.add(stdf, text=" 표준 점검 ")
        self._build_gian_std(stdf)
        casef = ttk.Frame(nb, padding=4)
        nb.add(casef, text=" 사안 카드 ")
        self._build_gian_case(casef)
        self.gian_vtree = ttk.Treeview(right, columns=("label", "old", "new"), show="headings", height=8)
        for c, t, w in (("label", "항목", 80), ("old", "원본 값", 210), ("new", "새 값", 210)):
            self.gian_vtree.heading(c, text=t)
            self.gian_vtree.column(c, width=w)
        self.gian_vtree.tag_configure("chg", background="#fff1c9")
        self.gian_vtree.pack(fill="both", expand=True)
        self.gian_vtree.bind("<<TreeviewSelect>>", self._gian_pick_var)

        ed = ttk.Frame(right)
        ed.pack(fill="x", pady=(4, 0))
        ttk.Label(ed, text="새 값").pack(side="left")
        self.gian_new = tk.StringVar()
        ent = ttk.Entry(ed, textvariable=self.gian_new)
        ent.pack(side="left", fill="x", expand=True, padx=4)
        ent.bind("<Return>", lambda e: self._gian_set_new())
        ttk.Button(ed, text="반영(Enter)", command=self._gian_set_new).pack(side="left")
        ttk.Button(ed, text="행 추가", command=self._gian_add_pair).pack(side="left", padx=2)
        ttk.Button(ed, text="행 삭제", command=self._gian_del_pair).pack(side="left")

        tl = ttk.Frame(right)
        tl.pack(fill="x", pady=(4, 0))
        ttk.Label(tl, text="새 제목").pack(side="left")
        self.gian_title = tk.StringVar()
        ttk.Entry(tl, textvariable=self.gian_title).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(tl, text="③ 초안에 적용", command=self.gian_apply).pack(side="left")

        boxf = ttk.Frame(frm)
        boxf.pack(fill="x", pady=(6, 0))
        self.gian_box = tk.Text(boxf, height=9, font=("Malgun Gothic", 9), wrap="word")
        sb = ttk.Scrollbar(boxf, orient="vertical", command=self.gian_box.yview)
        self.gian_box.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.gian_box.pack(side="left", fill="x", expand=True)
        self.gian_box.insert("1.0", "사용 순서: ① 기안한문서 읽기 → 왼쪽에서 기안 종류 선택 → ② 재작성 창 열기 → "
                             "오른쪽 표에서 바뀌는 값만 고치기(노란 줄) → ③ 초안에 적용 → [붙임 파일] 장에서 "
                             "새 사안 파일 폴더 고르기 → ④ 붙임 바꿔 끼우기.\n"
                             "결재경로·수신자·공개여부·첨부는 원본에서 그대로 따라옵니다. 관련 학생 표는 기안 창에서 "
                             "직접 확인한 뒤, [결재올림]은 선생님이 누르세요. 콘솔은 결재올림을 누르지 않습니다.")
        if not HAS_GIAN:
            self.gian_status.set("comtypes가 필요합니다:  pip install comtypes  (설치 후 콘솔 재시작)")

    # ---------- 기안 도우미: 붙임 파일 ----------
    #  재작성은 원본 문서의 첨부를 그대로 복사해 온다 → 옛 사안 파일을 새 사안 파일로 바꿔 끼운다.
    #  폴더를 고르면 '옛 파일명에 바뀌는 값을 적용한 이름'으로 새 파일을 찾아 짝지어 준다.
    def _build_gian_att(self, frm):
        self.gian_att_rows, self.gian_butim = [], []
        top = ttk.Frame(frm)
        top.pack(fill="x")
        ttk.Button(top, text="새 파일 폴더…", command=self.gian_att_pick_folder).pack(side="left")
        self.gian_att_dir = tk.StringVar(value=(self.cfg.get("gian", {}) or {}).get("attach_dir", ""))
        ttk.Label(top, textvariable=self.gian_att_dir, foreground="#555").pack(side="left", padx=6)

        self.gian_atree = ttk.Treeview(frm, columns=("no", "old", "new", "act"), show="headings", height=8)
        for c, t, w in (("no", "#", 28), ("old", "지금 첨부", 220), ("new", "→ 바꿔 넣을 파일", 220), ("act", "처리", 70)):
            self.gian_atree.heading(c, text=t)
            self.gian_atree.column(c, width=w, stretch=(c in ("old", "new")), anchor="center" if c in ("no", "act") else "w")
        self.gian_atree.tag_configure("chg", background="#fff1c9")
        self.gian_atree.tag_configure("bad", background="#fbdcdc")
        self.gian_atree.tag_configure("del", foreground="#999")
        self.gian_atree.pack(fill="both", expand=True, pady=(4, 0))

        bt = ttk.Frame(frm)
        bt.pack(fill="x", pady=(4, 0))
        ttk.Button(bt, text="이 행 파일 지정…", command=self.gian_att_pick_file).pack(side="left")
        ttk.Button(bt, text="유지/빼기", command=self.gian_att_toggle).pack(side="left", padx=2)
        ttk.Button(bt, text="파일 추가…", command=self.gian_att_add).pack(side="left")
        ttk.Button(bt, text="▲", width=3, command=lambda: self.gian_att_move(-1)).pack(side="left", padx=(6, 0))
        ttk.Button(bt, text="▼", width=3, command=lambda: self.gian_att_move(1)).pack(side="left")
        ttk.Button(bt, text="④ 붙임 바꿔 끼우기", command=self.gian_att_apply).pack(side="right")

    def _gian_pairs(self):
        """원본 기안 기준 치환표 (③ 적용 뒤에도 처음 원본 → 지금 새 값)."""
        return [(v.get("orig", v["old"]), v["new"]) for v in self.gian_vars if v.get("orig", v["old"]) != v["new"]]

    def _gian_att_load(self, atts, butim):
        self.gian_butim = butim
        self.gian_att_rows = [{"old": a["name"], "path": None, "act": "유지", "how": ""} for a in atts]
        if self.gian_att_dir.get():
            self._gian_att_match(quiet=True)
        else:
            self._gian_att_render()

    def _gian_att_match(self, quiet=False):
        """폴더에서 짝 찾기 (사람이 직접 지정한 행은 건드리지 않음)."""
        olds = [{"name": r["old"]} for r in self.gian_att_rows if r["old"] and r["how"] != "직접"]
        rows, extras = gian_draft.match_attachments(olds, self._gian_pairs(), self.gian_att_dir.get())
        by_old = {x["old"]: x for x in rows}
        for r in self.gian_att_rows:
            x = by_old.get(r["old"])
            if not x or r["how"] == "직접" or r["act"] == "빼기":
                continue
            if x["path"]:
                r.update(path=x["path"], act="교체", how=x["how"])
            elif x["how"] == "없음":
                r.update(path=None, act="유지", how="새 파일 못 찾음")
            else:
                r.update(path=None, act="유지", how="")
        have = {r["path"] for r in self.gian_att_rows if r["path"]}
        extras = [p for p in extras if p not in have]
        self._gian_att_render()
        if extras and not quiet:
            names = "\n".join(os.path.basename(p) for p in extras[:8])
            if messagebox.askyesno("붙임 파일", "폴더에 새 사안 표시가 있는데 짝이 없는 파일이 있습니다:\n\n{}\n\n추가할까요?".format(names)):
                for p in extras:
                    self.gian_att_rows.append({"old": None, "path": p, "act": "추가", "how": "폴더"})
                self._gian_att_render()

    def _gian_att_render(self):
        keep = self.gian_atree.selection()
        self.gian_atree.delete(*self.gian_atree.get_children())
        n = 0
        for i, r in enumerate(self.gian_att_rows):
            if r["act"] != "빼기":
                n += 1
            new = os.path.basename(r["path"]) if r["path"] else ("(빼기)" if r["act"] == "빼기" else (r["how"] or "그대로"))
            tag = ("del",) if r["act"] == "빼기" else ("chg",) if r["act"] in ("교체", "추가") else \
                  ("bad",) if r["how"] == "새 파일 못 찾음" else ()
            act = r["act"] + ("" if not r["how"] or r["how"] in ("직접", "폴더", "새 파일 못 찾음") else " " + r["how"][:6])
            self.gian_atree.insert("", "end", iid=str(i), tags=tag,
                                   values=("" if r["act"] == "빼기" else n, r["old"] or "(새로 추가)", new, act))
        if keep and keep[0] in self.gian_atree.get_children():
            self.gian_atree.selection_set(keep[0])

    def _gian_att_sel(self):
        sel = self.gian_atree.selection()
        return int(sel[0]) if sel else None

    def gian_att_pick_folder(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(title="새 사안 붙임 파일이 든 폴더", initialdir=self.gian_att_dir.get() or str(Path.home()))
        if not d:
            return
        self.gian_att_dir.set(os.path.normpath(d))
        self.cfg.setdefault("gian", {})["attach_dir"] = self.gian_att_dir.get()
        try:
            save_config(self.cfg)
        except Exception:
            pass
        if self.gian_att_rows:
            self._gian_att_match()

    def gian_att_pick_file(self):
        from tkinter import filedialog
        i = self._gian_att_sel()
        if i is None:
            messagebox.showinfo("붙임 파일", "바꿀 첨부 행을 먼저 고르세요.")
            return
        p = filedialog.askopenfilename(title="이 첨부 대신 넣을 파일", initialdir=self.gian_att_dir.get() or str(Path.home()))
        if p:
            r = self.gian_att_rows[i]
            r.update(path=os.path.normpath(p), act="교체" if r["old"] else "추가", how="직접")
            self._gian_att_render()

    def gian_att_toggle(self):
        i = self._gian_att_sel()
        if i is None:
            return
        r = self.gian_att_rows[i]
        if r["old"] is None:                      # 새로 추가한 행 → 목록에서 지움
            del self.gian_att_rows[i]
        elif r["act"] == "빼기":
            r.update(act="교체" if r["path"] else "유지")
        elif r["act"] == "교체":
            r.update(act="유지", path=None, how="")
        else:
            r.update(act="빼기")
        self._gian_att_render()

    def gian_att_add(self):
        from tkinter import filedialog
        ps = filedialog.askopenfilenames(title="추가로 붙일 파일", initialdir=self.gian_att_dir.get() or str(Path.home()))
        for p in ps:
            self.gian_att_rows.append({"old": None, "path": os.path.normpath(p), "act": "추가", "how": "직접"})
        self._gian_att_render()

    def gian_att_move(self, d):
        i = self._gian_att_sel()
        if i is None or not (0 <= i + d < len(self.gian_att_rows)):
            return
        rows = self.gian_att_rows
        rows[i], rows[i + d] = rows[i + d], rows[i]
        self._gian_att_render()
        self.gian_atree.selection_set(str(i + d))

    def gian_att_apply(self):
        if not self.gian_win:
            messagebox.showinfo("붙임 파일", "먼저 ② 재작성 창 열기(또는 열린 기안 창 읽기)를 하세요.")
            return
        plan = [{"old": r["old"], "path": r["path"], "act": r["act"]} for r in self.gian_att_rows]
        if not any(r["act"] != "유지" for r in plan):
            messagebox.showinfo("붙임 파일", "바꿀 첨부가 없습니다. 폴더를 고르거나 행마다 파일을 지정하세요.")
            return
        missing = [r["old"] for r in self.gian_att_rows if r["how"] == "새 파일 못 찾음" and r["act"] == "유지"]
        msg = "\n".join("{}  {}{}".format(r["act"], r["old"] or "", (" → " + os.path.basename(r["path"])) if r["path"] else "")
                        for r in self.gian_att_rows if r["act"] != "유지")
        if missing:
            msg += "\n\n⚠ 새 파일을 못 찾아 옛 파일 그대로 두는 첨부:\n" + "\n".join(missing)
        if not messagebox.askyesno("붙임 바꿔 끼우기", "기안 창의 첨부를 이렇게 바꿉니다:\n\n" + msg + "\n\n진행할까요?"):
            return
        self.gian_status.set("붙임 바꿔 끼우는 중…")
        ie, top = self.gian_win

        def work():
            try:
                res = gian_draft.replace_attachments(ie, top, plan)
                if res.get("ok"):
                    data = gian_draft.read_gianki(ie, top)
                    res["butim"] = data["butim"]
            except Exception as e:
                res = {"ok": False, "err": str(e)}
            self.ui_post("gian_att", res)
        threading.Thread(target=work, daemon=True).start()

    def _show_gian_att(self, r):
        if not r.get("ok"):
            self.gian_status.set("⚠ 붙임 교체 실패: {}".format(r.get("err")))
            if r.get("problems"):
                messagebox.showwarning("붙임 파일", "첨부할 수 없는 파일:\n\n" + "\n".join(r["problems"]))
            return
        self.gian_att_rows = [{"old": a["name"], "path": None, "act": "유지", "how": ""} for a in r["atts"]]
        self._gian_att_render()
        pairs, left = gian_draft.compare_butim(r.get("butim", []), r["atts"])
        lines = ["[{}] 붙임 교체 완료 — 뺀 것 {}개, 넣은 것 {}개{}".format(
            r["at"], r["removed"], r["added"], "" if r["order_ok"] else "  ⚠ 순서 확인 필요"), "",
            "본문 '붙임' 목록 ↔ 실제 첨부 대조:"]
        for b, hit in pairs:
            lines.append("  {} {}{}".format("✓" if hit else "⚠", b, "" if hit else "  ← 맞는 첨부 없음"))
        for n in left:
            lines.append("  ⚠ 본문 붙임에 없는 첨부: " + n)
        if not pairs:
            lines.append("  (본문에서 붙임 목록을 찾지 못함)")
        for a in r.get("alerts", []):
            lines.append("⚠ 에듀파인 알림: " + a)
        lines.append("")
        lines.append("기안 창의 [결재정보] 탭 첨부 목록을 눈으로 확인한 뒤 [결재올림]은 직접 누르세요.")
        self.gian_box.delete("1.0", "end")
        self.gian_box.insert("1.0", "\n".join(lines))
        ok = all(h for _b, h in pairs) and not left and r["order_ok"]
        self.gian_status.set("붙임 교체 완료 — " + ("본문 붙임과 일치 ✓" if ok else "대조 결과에 ⚠ 있음, 확인 필요"))
        self.log("[{}] 기안 도우미: 붙임 교체 (뺌 {}, 넣음 {})".format(r["at"], r["removed"], r["added"]))

    # ---------- 기안 도우미: 사안 카드 ----------
    #  사안번호마다 단계(접수→개최→결과 보고→자체해결/심의 요청→조치 이행)를 모아 보여 주고,
    #  [이 단계 초안 만들기] = 그 단계의 가장 최근 기안을 재작성 + 사안번호·관련 문서·카드 값을 '새 값'에 미리 채움.
    #  카드 파일(gian_cases.json)에는 사안번호·종결 여부·선생님이 적은 값(접수 일시 등)만 둔다(PC 안에만).
    GIAN_CASE_PATH = APP_DIR / "gian_cases.json"

    def _build_gian_case(self, frm):
        self.gian_cases_saved = gian_draft.load_cases(self.GIAN_CASE_PATH) if HAS_GIAN else {}
        self.gian_case_rows, self.gian_case_list, self._gian_case_stages = [], [], []
        self._gian_case_ctx = None
        self._gian_case_active = None
        top = ttk.Frame(frm)
        top.pack(fill="x")
        ttk.Button(top, text="사안 읽기", command=self.gian_case_load).pack(side="left")
        ttk.Button(top, text="새 사안", command=self.gian_case_new).pack(side="left", padx=3)
        ttk.Button(top, text="종결 / 다시 열기", command=self.gian_case_toggle).pack(side="left")

        self.gian_ctree = ttk.Treeview(frm, columns=("case", "last", "days", "next"), show="headings", height=4)
        for c, t, w in (("case", "사안번호", 110), ("last", "마지막 단계", 140), ("days", "접수 후", 55),
                        ("next", "다음 할 일", 230)):
            self.gian_ctree.heading(c, text=t)
            self.gian_ctree.column(c, width=w, stretch=(c == "next"), anchor="center" if c == "days" else "w")
        self.gian_ctree.tag_configure("closed", foreground="#999999")
        self.gian_ctree.tag_configure("todo", background="#fff1c9")
        self.gian_ctree.pack(fill="x", pady=(4, 0))
        self.gian_ctree.bind("<<TreeviewSelect>>", self._gian_case_pick)

        self.gian_sttree = ttk.Treeview(frm, columns=("name", "docno", "date", "state"), show="headings", height=6)
        for c, t, w in (("name", "단계", 170), ("docno", "문서번호", 120), ("date", "기안일 (결재)", 120),
                        ("state", "상태", 70)):
            self.gian_sttree.heading(c, text=t)
            self.gian_sttree.column(c, width=w, stretch=(c == "name"), anchor="w" if c in ("name", "docno") else "center")
        self.gian_sttree.tag_configure("next", background="#fff1c9")
        self.gian_sttree.tag_configure("done", foreground="#1f6f3a")
        self.gian_sttree.tag_configure("none", foreground="#999999")
        self.gian_sttree.pack(fill="both", expand=True, pady=(4, 0))
        self.gian_sttree.bind("<<TreeviewSelect>>", self._gian_case_stage_pick)
        ttk.Button(frm, text="이 단계 초안 만들기 (재작성 → 값 미리 채움)",
                   command=self.gian_case_draft).pack(anchor="e", pady=(3, 0))

        ttk.Label(frm, text="카드에 적어 둔 값 — 초안을 만들 때 같은 항목에 자동으로 넣고, ③ 적용한 값은 여기 저장됩니다",
                  foreground="#777").pack(anchor="w", pady=(4, 0))
        self.gian_cvtree = ttk.Treeview(frm, columns=("label", "value"), show="headings", height=3)
        self.gian_cvtree.heading("label", text="항목")
        self.gian_cvtree.heading("value", text="값")
        self.gian_cvtree.column("label", width=110, stretch=False)
        self.gian_cvtree.column("value", width=360)
        self.gian_cvtree.pack(fill="x")
        self.gian_cvtree.bind("<<TreeviewSelect>>", self._gian_case_var_pick)
        ed = ttk.Frame(frm)
        ed.pack(fill="x", pady=(2, 0))
        self.gian_cv_label, self.gian_cv_value = tk.StringVar(), tk.StringVar()
        ttk.Entry(ed, textvariable=self.gian_cv_label, width=12).pack(side="left")
        ttk.Entry(ed, textvariable=self.gian_cv_value).pack(side="left", fill="x", expand=True, padx=3)
        ttk.Button(ed, text="넣기·고치기", command=self.gian_case_var_set).pack(side="left")
        ttk.Button(ed, text="빼기", command=self.gian_case_var_del).pack(side="left", padx=(3, 0))

    def _gian_case_save(self):
        try:
            gian_draft.save_cases(self.GIAN_CASE_PATH, self.gian_cases_saved)
        except Exception as e:
            self.log("사안 카드 저장 실패: {}".format(e))

    def gian_case_load(self):
        if not (HAS_WS and HAS_GIAN):
            messagebox.showerror("오류", "websocket-client와 comtypes가 필요합니다.")
            return
        self.gian_status.set("기안한문서에서 사안별 기안 읽는 중… (읽기만)")

        def prog(i, n):
            self.ui_post("gian_prog", "사안 기안 본문 읽는 중… {}/{} (읽기만)".format(i, n))

        def work():
            try:
                rows, st = gian_draft.read_doc_meta(self._port(), find_target_page, CDP, with_body=True, progress=prog)
            except Exception as e:
                rows, st = None, str(e)
            self.ui_post("gian_case", {"rows": rows, "status": st})
        threading.Thread(target=work, daemon=True).start()

    def _show_gian_case(self, p):
        if not p["rows"]:
            self.gian_status.set("⚠ " + p["status"])
            return
        self.gian_case_rows = p["rows"]
        self._gian_case_refresh()
        n_open = sum(1 for c in self.gian_case_list if not c["closed"])
        self.gian_status.set("사안 {}건 (진행 중 {}건) — {}. 사안을 고르고 노란 단계에서 '초안 만들기'".format(
            len(self.gian_case_list), n_open, p["status"]))

    def _gian_case_refresh(self, keep=None):
        keep = keep or (self._gian_case_sel() or {}).get("case")
        self.gian_case_list = gian_draft.build_cases(self.gian_case_rows, self.gian_cases_saved)
        self.gian_ctree.delete(*self.gian_ctree.get_children())
        for i, c in enumerate(self.gian_case_list):
            last = gian_draft.stage_name(c["last"]) if c["last"] else "(아직 없음)"
            nxt = "종결" if c["closed"] else (" 또는 ".join(gian_draft.stage_name(k) for k in c["next"]) or "-")
            days = "{}일".format(c["days"]) if c["days"] != "" else ""
            self.gian_ctree.insert("", "end", iid=str(i), values=(c["case"], last, days, nxt),
                                   tags=("closed",) if c["closed"] else ("todo",) if c["next"] else ())
        idx = next((i for i, c in enumerate(self.gian_case_list) if c["case"] == keep), None)
        if idx is not None:
            self.gian_ctree.selection_set(str(idx))
            self.gian_ctree.see(str(idx))
        else:
            self.gian_sttree.delete(*self.gian_sttree.get_children())
            self.gian_cvtree.delete(*self.gian_cvtree.get_children())

    def _gian_case_sel(self):
        sel = self.gian_ctree.selection() if hasattr(self, "gian_ctree") else ()
        return self.gian_case_list[int(sel[0])] if sel and int(sel[0]) < len(self.gian_case_list) else None

    def _gian_case_pick(self, _e=None):
        c = self._gian_case_sel()
        if not c:
            return
        self._gian_case_stages = gian_draft.case_stages(c)
        self.gian_sttree.delete(*self.gian_sttree.get_children())
        first_next = None
        for i, s in enumerate(self._gian_case_stages):
            d = ""
            if s["draft"]:
                d = "{}-{}".format(s["draft"][4:6], s["draft"][6:8])
                if s["done"] and s["done"] != s["draft"]:
                    d += " ({}-{})".format(s["done"][4:6], s["done"][6:8])
            tag = "next" if s["state"] == "다음" else "done" if s["state"].startswith("완료") else "none"
            if tag == "next" and first_next is None:
                first_next = i
            self.gian_sttree.insert("", "end", iid=str(i), tags=(tag,),
                                    values=(s["name"], s["docno"], d, s["state"] or "-"))
        self._gian_case_render_vars(c)
        if first_next is not None:
            self.gian_sttree.selection_set(str(first_next))      # → _gian_case_stage_pick 이 계획을 보여 줌
        else:
            self.gian_box.delete("1.0", "end")
            self.gian_box.insert("1.0", "[{}] {} — 문서 {}건{}".format(
                c["case"], "종결" if c["closed"] else "진행 중", len(c["docs"]),
                "" if self.gian_case_rows else "  ('사안 읽기'를 누르면 올린 기안이 단계별로 나옵니다)"))

    def _gian_case_render_vars(self, c):
        self.gian_cvtree.delete(*self.gian_cvtree.get_children())
        for i, (k, v) in enumerate(sorted(self.gian_cases_saved.get(c["case"], {}).get("vars", {}).items())):
            self.gian_cvtree.insert("", "end", iid=str(i), values=(k, v))

    def _gian_case_stage(self):
        sel = self.gian_sttree.selection()
        return self._gian_case_stages[int(sel[0])] if sel and int(sel[0]) < len(self._gian_case_stages) else None

    def _gian_case_stage_pick(self, _e=None):
        c, s = self._gian_case_sel(), self._gian_case_stage()
        if not (c and s):
            return
        lines = ["[{}] {} — {}".format(c["case"], s["name"], s["state"] or "아직 안 올림")]
        if not self.gian_case_rows:
            lines.append("먼저 [사안 읽기]를 누르세요.")
        else:
            p = gian_draft.plan_stage(self.gian_case_rows, c["case"], s["key"])
            if p.get("err"):
                lines.append(p["err"])
            else:
                lines.append("초안 원본: {} '{}' (이 단계의 가장 최근 기안)".format(p["src"], p["src_title"]))
                for pr in p["pairs"]:
                    if pr["old"] != pr["new"]:
                        lines.append("  · {}: '{}' → '{}'".format(pr["label"], pr["old"], pr["new"]))
                lines += ["  " + n for n in p["notes"] if n.startswith("⚠")]
                cv = self.gian_cases_saved.get(c["case"], {}).get("vars", {})
                if cv:
                    lines.append("  · 카드 값도 같은 항목에 채움: " + ", ".join(cv))
                lines.append("→ [이 단계 초안 만들기]를 누르면 재작성 창을 열고 위 값을 '바뀌는 값'에 미리 넣습니다. "
                             "남은 값(일시·학생 등)을 확인하고 ③ 적용 → 붙임 → 표준 점검 → 결재올림은 직접.")
        self.gian_box.delete("1.0", "end")
        self.gian_box.insert("1.0", "\n".join(lines))

    def gian_case_new(self):
        from tkinter import simpledialog
        known = [c["case"] for c in self.gian_case_list] or list(self.gian_cases_saved)
        no = simpledialog.askstring("새 사안", "사안번호 (예: 우리중-2026-11)",
                                    initialvalue=gian_draft.next_case_no(known), parent=self.root)
        no = (no or "").strip()
        if not no:
            return
        if not gian_draft._CASENO_RE.fullmatch(no):
            messagebox.showwarning("새 사안", "'학교-연도-번호' 꼴로 적어 주세요 (예: 우리중-2026-11).")
            return
        self.gian_cases_saved.setdefault(no, {"vars": {}, "created": datetime.now().strftime("%Y-%m-%d %H:%M")})
        self._gian_case_save()
        self._gian_case_refresh(keep=no)
        self.gian_status.set("사안 {} 카드를 만들었습니다 — '사안 접수' 단계에서 초안 만들기".format(no))

    def gian_case_toggle(self):
        c = self._gian_case_sel()
        if not c:
            return
        self.gian_cases_saved.setdefault(c["case"], {"vars": {}})["closed"] = not c["closed"]
        self._gian_case_save()
        self._gian_case_refresh(keep=c["case"])

    def _gian_case_var_pick(self, _e=None):
        sel = self.gian_cvtree.selection()
        if sel:
            k, v = self.gian_cvtree.item(sel[0], "values")
            self.gian_cv_label.set(k)
            self.gian_cv_value.set(v)

    def gian_case_var_set(self):
        c = self._gian_case_sel()
        k, v = self.gian_cv_label.get().strip(), self.gian_cv_value.get().strip()
        if not (c and k):
            return
        self.gian_cases_saved.setdefault(c["case"], {"vars": {}}).setdefault("vars", {})[k] = v
        self._gian_case_save()
        self._gian_case_render_vars(c)

    def gian_case_var_del(self):
        c = self._gian_case_sel()
        k = self.gian_cv_label.get().strip()
        if c and k:
            self.gian_cases_saved.get(c["case"], {}).get("vars", {}).pop(k, None)
            self._gian_case_save()
            self._gian_case_render_vars(c)

    def gian_case_draft(self):
        c, s = self._gian_case_sel(), self._gian_case_stage()
        if not (c and s):
            messagebox.showinfo("사안 카드", "사안과 단계를 먼저 고르세요.")
            return
        if not self.gian_case_rows:
            messagebox.showinfo("사안 카드", "먼저 [사안 읽기]를 누르세요.")
            return
        p = gian_draft.plan_stage(self.gian_case_rows, c["case"], s["key"])
        if p.get("err"):
            messagebox.showinfo("사안 카드", p["err"])
            return
        if s["state"].startswith("완료") and not messagebox.askyesno(
                "사안 카드", "{} '{}' 단계는 이미 {}로 올렸습니다. 그래도 새 초안을 만들까요?".format(
                    c["case"], s["name"], s["docno"])):
            return
        chg = ["{}: {} → {}".format(x["label"], x["old"], x["new"]) for x in p["pairs"] if x["old"] != x["new"]]
        warn = [n for n in p["notes"] if n.startswith("⚠")]
        if not messagebox.askyesno("사안 카드 — 초안 만들기", "{} '{}' 초안을 만듭니다.\n\n원본: {}\n{}\n\n{}{}"
                                   "재작성 창을 열고 값만 미리 채웁니다(③ 적용은 선생님이). 결재올림은 하지 않습니다.".format(
                                       c["case"], s["name"], p["src_title"], p["src"], "\n".join(chg) + "\n" if chg else "",
                                       "\n".join(warn) + "\n\n" if warn else "")):
            return
        self._gian_case_ctx = {"case": c["case"], "key": s["key"], "name": s["name"], "plan": p,
                               "vars": dict(self.gian_cases_saved.get(c["case"], {}).get("vars", {}))}
        self.gian_open(docno=p["src"])

    def _gian_case_prefill(self, ctx, text):
        """재작성 창이 열린 뒤: 계획한 값과 카드 값을 '새 값'에 넣는다 (적용은 선생님이 ③)."""
        filled, warn = [], []
        for pr in ctx["plan"]["pairs"]:
            old = pr["old"]
            if old not in text:                       # 본문 글자와 띄어쓰기만 다를 때 실제 글자로 맞춤
                m = re.search(r"\s*".join(re.escape(ch) for ch in old.replace(" ", "")), text)
                if m:
                    old = m.group(0)
                elif old not in self._gian_orig_title:
                    warn.append("⚠ '{}'을 기안 창에서 찾지 못했습니다 — 직접 확인".format(pr["old"]))
                    continue
            v = next((v for v in self.gian_vars if v["old"] == old), None)
            if v is None:
                v = {"label": pr["label"], "old": old, "new": old, "orig": old}
                self.gian_vars.insert(0, v)
            elif pr["label"].startswith("관련"):
                v["label"] = pr["label"]
            v["new"] = pr["new"]
            if pr["old"] != pr["new"]:
                filled.append("{}: '{}' → '{}'".format(pr["label"], old, v["new"]))
        # 관련 줄 통째로 바꾸는 값이 있으면, 그 안에 든 문서번호·날짜 조각(다른 곳엔 없는 것)은 표에서 숨김
        whole = [v for v in self.gian_vars if v["label"].startswith("관련 문서")]
        self.gian_vars = [v for v in self.gian_vars if not any(
            v is not w and v["new"] == v["old"] and v["old"] in w["old"] and text.count(v["old"]) <= text.count(w["old"])
            for w in whole)]
        for v in self.gian_vars:
            val = ctx["vars"].get(v["label"])
            if val and v["new"] == v["old"] and val != v["old"]:
                v["new"] = val
                filled.append("{} (카드 값): '{}' → '{}'".format(v["label"], v["old"], val))
        self._gian_render_vars()
        head = ["[사안 카드] {} '{}' 초안 — 원본 {}".format(ctx["case"], ctx["name"], ctx["plan"]["src"]),
                "미리 채운 값 (노란 줄, 아직 기안 창엔 안 들어감 → 확인 후 ③ 초안에 적용):"]
        head += ["  ✓ " + f for f in filled] or ["  (없음)"]
        head += ["  " + n for n in ctx["plan"]["notes"] if n.startswith("⚠")] + ["  " + w for w in warn]
        head.append("남은 값(일시·장소·관련 학생 등)은 표에서 직접 고치세요. 관련 학생 표는 기안 창에서 확인.")
        return head

    # ---------- 기안 도우미: 표준 점검 ----------
    #  과거 기안을 서버 조회로 읽어 종류별 표준(결재선·과제카드·공개·열람제한·수신·첨부)을 제안 →
    #  선생님이 확정 → 재작성 창을 열 때마다 자동 대조. 공개·열람제한은 버튼으로 표준대로 맞춤.
    GIAN_STD_PATH = APP_DIR / "gian_standards.json"

    def _build_gian_std(self, frm):
        self.gian_stds = gian_draft.load_standards(self.GIAN_STD_PATH) if HAS_GIAN else {}
        self._gian_std_keys = []
        top = ttk.Frame(frm)
        top.pack(fill="x")
        ttk.Button(top, text="표준 만들기·갱신", command=self.gian_std_build).pack(side="left")
        ttk.Button(top, text="열린 창 점검", command=self.gian_std_check).pack(side="left", padx=3)
        ttk.Button(top, text="공개·열람제한 맞추기", command=self.gian_std_fix).pack(side="left")

        self.gian_stree = ttk.Treeview(frm, columns=("name", "n", "st", "sp"), show="headings", height=5)
        for c, t, w in (("name", "기안 종류", 260), ("n", "건수", 40), ("st", "표준", 50), ("sp", "들쭉날쭉", 60)):
            self.gian_stree.heading(c, text=t)
            self.gian_stree.column(c, width=w, stretch=(c == "name"), anchor="w" if c == "name" else "center")
        self.gian_stree.tag_configure("warn", background="#fff1c9")
        self.gian_stree.tag_configure("done", foreground="#1f6f3a")
        self.gian_stree.pack(fill="both", expand=True, pady=(4, 0))
        self.gian_stree.bind("<<TreeviewSelect>>", self._gian_std_pick)

        g = ttk.Frame(frm)
        g.pack(fill="x", pady=(4, 0))
        self.gian_std_cb = {}
        for i, (f, label) in enumerate(gian_draft.FIELDS if HAS_GIAN else []):
            ttk.Label(g, text=label).grid(row=i, column=0, sticky="w")
            cb = ttk.Combobox(g, width=58)
            cb.grid(row=i, column=1, sticky="we", padx=4, pady=1)
            self.gian_std_cb[f] = cb
        n = len(self.gian_std_cb)
        ttk.Label(g, text="지정날짜").grid(row=n, column=0, sticky="w")
        self.gian_std_mmdd = tk.StringVar()
        dd = ttk.Frame(g)
        dd.grid(row=n, column=1, sticky="w", padx=4)
        ttk.Entry(dd, textvariable=self.gian_std_mmdd, width=7).pack(side="left")
        ttk.Label(dd, text="(월-일, 예: 03-02 → 다가오는 그 날짜로 설정)", foreground="#777").pack(side="left", padx=4)
        ttk.Label(g, text="필수 첨부").grid(row=n + 1, column=0, sticky="w")
        self.gian_std_req = tk.StringVar()
        ttk.Entry(g, textvariable=self.gian_std_req).grid(row=n + 1, column=1, sticky="we", padx=4, pady=1)
        ttk.Label(g, text="선택 첨부").grid(row=n + 2, column=0, sticky="w")
        self.gian_std_opt = tk.StringVar()
        ttk.Entry(g, textvariable=self.gian_std_opt).grid(row=n + 2, column=1, sticky="we", padx=4, pady=1)
        g.columnconfigure(1, weight=1)
        ttk.Button(frm, text="이 값으로 표준 확정", command=self.gian_std_confirm).pack(anchor="e", pady=(4, 0))
        self._gian_std_render()

    def _gian_std_render(self):
        self.gian_stree.delete(*self.gian_stree.get_children())
        items = sorted(self.gian_stds.items(), key=lambda kv: (-kv[1].get("count", 0), kv[1].get("name", "")))
        self._gian_std_keys = [k for k, _s in items]
        for i, (k, s) in enumerate(items):
            sp = s.get("spread", 0)
            tag = ("done",) if s.get("confirmed") else ("warn",) if sp else ()
            self.gian_stree.insert("", "end", iid=str(i), tags=tag, values=(
                s.get("name", ""), s.get("count", 0), "확정" if s.get("confirmed") else "제안", sp or ""))

    def _gian_std_sel(self):
        sel = self.gian_stree.selection()
        return self._gian_std_keys[int(sel[0])] if sel else None

    def _gian_std_pick(self, _e=None):
        k = self._gian_std_sel()
        if not k:
            return
        s = self.gian_stds[k]
        lines = ["[{}] 표준 {} — 문서 {}건".format(s["name"], "확정" if s.get("confirmed") else "제안", s.get("count", 0))]
        for f, label in gian_draft.FIELDS:
            fv = s["fields"].get(f, {})
            vals = [v for v, _d in fv.get("variants", [])]
            if fv.get("value") and fv["value"] not in vals:
                vals.insert(0, fv["value"])
            self.gian_std_cb[f]["values"] = vals
            self.gian_std_cb[f].set(fv.get("value", ""))
            if len(fv.get("variants", [])) > 1:
                lines.append("⚠ {}: ".format(label) + "  /  ".join(
                    "{} [{}]".format(v, ",".join(d)) for v, d in fv["variants"]))
        for sp in s.get("att_spellings", []):
            lines.append("⚠ 첨부 이름 표기가 섞임: " + "  /  ".join(sp))
        self.gian_std_mmdd.set(s.get("read_mmdd", ""))
        self.gian_std_req.set(" | ".join(s.get("atts_required", [])))
        self.gian_std_opt.set(" | ".join(s.get("atts_optional", [])))
        if len(lines) == 1:
            lines.append("✓ 결재선·과제카드·공개·열람제한·수신·첨부는 지금까지 올린 문서가 모두 같습니다.")
        lines.append("")
        lines.extend(gian_draft.body_template_lines(s))
        self.gian_box.delete("1.0", "end")
        self.gian_box.insert("1.0", "\n".join(lines))

    def gian_std_build(self):
        if not (HAS_WS and HAS_GIAN):
            messagebox.showerror("오류", "websocket-client와 comtypes가 필요합니다.")
            return
        self.gian_status.set("과거 기안의 결재선·과제카드·공개여부 읽는 중… (읽기만)")

        def prog(i, n):
            self.ui_post("gian_prog", "과거 기안 본문 읽는 중… {}/{} (읽기만)".format(i, n))

        def work():
            try:
                rows, st = gian_draft.read_doc_meta(self._port(), find_target_page, CDP,
                                                    with_body=True, progress=prog)
            except Exception as e:
                rows, st = None, str(e)
            self.ui_post("gian_std", {"rows": rows, "status": st})
        threading.Thread(target=work, daemon=True).start()

    def _show_gian_std(self, p):
        if not p["rows"]:
            self.gian_status.set("⚠ " + p["status"])
            return
        prop = gian_draft.propose_standards(p["rows"])
        self.gian_stds = gian_draft.merge_standards(self.gian_stds, prop)
        try:
            gian_draft.save_standards(self.GIAN_STD_PATH, self.gian_stds)
        except Exception as e:
            self.log("기안 표준 저장 실패: {}".format(e))
        self._gian_std_render()
        n_sp = sum(1 for s in self.gian_stds.values() if s.get("spread") and not s.get("confirmed"))
        self.gian_status.set("표준 {}종 ({}) — 들쭉날쭉한 종류 {}개. 종류를 골라 값 확인 후 '확정'".format(
            len(self.gian_stds), p["status"], n_sp))
        self.log("[{}] 기안 표준: {}종 갱신".format(datetime.now().strftime("%H:%M:%S"), len(self.gian_stds)))

    def gian_std_confirm(self):
        k = self._gian_std_sel()
        if not k:
            messagebox.showinfo("기안 표준", "확정할 기안 종류를 먼저 고르세요.")
            return
        s = self.gian_stds[k]
        for f, _l in gian_draft.FIELDS:
            s["fields"].setdefault(f, {})["value"] = self.gian_std_cb[f].get().strip()
        mmdd = self.gian_std_mmdd.get().strip()
        if mmdd and not re.match(r"^\d{2}-\d{2}$", mmdd):
            messagebox.showwarning("기안 표준", "지정날짜는 '03-02'처럼 월-일로 적어 주세요.")
            return
        s["read_mmdd"] = mmdd
        split = lambda t: [x.strip() for x in t.split("|") if x.strip()]
        s["atts_required"], s["atts_optional"] = split(self.gian_std_req.get()), split(self.gian_std_opt.get())
        s["confirmed"] = True
        s["confirmed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
        gian_draft.save_standards(self.GIAN_STD_PATH, self.gian_stds)
        self._gian_std_render()
        self.gian_status.set("'{}' 표준 확정 — 재작성 창을 열면 자동으로 대조합니다".format(s["name"]))

    def gian_std_check(self, quiet=False, append=False):
        if not self.gian_win:
            if not quiet:
                messagebox.showinfo("기안 표준", "먼저 ② 재작성 창 열기(또는 열린 기안 창 읽기)를 하세요.")
            return
        ie, top = self.gian_win
        stds = self.gian_stds

        def work():
            try:
                res = gian_draft.check_open_window(ie, top, stds)
            except Exception as e:
                res = {"ok": False, "err": str(e)}
            res["quiet"], res["append"] = quiet, append
            self.ui_post("gian_check", res)
        threading.Thread(target=work, daemon=True).start()

    def gian_std_fix(self):
        if not self.gian_win:
            messagebox.showinfo("기안 표준", "먼저 ② 재작성 창 열기(또는 열린 기안 창 읽기)를 하세요.")
            return
        ie, top = self.gian_win

        def work():
            try:
                wm = gian_draft.read_window_meta(gian_draft.Gianki(ie, top))
                std = self.gian_stds.get(gian_draft.group_key(wm["title"]))
                if not std:
                    res = {"ok": False, "err": "이 기안 종류의 표준이 없습니다"}
                else:
                    res = gian_draft.fix_window(ie, top, std)
                    res.update(name=std["name"], confirmed=std.get("confirmed"), fixed=True)
            except Exception as e:
                res = {"ok": False, "err": str(e)}
            self.ui_post("gian_check", res)
        threading.Thread(target=work, daemon=True).start()

    def _show_gian_check(self, r):
        if not r.get("ok"):
            if not r.get("quiet"):
                self.gian_status.set("⚠ 표준 점검: " + str(r.get("err")))
            return
        bad = [x for x in r["findings"] if not x["ok"]]
        if r.get("quiet") and not bad and not r.get("append"):
            self.gian_status.set(self.gian_status.get() + "  ·  표준 점검 ✓")
            return
        info = [x for x in r["findings"] if x["ok"] and x["tip"]]
        head = "[{}] 표준 {} — {}: {}".format(
            r["at"], "맞추기 후 점검" if r.get("fixed") else "점검", r["name"],
            "모두 표준과 같음 ✓" if not bad else "다른 곳 {}개".format(len(bad)))
        lines = [head + ("" if r.get("confirmed") else "  (아직 '제안' 표준 — 확정 전)"), ""]
        for x in r["findings"]:
            if x["ok"] and not x["tip"]:
                lines.append("  ✓ {}: {}".format(x["field"], x["have"] if x["field"] != "첨부" else x["want"]))
        for x in bad:
            lines.append("  ⚠ {}: 지금 '{}' → 표준 '{}'{}{}".format(
                x["field"], x["have"], x["want"], "  [맞추기 가능]" if x["fix"] else "",
                ("  — " + x["tip"]) if x["tip"] else ""))
        for x in info:
            lines.append("  · {}: {} — {}".format(x["field"], x["have"], x["tip"]))
        fixable = any(x["fix"] for x in bad)
        summary = "표준 점검: " + ("✓ 이상 없음" if not bad else "⚠ {}곳 다름{}".format(
            len(bad), " — [공개·열람제한 맞추기] 가능" if fixable else ""))
        if r.get("append"):                      # 초안 적용 보고 아래에 덧붙임
            self.gian_box.insert("end", "\n\n" + "\n".join(lines))
            self.gian_status.set(self.gian_status.get() + "  ·  " + summary)
            return
        self.gian_box.delete("1.0", "end")
        self.gian_box.insert("1.0", "\n".join(lines))
        self.gian_status.set(summary)
        if bad and not r.get("fixed") and not r.get("quiet"):
            self.gian_nb.select(2)

    def gian_load(self):
        if not (HAS_WS and HAS_GIAN):
            messagebox.showerror("오류", "websocket-client와 comtypes가 필요합니다.")
            return
        self.gian_status.set("기안한문서 읽는 중…")

        def work():
            try:
                rows, st = gian_draft.read_drafted_list(self._port(), find_target_page, CDP)
            except Exception as e:
                rows, st = None, str(e)
            self.ui_post("gian_list", {"rows": rows, "status": st})
        threading.Thread(target=work, daemon=True).start()

    def _show_gian_list(self, p):
        self.gian_status.set(p["status"])
        if not p["rows"]:
            return
        self.gian_groups = gian_draft.group_rows(p["rows"])
        self.gian_gtree.delete(*self.gian_gtree.get_children())
        for i, g in enumerate(self.gian_groups):
            self.gian_gtree.insert("", "end", iid=str(i), values=(g["count"], g["sample"]))
        if self.gian_groups:
            self.gian_gtree.selection_set("0")

    def _gian_pick_group(self, _e=None):
        sel = self.gian_gtree.selection()
        if not sel:
            return
        g = self.gian_groups[int(sel[0])]
        vals = ["{}  {}  ({})".format(r["docno"], r["title"], r["done"][:10]) for r in g["rows"]]
        self.gian_src_cb["values"] = vals
        self.gian_src.set(vals[0] if vals else "")

    def gian_open(self, docno=None):
        if not (HAS_WS and HAS_GIAN):
            messagebox.showerror("오류", "websocket-client와 comtypes가 필요합니다.")
            return
        if docno is None:                              # [② 재작성 창 열기] 버튼: 왼쪽에서 고른 원본
            self._gian_case_ctx = None
            src = self.gian_src.get().strip()
            if not src:
                messagebox.showinfo("기안 도우미", "왼쪽에서 원본으로 쓸 기안을 먼저 고르세요.")
                return
            docno = src.split()[0]
        self.gian_status.set("{} 재작성 창 여는 중… (기안기가 뜰 때까지 10~30초)".format(docno))

        def work():
            try:
                before = {ie for _t, ie, _x in gian_draft.find_wxs_windows()}
                st = gian_draft.open_redraft(self._port(), docno, find_target_page, CDP)
                if st != "ok":
                    msg = {"noframe": "에듀파인 [기안한문서] 화면이 열려 있지 않습니다",
                           "notfound": "목록에서 {}를 찾지 못했습니다(다른 쪽에 있을 수 있음)".format(docno)}.get(st, st)
                    self.ui_post("gian_read", {"err": msg})
                    return
                win, notes = gian_draft.wait_new_gianki(before)
                for n in notes:
                    self.ui_post("log", "[{}] 기안 도우미: {}".format(datetime.now().strftime("%H:%M:%S"), n))
                if not win:
                    tip = next((n for n in notes if n.startswith("⚠")), "기안기 창이 뜨지 않았습니다 (WXSClient 확인)")
                    self.ui_post("gian_read", {"err": tip})
                    return
                data = gian_draft.read_gianki(*win)
                data["win"] = win
                self.ui_post("gian_read", data)
            except Exception as e:
                self.ui_post("gian_read", {"err": str(e)})
        threading.Thread(target=work, daemon=True).start()

    def gian_read_open(self):
        if not HAS_GIAN:
            messagebox.showerror("오류", "comtypes가 필요합니다 (pip install comtypes).")
            return

        def work():
            try:
                wins = gian_draft.find_wxs_windows()
                if not wins:
                    self.ui_post("gian_read", {"err": "열린 재작성 기안 창이 없습니다"})
                    return
                top, ie, _t = wins[-1]
                data = gian_draft.read_gianki(ie, top)
                data["win"] = (ie, top)
                self.ui_post("gian_read", data)
            except Exception as e:
                self.ui_post("gian_read", {"err": str(e)})
        threading.Thread(target=work, daemon=True).start()

    def _show_gian_read(self, p):
        ctx, self._gian_case_ctx = self._gian_case_ctx, None      # 사안 카드에서 연 창이면 값 미리 채움
        if p.get("err"):
            self.gian_status.set("⚠ " + p["err"])
            return
        self.gian_win = p["win"]
        self._gian_case_active = ctx["case"] if ctx else None
        self.gian_vars = [{"label": v["label"], "old": v["old"], "new": v["old"], "orig": v["old"]}
                          for v in p["vars"]]
        self._gian_orig_title = p["title"]
        self._gian_title_touched = False
        self._gian_render_vars()
        self.gian_status.set("기안 창 연결됨 — 바뀌는 값만 고치고 ③을 누르세요")
        self.gian_box.delete("1.0", "end")
        head = self._gian_case_prefill(ctx, p["text"]) if ctx else []
        self.gian_box.insert("1.0", ("\n".join(head) + "\n\n" if head else "") +
                             gian_draft.body_region(p["text"]).strip())
        if ctx:
            self.gian_nb.select(0)
            self.gian_status.set("{} '{}' 초안 창 — 노란 줄(미리 채운 값) 확인 후 ③ 초안에 적용".format(
                ctx["case"], ctx["name"]))
        self.log("[{}] 기안 도우미: '{}' 재작성 창 연결, 바뀔 후보 {}개".format(
            datetime.now().strftime("%H:%M:%S"), p["title"], len(self.gian_vars)))
        self._gian_att_load(p.get("atts", []), p.get("butim", []))
        if self.gian_stds:
            self.root.after(300, lambda: self.gian_std_check(quiet=True))   # 표준이 있으면 자동 대조

    def _gian_preview_title(self):
        """원본 제목에 표의 치환을 적용한 제목 (gian_draft.apply_changes와 같은 규칙)."""
        t = self._gian_orig_title
        pairs = sorted([(v["old"], v["new"]) for v in self.gian_vars if v["old"] != v["new"]],
                       key=lambda p: -len(p[0]))
        for i, (o, _n) in enumerate(pairs):
            t = t.replace(o, "@@T{}@@".format(i))
        for i, (_o, n) in enumerate(pairs):
            t = t.replace("@@T{}@@".format(i), n)
        return t

    def _gian_render_vars(self):
        keep = self.gian_vtree.selection()
        self.gian_vtree.delete(*self.gian_vtree.get_children())
        for i, v in enumerate(self.gian_vars):
            self.gian_vtree.insert("", "end", iid=str(i), values=(v["label"], v["old"], v["new"]),
                                   tags=("chg",) if v["new"] != v["old"] else ())
        if keep and keep[0] in self.gian_vtree.get_children():
            self.gian_vtree.selection_set(keep[0])
        # 새 제목: 사람이 직접 고치지 않았으면 치환 결과로 자동 갱신
        if not getattr(self, "_gian_title_touched", False) or self.gian_title.get() == getattr(self, "_gian_auto_title", None):
            self._gian_auto_title = self._gian_preview_title()
            self.gian_title.set(self._gian_auto_title)
            self._gian_title_touched = False

    def _gian_pick_var(self, _e=None):
        sel = self.gian_vtree.selection()
        if sel:
            self.gian_new.set(self.gian_vars[int(sel[0])]["new"])

    def _gian_set_new(self):
        sel = self.gian_vtree.selection()
        if not sel:
            return
        i = int(sel[0])
        self.gian_vars[i]["new"] = self.gian_new.get()
        if self.gian_title.get() != getattr(self, "_gian_auto_title", None):
            self._gian_title_touched = True
        self._gian_render_vars()
        nxt = str(i + 1)
        if nxt in self.gian_vtree.get_children():
            self.gian_vtree.selection_set(nxt)
            self.gian_vtree.see(nxt)

    def _gian_add_pair(self):
        old = simpledialog.askstring("행 추가", "기안문에서 바꿀 원본 글자 (본문에 있는 그대로):", parent=self.root)
        if not old:
            return
        new = simpledialog.askstring("행 추가", "'{}' 를 무엇으로 바꿀까요?".format(old), parent=self.root) or old
        self.gian_vars.append({"label": "직접", "old": old, "new": new, "orig": old})
        self._gian_render_vars()

    def _gian_del_pair(self):
        sel = self.gian_vtree.selection()
        if sel:
            del self.gian_vars[int(sel[0])]
            self._gian_render_vars()

    def gian_apply(self):
        if not self.gian_win:
            messagebox.showinfo("기안 도우미", "먼저 ② 재작성 창 열기(또는 열린 기안 창 읽기)를 하세요.")
            return
        pairs = [(v["old"], v["new"]) for v in self.gian_vars if v["new"] != v["old"]]
        title = self.gian_title.get().strip()
        if not pairs and title == self._gian_orig_title:
            messagebox.showinfo("기안 도우미", "바뀐 값이 없습니다. 오른쪽 표에서 새 값을 고친 뒤 적용하세요.")
            return
        empty = [o for o, n in pairs if not n.strip()]
        if empty and not messagebox.askyesno("확인", "빈 값으로 바꾸는 항목이 있습니다:\n{}\n그대로 진행할까요?".format("\n".join(empty))):
            return
        self.gian_status.set("기안 창에 적용 중…")
        ie, top = self.gian_win

        def work():
            try:
                res = gian_draft.apply_changes(ie, top, pairs, title or None)
            except Exception as e:
                res = {"ok": False, "err": str(e)}
            self.ui_post("gian_apply", res)
        threading.Thread(target=work, daemon=True).start()

    def _show_gian_apply(self, r):
        if not r.get("ok"):
            self.gian_status.set("⚠ 적용 실패: {} (기안 창이 닫혔으면 ②부터 다시)".format(r.get("err")))
            return
        if self._gian_case_active:                # 사안 카드에서 만든 초안: 고친 값(접수 일시 등)을 카드에 기억
            cv = self.gian_cases_saved.setdefault(self._gian_case_active, {"vars": {}}).setdefault("vars", {})
            for v in self.gian_vars:
                if v["new"] != v["old"] and v["new"].strip() and v["label"] not in gian_draft.CASE_LEARN_SKIP:
                    cv[v["label"]] = v["new"]
            self._gian_case_save()
        # 적용이 끝났으니 지금 상태를 새 원본으로 (같은 창에 또 고칠 때 이중 치환 방지)
        for v in self.gian_vars:
            v["old"] = v["new"]
        self._gian_orig_title = r["title"]
        self._gian_title_touched = False
        self._gian_render_vars()
        lines = ["[{}] 초안 적용 완료 — 제목: {}".format(r["at"], r["title"]), ""]
        for x in r["report"]:
            ok = x["now"] > 0 and not x["left"]
            lines.append("{} '{}' → '{}'   (원본 {}곳 → 지금 새 값 {}곳{})".format(
                "✓" if ok else "⚠", x["old"], x["new"], x["found"], x["now"],
                "" if not x["left"] else ", 원본 {}곳 남음".format(x["left"])))
        for w in r["warn"]:
            lines.append("⚠ " + (w if len(w) > 8 else "짧은 글자 '{}' 는 다른 곳까지 바뀌었을 수 있음".format(w)))
        lines.append("")
        lines.append("다음: [붙임 파일] 장에서 옛 사안 첨부를 새 파일로 바꿔 끼우기(④). 관련 학생 표·결재경로는 "
                     "기안 창에서 확인하고 [결재올림]은 직접 누르세요.")
        self.gian_box.delete("1.0", "end")
        self.gian_box.insert("1.0", "\n".join(lines))
        self.gian_status.set("적용 완료 — 기안 창을 앞으로 띄웠습니다. 확인 후 직접 결재올림")
        if self.gian_stds:                        # 바꾼 뒤 본문·사안번호·붙임을 표준과 다시 대조
            self.root.after(200, lambda: self.gian_std_check(quiet=True, append=True))
        self.log("[{}] 기안 도우미: 초안 적용 ({}건 치환) — {}".format(r["at"], len(r["report"]), r["title"]))

    # ===================== 마감 D-Day =====================
    def _build_dday(self):
        frm = self.tab_dday
        ttk.Label(frm, text="마감 D-Day", font=("Malgun Gothic", 13, "bold")).pack(anchor="w")
        ttk.Label(frm, foreground="#777", text="마감일을 등록하면 남은 일수를 보여주고, 설정의 '며칠 전'부터 아침 8:30에 알림.").pack(anchor="w", pady=(0, 8))
        self.dday_tree = ttk.Treeview(frm, columns=("name", "date", "dday"), show="headings", height=9)
        for c, t, w in [("name", "항목", 320), ("date", "마감일", 130), ("dday", "남음", 100)]:
            self.dday_tree.heading(c, text=t)
            self.dday_tree.column(c, width=w, anchor="w")
        self.dday_tree.pack(fill="both", expand=True, pady=6)
        ed = ttk.Frame(frm)
        ed.pack(fill="x")
        self.dday_name = tk.StringVar()
        self.dday_date = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(ed, textvariable=self.dday_name, width=28).pack(side="left")
        ttk.Entry(ed, textvariable=self.dday_date, width=12).pack(side="left", padx=6)
        ttk.Button(ed, text="추가", command=self.dday_add).pack(side="left", padx=3)
        ttk.Button(ed, text="선택 삭제", command=self.dday_del).pack(side="left", padx=3)
        self._reload_dday()

    def _reload_dday(self):
        self.dday_tree.delete(*self.dday_tree.get_children())
        items = []
        for it in self.cfg.get("deadlines", []):
            try:
                rem = (datetime.strptime(it.get("date", ""), "%Y-%m-%d").date() - date.today()).days
            except Exception:
                rem = 99999
            items.append((rem, it))
        for rem, it in sorted(items, key=lambda x: x[0]):
            label = "지남" if rem < 0 else ("오늘" if rem == 0 else "D-{}".format(rem))
            self.dday_tree.insert("", "end", values=(it.get("name", ""), it.get("date", ""), label))

    def dday_add(self):
        nm, ds = self.dday_name.get().strip(), self.dday_date.get().strip()
        try:
            datetime.strptime(ds, "%Y-%m-%d")
        except Exception:
            messagebox.showinfo("안내", "날짜는 YYYY-MM-DD 형식으로 입력하세요.")
            return
        if not nm:
            return
        self.cfg.setdefault("deadlines", []).append({"name": nm, "date": ds})
        save_config(self.cfg)
        self._reload_dday()
        self.dday_name.set("")

    def dday_del(self):
        sel = self.dday_tree.selection()
        if not sel:
            return
        vals = self.dday_tree.item(sel[0], "values")
        self.cfg["deadlines"] = [x for x in self.cfg.get("deadlines", [])
                                 if not (x.get("name") == vals[0] and x.get("date") == vals[1])]
        save_config(self.cfg)
        self._reload_dday()

    def _record_stat(self, p):
        today = date.today().isoformat()
        st = self.state.setdefault("stats", {}).setdefault(today, {})
        k = p.get("k")
        st[k] = st.get(k, 0) + int(p.get("n", 1))
        save_state(self.state)

    # ===================== 급식·학사일정 =====================
    def _build_meal(self):
        frm = self.tab_meal
        top = ttk.Frame(frm)
        top.pack(fill="x")
        ttk.Label(top, text="급식·학사일정", font=("Malgun Gothic", 13, "bold")).pack(side="left")
        ttk.Label(top, text="  (" + self.cfg.get("neis_open", {}).get("school", "") + ")", foreground="#777").pack(side="left")
        ttk.Button(top, text="새로고침", command=self.refresh_meal).pack(side="left", padx=10)
        self.meal_updated = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.meal_updated, foreground="#999").pack(side="left")

        lf0 = ttk.LabelFrame(frm, text="오늘 급식", padding=8)
        lf0.pack(fill="x", pady=(8, 6))
        self.meal_today = tk.Text(lf0, height=3, font=("Malgun Gothic", 11), wrap="word")
        self.meal_today.pack(fill="x")

        body = ttk.Frame(frm)
        body.pack(fill="both", expand=True)
        lf1 = ttk.LabelFrame(body, text="이번 주 급식", padding=8)
        lf1.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self.meal_week = tk.Text(lf1, font=("Malgun Gothic", 10))
        self.meal_week.pack(fill="both", expand=True)
        lf2 = ttk.LabelFrame(body, text="이번 달 학사일정", padding=8)
        lf2.pack(side="left", fill="both", expand=True, padx=(4, 0))
        ttk.Button(lf2, text="학사일정 → 마감 D-Day에 추가", command=self.import_schedule_to_dday).pack(anchor="w", pady=(0, 6))
        self.sched_box = tk.Text(lf2, font=("Malgun Gothic", 10))
        self.sched_box.pack(fill="both", expand=True)
        self._last_sched = []
        self.root.after(1200, self.refresh_meal)   # 켜지면 한 번 자동 로드

    def refresh_meal(self):
        nopen = self.cfg.get("neis_open", {})
        if not nopen.get("scode"):
            self.meal_today.delete("1.0", "end")
            self.meal_today.insert("1.0", "학교코드 미설정 — 설정 파일 neis_open을 확인하세요.")
            return
        self.meal_updated.set("· 불러오는 중…")
        today = date.today()
        wk_to = today + timedelta(days=6)
        m_from = today.replace(day=1)
        if m_from.month == 12:
            m_to = m_from.replace(day=31)
        else:
            m_to = m_from.replace(month=m_from.month + 1, day=1) - timedelta(days=1)

        def work():
            meals, ms = do_fetch_meal(nopen, today.strftime("%Y%m%d"), wk_to.strftime("%Y%m%d"))
            sch, ss = do_fetch_schedule(nopen, m_from.strftime("%Y%m%d"), m_to.strftime("%Y%m%d"))
            self.ui_post("meal", {"meals": meals, "ms": ms, "sch": sch, "ss": ss, "today": today.strftime("%Y%m%d")})
        threading.Thread(target=work, daemon=True).start()

    def _show_meal(self, p):
        today = p.get("today")
        meals, sch = p.get("meals"), p.get("sch")
        self.meal_today.delete("1.0", "end")
        if meals is None:
            self.meal_today.insert("1.0", "급식 조회 실패: {} (인터넷/학교코드 확인)".format(p.get("ms")))
        else:
            tm = [x for x in meals if x["date"] == today]
            if tm:
                self.meal_today.insert("1.0", " · ".join(x["dishes"].replace("\n", " · ") for x in tm))
            else:
                self.meal_today.insert("1.0", "오늘 급식 정보 없음 (주말/방학일 수 있음)")
        self.meal_week.delete("1.0", "end")
        if meals:
            for x in meals:
                d = x["date"]
                ds = "{}/{}".format(d[4:6], d[6:8]) if len(d) == 8 else d
                self.meal_week.insert("end", "[{} {}]\n{}\n\n".format(ds, x["meal"], x["dishes"]))
        self.sched_box.delete("1.0", "end")
        if sch is None:
            self.sched_box.insert("1.0", "학사일정 조회 실패: {}".format(p.get("ss")))
        elif not sch:
            self.sched_box.insert("1.0", "이번 달 학사일정 없음")
        else:
            for x in sch:
                d = x["date"]
                ds = "{}/{}".format(d[4:6], d[6:8]) if len(d) == 8 else d
                self.sched_box.insert("end", "{}  {}\n".format(ds, x["event"]))
        self._last_sched = sch or []
        self.meal_updated.set("· 갱신 " + datetime.now().strftime("%H:%M:%S"))

    def import_schedule_to_dday(self):
        sch = getattr(self, "_last_sched", [])
        if not sch:
            messagebox.showinfo("안내", "먼저 [새로고침]으로 학사일정을 불러오세요.")
            return
        today = date.today().isoformat()
        exist = {(d.get("name"), d.get("date")) for d in self.cfg.get("deadlines", [])}
        added = 0
        for x in sch:
            d = x["date"]
            if len(d) != 8:
                continue
            iso = "{}-{}-{}".format(d[0:4], d[4:6], d[6:8])
            if iso < today:
                continue
            key = (x["event"], iso)
            if key in exist:
                continue
            self.cfg.setdefault("deadlines", []).append({"name": x["event"], "date": iso})
            exist.add(key)
            added += 1
        if added:
            save_config(self.cfg)
            try:
                self._reload_dday()
            except Exception:
                pass
            messagebox.showinfo("완료", "학사일정 {}건을 마감 D-Day에 추가했습니다.".format(added))
        else:
            messagebox.showinfo("안내", "추가할 새 일정이 없습니다 (이미 있거나 지난 일정).")

    # ===================== 알림장 (전달사항 읽기) =====================
    def _build_alim(self):
        frm = self.tab_alim
        top = ttk.Frame(frm)
        top.pack(fill="x")
        ttk.Label(top, text="알림장 (전달사항)", font=("Malgun Gothic", 13, "bold")).pack(side="left")
        ttk.Label(top, text="일자").pack(side="left", padx=(12, 2))
        self.alim_date = tk.StringVar(value=datetime.now().strftime("%Y%m%d"))
        ttk.Entry(top, textvariable=self.alim_date, width=10).pack(side="left")
        ttk.Button(top, text="◀", width=2, command=lambda: self._alim_shift(-1)).pack(side="left", padx=(4, 0))
        ttk.Button(top, text="▶", width=2, command=lambda: self._alim_shift(1)).pack(side="left", padx=(0, 2))
        ttk.Button(top, text="불러오기", command=self.refresh_alim).pack(side="left", padx=8)
        ttk.Button(top, text="알림장 앱 열기", command=self.open_alim).pack(side="left", padx=3)
        ttk.Button(top, text="본문 복사", command=lambda: self.copy_widget(self.alim_box)).pack(side="left", padx=3)
        self.alim_updated = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.alim_updated, foreground="#777").pack(side="left", padx=8)
        ttk.Label(frm, foreground="#888", justify="left", wraplength=940, text=(
            "일자를 고르고 [불러오기]를 누르면 학교 알림장 서버에서 그날 전달사항을 «앱을 띄우지 않고» 바로 읽어옵니다. "
            "(알림장 앱이 쓰는 것과 같은 조회 — 학교망 안 또는 EVPN 연결에서만 됩니다.)\n"
            "서버에 닿지 못하면, 실행 중인 알림장 앱 창을 읽어오는 예전 방식으로 자동 전환합니다.")
        ).pack(anchor="w", pady=(6, 4))
        self.alim_box = tk.Text(frm, height=22, font=("Malgun Gothic", 10), wrap="word")
        self.alim_box.pack(fill="both", expand=True)

    def _alim_shift(self, days):
        try:
            d = datetime.strptime(self.alim_date.get().strip(), "%Y%m%d") + timedelta(days=days)
            self.alim_date.set(d.strftime("%Y%m%d"))
        except Exception:
            self.alim_date.set(datetime.now().strftime("%Y%m%d"))

    def refresh_alim(self):
        date8 = self.alim_date.get().strip()
        if not (date8.isdigit() and len(date8) == 8):
            messagebox.showinfo("안내", "일자를 YYYYMMDD 8자리로 입력하세요. (예: 20260921)"); return
        self.alim_box.delete("1.0", "end")
        self.alim_box.insert("1.0", "읽는 중…")
        db = dict(self.cfg.get("alim_db", {}))

        def work():
            # 1순위: 학교 서버에서 직접(앱 없이). 연결 실패 시 실행 중인 앱 창 읽기로 폴백.
            if db.get("enabled", True) and HAS_PYMYSQL:
                text, status = read_alim_from_db(date8, db)
                if status == "ok":
                    self.ui_post("alim", {"text": text, "status": "ok", "src": "db", "date": date8}); return
                if status == "빈날":
                    self.ui_post("alim", {"text": "", "status": "빈날", "src": "db", "date": date8}); return
                # 연결 실패 등 → 창 읽기로 폴백
                wtext, wstatus = read_alim_notice()
                if wstatus == "ok":
                    self.ui_post("alim", {"text": wtext, "status": "ok", "src": "win", "date": date8, "db_why": status}); return
                self.ui_post("alim", {"text": None, "status": wstatus, "src": "win", "date": date8, "db_why": status}); return
            # DB 비활성/미설치 → 창 읽기만
            wtext, wstatus = read_alim_notice()
            self.ui_post("alim", {"text": wtext, "status": wstatus, "src": "win", "date": date8})
        threading.Thread(target=work, daemon=True).start()

    def _show_alim(self, payload):
        self.alim_box.delete("1.0", "end")
        text, status = payload.get("text"), payload.get("status")
        src = payload.get("src", "")
        date8 = payload.get("date", "")
        src_label = "서버 직접" if src == "db" else "앱 창"
        now = datetime.now().strftime("%H:%M:%S")

        if status == "ok" and text:
            self.alim_box.insert("1.0", text)
            tail = "" if src == "db" else "  (서버에 못 닿아 앱 창에서 읽음)"
            self.alim_updated.set("· {} {} · {}{}".format(date8, src_label, now, tail))
            self.log("[{}] 알림장 전달사항 불러옴 — {} {} ({}자)".format(now, date8, src_label, len(text)))
            return
        if status == "빈날":
            self.alim_box.insert("1.0", "이 날({}) 전달사항이 없습니다. (서버 연결은 정상)".format(date8))
            self.alim_updated.set("· {} 서버 직접 · {}".format(date8, now))
            return
        msg = {
            "실행안됨": "서버에 닿지 못했고, 알림장(ALIM.EXE)도 실행돼 있지 않습니다.\n"
                     "학교망(또는 EVPN) 연결을 확인하거나, [알림장 앱 열기]로 앱을 띄운 뒤 다시 시도하세요.",
            "본문 없음": "앱 창에서 본문을 찾지 못했습니다. 알림장에서 날짜를 눌러 전달사항이 보이게 한 뒤 다시 시도하세요.",
            "윈도우 전용": "이 기능은 윈도우에서만 동작합니다.",
            "pymysql 없음 - requirements.txt 설치 필요": "pymysql 이 설치돼 있지 않습니다. 콘솔_실행.bat 을 다시 실행하면 자동 설치됩니다.",
        }.get(status, "읽기 실패: {}".format(status))
        if payload.get("db_why"):
            msg += "\n\n(서버 직접 읽기 실패: {})".format(payload["db_why"])
        self.alim_box.insert("1.0", msg)
        self.alim_updated.set("")

    def open_alim(self):
        exe = self.cfg.get("paths", {}).get("alim_exe", "").strip()
        if not exe or not os.path.exists(exe):
            messagebox.showerror("오류", "알림장 실행파일을 찾지 못했습니다.\n설정에서 'alim_exe' 경로를 확인하세요.\n(현재: {})".format(exe or "미설정"))
            return
        try:
            # ALIM.EXE는 '시작 위치(자기 폴더)' 기준으로 버전/설정 파일을 찾는다.
            # os.startfile은 콘솔의 현재 폴더를 물려줘 업데이트 창이 뜨므로,
            # 바탕화면 바로가기처럼 cwd를 실행파일 폴더로 맞춰 실행한다.
            if exe.lower().endswith(".lnk"):
                os.startfile(exe)  # noqa  (바로가기는 자체 시작위치 사용)
            else:
                subprocess.Popen([exe], cwd=os.path.dirname(exe) or None)
            self.log("알림장 앱 실행: {}".format(exe))
        except Exception as e:
            messagebox.showerror("오류", "알림장 실행 실패\n{}".format(e))

    def _mk_action_btn(self, parent, text, command, bg, active):
        """상단 빠른 실행용 색상 버튼 (tk.Button)."""
        return tk.Button(parent, text=text, command=command,
                         relief="flat", bd=0, cursor="hand2",
                         font=("Malgun Gothic", 9, "bold"),
                         bg=bg, fg="white", activebackground=active,
                         activeforeground="white", padx=12, pady=6)

    def _select_tab(self, frame):
        """색상 탭바에서 frame을 활성화(선택 탭 진하게 + 해당 화면 표시)."""
        for f, b in self._tab_btns.items():
            solid, pale = self._tab_meta[f]
            if f is frame:
                b.configure(bg=solid, fg="white")
                f.pack(fill="both", expand=True)
            else:
                b.configure(bg=pale, fg="#333")
                f.pack_forget()

    def _build_naeis_group(self):
        """'나이스 업무' 상위 탭 안에 하위 탭(복무·기안 템플릿 / 출결 마감) 구성."""
        parent = self.tab_naeis
        subbar = ttk.Frame(parent, padding=(12, 10, 12, 2))
        subbar.pack(fill="x")
        subcon = ttk.Frame(parent)
        subcon.pack(fill="both", expand=True, padx=12, pady=(2, 10))
        self.tab_tpl = ttk.Frame(subcon, padding=(0, 8, 0, 0))
        self.tab_att = ttk.Frame(subcon, padding=(0, 8, 0, 0))
        subdefs = [
            ("복무·기안 템플릿", self.tab_tpl, "#7b5ea7", "#ece4f4"),
            ("출결 마감",        self.tab_att, "#c0504d", "#f4e2e1"),
        ]
        self._sub_btns = {}
        self._sub_meta = {}
        for text, frame, solid, pale in subdefs:
            b = tk.Button(subbar, text="›  " + text, relief="flat", bd=0, cursor="hand2",
                          font=("Malgun Gothic", 10, "bold"),
                          bg=pale, fg="#333", activebackground=solid,
                          activeforeground="white", padx=16, pady=6,
                          command=lambda f=frame: self._select_subtab(f))
            b.pack(side="left", padx=(0, 4))
            self._sub_btns[frame] = b
            self._sub_meta[frame] = (solid, pale)

    def _select_subtab(self, frame):
        """'나이스 업무' 내부 하위 탭 전환."""
        for f, b in self._sub_btns.items():
            solid, pale = self._sub_meta[f]
            if f is frame:
                b.configure(bg=solid, fg="white")
                f.pack(fill="both", expand=True)
            else:
                b.configure(bg=pale, fg="#333")
                f.pack_forget()

    def _build_dashboard(self, frm):
        """세션 유지 + 결재 관제 (컴팩트 표) — '오늘' 탭 아래쪽에 붙는다."""

        # ===== 세션 유지 =====
        lf_s = ttk.LabelFrame(frm, text="세션 유지 (자동 로그아웃 방지)", padding=8)
        lf_s.pack(fill="x", pady=(0, 8))
        c1 = ttk.Frame(lf_s)
        c1.pack(fill="x")
        self.keep_btn = ttk.Button(c1, text="세션 유지 시작", command=self.toggle_keeper)
        self.keep_btn.pack(side="left", padx=3)
        ttk.Button(c1, text="지금 갱신", command=self.refresh_now).pack(side="left", padx=3)
        self.keep_state_var = tk.StringVar(value="세션 유지: 중지")
        ttk.Label(c1, textvariable=self.keep_state_var).pack(side="left", padx=12)
        self.next_var = tk.StringVar(value="")
        ttk.Label(c1, textvariable=self.next_var, foreground="#555").pack(side="right")
        self.tree = ttk.Treeview(lf_s, columns=("site", "state", "last", "detail"), show="headings", height=3)
        for c, txt, w in [("site", "사이트", 150), ("state", "상태", 70),
                          ("last", "마지막 갱신", 100), ("detail", "내용", 430)]:
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="w")
        self.tree.pack(fill="x", pady=(6, 0))
        for t in self.cfg["keepalive_targets"]:
            iid = self.tree.insert("", "end", values=(t["name"], "-", "-", "아직 갱신 안 함"))
            self.status_rows[t["name"]] = iid

        # ===== 결재 관제 =====
        lf_m = ttk.LabelFrame(frm, text="결재 관제 (에듀파인·나이스·E알리미 건수 감지 → 알림)", padding=8)
        lf_m.pack(fill="x", pady=(0, 8))
        c2 = ttk.Frame(lf_m)
        c2.pack(fill="x")
        self.mon_btn = ttk.Button(c2, text="관제 시작", command=self.toggle_monitor)
        self.mon_btn.pack(side="left", padx=3)
        ttk.Button(c2, text="지금 확인", command=self.monitor_now).pack(side="left", padx=3)
        ttk.Button(c2, text="알림 테스트", command=lambda: toast("학교업무 콘솔", "알림이 정상 동작합니다.")).pack(side="left", padx=3)
        self.mon_state_var = tk.StringVar(value="관제: 중지")
        ttk.Label(c2, textvariable=self.mon_state_var).pack(side="left", padx=12)
        self.mon_tree = ttk.Treeview(lf_m, columns=("src", "label", "value", "delta", "last"), show="headings", height=4)
        for c, txt, w in [("src", "출처", 190), ("label", "항목", 130), ("value", "현재", 70),
                          ("delta", "변화", 70), ("last", "마지막 확인", 250)]:
            self.mon_tree.heading(c, text=txt)
            self.mon_tree.column(c, width=w, anchor="w")
        self.mon_tree.pack(fill="x", pady=(6, 0))

        ttk.Label(frm, foreground="#888", text=(
            "· ① 로그인 자동화 실행(디버그 크롬 뜨고 로그인) → 세션 유지 시작 / 관제 시작.")
        ).pack(anchor="w")

    def _build_query(self):
        frm = self.tab_query
        ttk.Label(frm, justify="left", foreground="#37597a", wraplength=940, text=(
            "조회 전용(읽기): 열려 있는 결재/문서 '목록 화면'을 읽어 표로 보여주고, 선택 항목을 클릭해 엽니다. "
            "나이스 '출결 현황' 화면에서는 미마감 학급을 점검합니다. 입력·제출은 하지 않습니다.")
        ).pack(anchor="w", pady=(0, 8))

        # --- 결재·문서 목록 ---
        lf = ttk.LabelFrame(frm, text="결재·문서 목록", padding=8)
        lf.pack(fill="both", expand=True)
        ctl = ttk.Frame(lf)
        ctl.pack(fill="x")
        ttk.Label(ctl, text="대상").pack(side="left")
        srcs = [s["name"] for s in self.cfg.get("doclist_sources", [])] or ["K-에듀파인"]
        self.query_target = tk.StringVar(value=srcs[0])
        ttk.Combobox(ctl, textvariable=self.query_target, state="readonly", width=16, values=srcs).pack(side="left", padx=6)
        ttk.Button(ctl, text="목록 불러오기", command=self.query_load).pack(side="left", padx=3)
        ttk.Button(ctl, text="선택 항목 열기", command=self.query_open).pack(side="left", padx=3)
        cols = ("title", "sub", "date")
        self.q_tree = ttk.Treeview(lf, columns=cols, show="headings", height=8)
        for c, txt, w in [("title", "제목", 470), ("sub", "기안자·상태", 150), ("date", "일시", 120)]:
            self.q_tree.heading(c, text=txt)
            self.q_tree.column(c, width=w, anchor="w")
        self.q_tree.pack(fill="both", expand=True, pady=8)
        self.q_tree.bind("<Double-1>", lambda e: self.query_open())
        self.doclist_rows = {}   # iid → (match, x, y)

        # --- 출결 미마감 점검 ---
        lf2 = ttk.LabelFrame(frm, text="출결 미마감 점검 (나이스 '출결 현황' 화면에서)", padding=8)
        lf2.pack(fill="x", pady=(10, 0))
        ctl2 = ttk.Frame(lf2)
        ctl2.pack(fill="x")
        ttk.Button(ctl2, text="출결 점검", command=self.attendance_check).pack(side="left", padx=3)
        ttk.Label(ctl2, text="마감 기준시각").pack(side="left", padx=(10, 2))
        self.att_deadline = tk.StringVar(value=self.cfg.get("attendance", {}).get("deadline", "15:00"))
        ttk.Entry(ctl2, textvariable=self.att_deadline, width=8).pack(side="left")
        self.att_result = tk.StringVar(value="아직 점검 안 함")
        ttk.Label(lf2, textvariable=self.att_result, justify="left", wraplength=940).pack(anchor="w", pady=(8, 0))

        # --- 수행평가 누락 확인 (반별 누적) ---
        lf3 = ttk.LabelFrame(frm, text="수행평가 누락 확인   🚧 개발 중 (미완성)", padding=8)
        lf3.pack(fill="both", expand=True, pady=(10, 0))
        ttk.Label(lf3, foreground="#c0392b", font=("Malgun Gothic", 10, "bold"), text=(
            "🚧 현재 개발 중인 기능입니다 (미완성) — 강의실은 나이스에서 직접 바꿔가며 사용하세요.")
        ).pack(anchor="w", pady=(0, 4))
        ttk.Label(lf3, foreground="#555", justify="left", wraplength=940, text=(
            "① 나이스에서 강의실(반)을 바꾸고 반드시 [조회]  →  ② 콘솔에서 반 번호 넣고 [현재 반 추가].\n"
            "콘솔은 '지금 나이스 화면'을 그대로 읽을 뿐 강의실을 바꾸지 않아요. 매 반마다 나이스에서 직접 조회하세요.\n"
            "(읽기만 — 안전.  ※ 결시·학적변동 제외.  직전과 값이 같으면 화면 안 바꾼 걸로 보고 경고합니다.)")
        ).pack(anchor="w")
        prow = ttk.Frame(lf3); prow.pack(anchor="w", pady=6)
        ttk.Label(prow, text="반").pack(side="left")
        self.perf_ban = tk.StringVar()
        ttk.Entry(prow, textvariable=self.perf_ban, width=4).pack(side="left", padx=(2, 8))
        ttk.Button(prow, text="현재 반 추가", command=self.perf_check).pack(side="left", padx=(0, 4))
        ttk.Button(prow, text="전체 초기화", command=self.perf_reset).pack(side="left")
        ttk.Label(prow, text="(반 칸에 번호 넣고 추가하면 자동으로 다음 번호로 넘어가요)",
                  foreground="#888").pack(side="left", padx=8)
        self.perf_box = tk.Text(lf3, height=10, font=("Malgun Gothic", 10))
        self.perf_box.pack(fill="both", expand=True)
        self.perf_seq = 0
        self._perf_last_sig = None

    def _build_template(self):
        frm = self.tab_tpl
        # 복무 사유
        lf1 = ttk.LabelFrame(frm, text="복무 사유 문구", padding=10)
        lf1.pack(fill="x", pady=(0, 10))
        self.leave_type = tk.StringVar(value="연가")
        ttk.Label(lf1, text="종류").grid(row=0, column=0, sticky="w")
        ttk.Combobox(lf1, textvariable=self.leave_type, state="readonly", width=12,
                     values=list(self.cfg["leave_reason_presets"].keys())
                     ).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Button(lf1, text="문구 생성", command=self.gen_leave).grid(row=0, column=2, padx=4)
        ttk.Button(lf1, text="복사", command=lambda: self.copy_widget(self.leave_box)
                   ).grid(row=0, column=3, padx=4)
        self.leave_box = tk.Text(lf1, height=4, width=90)
        self.leave_box.grid(row=1, column=0, columnspan=4, sticky="we", pady=(8, 0))
        lf1.columnconfigure(3, weight=1)

        # 기안 문구
        lf2 = ttk.LabelFrame(frm, text="기안 제목·붙임 문구", padding=10)
        lf2.pack(fill="both", expand=True)
        self.doc_subject = tk.StringVar()
        self.doc_purpose = tk.StringVar()
        self.doc_attach = tk.StringVar(value="붙임자료")
        ttk.Label(lf2, text="주제").grid(row=0, column=0, sticky="w")
        ttk.Entry(lf2, textvariable=self.doc_subject, width=24).grid(row=0, column=1, padx=6, pady=3)
        ttk.Label(lf2, text="행위/목적").grid(row=0, column=2, sticky="w")
        ttk.Entry(lf2, textvariable=self.doc_purpose, width=24).grid(row=0, column=3, padx=6)
        ttk.Label(lf2, text="붙임 이름").grid(row=1, column=0, sticky="w")
        ttk.Entry(lf2, textvariable=self.doc_attach, width=24).grid(row=1, column=1, padx=6, pady=3)
        ttk.Button(lf2, text="초안 생성", command=self.gen_doc).grid(row=1, column=2, padx=4)
        ttk.Button(lf2, text="복사", command=lambda: self.copy_widget(self.doc_box)).grid(row=1, column=3, sticky="w")
        self.doc_box = tk.Text(lf2, height=10, width=90)
        self.doc_box.grid(row=2, column=0, columnspan=4, sticky="nsew", pady=(8, 0))
        lf2.columnconfigure(3, weight=1)
        lf2.rowconfigure(2, weight=1)

    # ---------- 출결 마감 ----------
    GYOSI_CHOICES = ["", "조회", "1교시", "2교시", "3교시", "4교시",
                     "5교시", "6교시", "7교시", "8교시", "종례"]

    def _build_attendance(self):
        frm = self.tab_att
        self.att_exceptions = {}   # no(str) -> (구분, 종류, 교시, 사유)
        self.att_cols = []

        ttk.Label(frm, foreground="#b35c00", justify="left", wraplength=940,
                  font=("Malgun Gothic", 11, "bold"), text=(
            "나이스에 실제로 입력합니다. 저장은 따로 물어보고, 마감(확정)은 누르지 않습니다.")
        ).pack(anchor="w", pady=(0, 2))
        ttk.Label(frm, foreground="#666", justify="left", wraplength=940, text=(
            "나이스 '학급담임 → 출결관리 → 일일출결관리(담임용)' 화면을 띄워 두세요.\n"
            "[명단 불러오기]는 날짜를 넣고 조회한 뒤 번호·성명과 «현재 출결 상태»까지 읽어옵니다. "
            "예외로 지정한 학생만 입력되고, 나머지는 손대지 않습니다.\n"
            "조퇴·지각은 교시를 반드시 고르세요 — 교시가 빈 줄이 하나라도 있으면 "
            "나이스가 그날 저장을 통째로 거부해서 다른 학생까지 안 들어갑니다."
        )).pack(anchor="w", pady=(0, 8))

        ctl = ttk.Frame(frm); ctl.pack(fill="x")
        ttk.Label(ctl, text="일자(YYYYMMDD)").pack(side="left")
        self.att_date = tk.StringVar(value=datetime.now().strftime("%Y%m%d"))
        ttk.Entry(ctl, textvariable=self.att_date, width=12).pack(side="left", padx=6)
        ttk.Button(ctl, text="명단 불러오기", command=self.attendance_load).pack(side="left", padx=3)
        ttk.Button(ctl, text="화면 진단", command=self.attendance_diag).pack(side="left", padx=3)
        self.att_load_status = tk.StringVar(value="")
        ttk.Label(ctl, textvariable=self.att_load_status, foreground="#555").pack(side="left", padx=10)

        cols = ("no", "name", "now", "state", "reason")
        self.att_tree = ttk.Treeview(frm, columns=cols, show="headings", height=10)
        for c, t, w, a in (("no", "번호", 55, "center"), ("name", "성명", 120, "w"),
                           ("now", "나이스 현재", 200, "w"),
                           ("state", "넣을 내용", 150, "center"), ("reason", "사유", 260, "w")):
            self.att_tree.heading(c, text=t)
            self.att_tree.column(c, width=w, anchor=a)
        self.att_tree.pack(fill="both", expand=True, pady=(8, 6))

        ex = ttk.LabelFrame(frm, text="예외 지정 (문제 있는 학생만 — 지정 안 한 학생은 건드리지 않음)", padding=8)
        ex.pack(fill="x")
        ttk.Label(ex, text="번호").grid(row=0, column=0)
        self.ex_no = tk.StringVar()
        ttk.Entry(ex, textvariable=self.ex_no, width=6).grid(row=0, column=1, padx=4)
        ttk.Label(ex, text="구분").grid(row=0, column=2)
        self.ex_gubun = tk.StringVar(value="질병")
        ttk.Combobox(ex, textvariable=self.ex_gubun, state="readonly", width=8,
                     values=["질병", "미인정", "기타", "출석인정"]).grid(row=0, column=3, padx=4)
        ttk.Label(ex, text="종류").grid(row=0, column=4)
        self.ex_jong = tk.StringVar(value="결석")
        ttk.Combobox(ex, textvariable=self.ex_jong, state="readonly", width=8,
                     values=["지각", "조퇴", "결석", "결과"]).grid(row=0, column=5, padx=4)
        ttk.Label(ex, text="교시").grid(row=0, column=6)
        self.ex_gyosi = tk.StringVar(value="")
        ttk.Combobox(ex, textvariable=self.ex_gyosi, state="readonly", width=7,
                     values=self.GYOSI_CHOICES).grid(row=0, column=7, padx=4)
        ttk.Label(ex, text="사유").grid(row=0, column=8)
        self.ex_reason = tk.StringVar()
        ttk.Entry(ex, textvariable=self.ex_reason, width=20).grid(row=0, column=9, padx=4)
        ttk.Button(ex, text="예외 지정", command=self.attendance_set_exception).grid(row=0, column=10, padx=6)
        ttk.Button(ex, text="예외 해제", command=self.attendance_clear_exception).grid(row=0, column=11, padx=2)
        ttk.Label(ex, foreground="#777", text="※ 조퇴·지각만 교시가 필요합니다 (결석·결과는 비워두세요)").grid(
            row=1, column=0, columnspan=12, sticky="w", pady=(4, 0))
        # 표에서 행 클릭 시 번호 자동 입력
        self.att_tree.bind("<<TreeviewSelect>>", self._att_pick_row)

        btm = ttk.Frame(frm); btm.pack(fill="x", pady=(8, 0))
        ttk.Button(btm, text="미리보기", command=self.attendance_preview).pack(side="left", padx=3)
        self.att_apply_btn = ttk.Button(btm, text="나이스에 입력", command=self.attendance_apply)
        self.att_apply_btn.pack(side="left", padx=3)
        ttk.Label(btm, text="※ 마감(확정)은 나이스에서 직접 '출결마감' → '확인' 하세요.",
                  foreground="#a33").pack(side="left", padx=10)

    def _att_pick_row(self, _evt=None):
        sel = self.att_tree.selection()
        if sel:
            self.ex_no.set(self.att_tree.item(sel[0], "values")[0])

    def attendance_load(self):
        if not HAS_WS:
            messagebox.showerror("오류", "websocket-client가 필요합니다."); return
        date8 = self.att_date.get().strip()
        if not (date8.isdigit() and len(date8) == 8):
            messagebox.showinfo("안내", "일자를 YYYYMMDD 8자리로 입력하세요. (예: 20260921)"); return
        self.att_load_status.set("날짜 조회 → 명단 불러오는 중…")
        self.att_tree.delete(*self.att_tree.get_children())
        match = self.cfg.get("attendance", {}).get("match", "sen.neis.go.kr")

        def work():
            got, status = do_load_roster_by_date(self._port(), date8, match)
            self.ui_post("roster", {"got": got, "status": status})
        threading.Thread(target=work, daemon=True).start()

    def attendance_diag(self):
        """지금 떠 있는 화면을 읽기만 해서 콘솔이 뭘 보고 있는지 보여준다(변경 없음)."""
        if not HAS_WS:
            messagebox.showerror("오류", "websocket-client가 필요합니다."); return
        match = self.cfg.get("attendance", {}).get("match", "sen.neis.go.kr")

        def work():
            got, status = do_read_roster(self._port(), match)
            self.ui_post("attdiag", {"got": got, "status": status})
        threading.Thread(target=work, daemon=True).start()

    def _show_attdiag(self, payload):
        got, status = payload.get("got"), payload.get("status", "")
        if not got:
            messagebox.showwarning("화면 진단", "화면을 읽지 못했습니다.\n\n{}".format(status)); return
        cols = got.get("cols") or []
        rows = got.get("rows") or []
        lines = ["열 {}개: {}".format(len(cols), ", ".join(cols)),
                 "학생 행 {}개".format(len(rows)),
                 "행 보정값(off): {}".format(sorted(set(r.get("off") for r in rows)) or "-"),
                 ""]
        for r in rows[:8]:
            lines.append(" {}번 {} | 마감:{} | 사유:{} | {}".format(
                r.get("no"), r.get("name"), r.get("magam") or "-",
                r.get("reason") or "-", r.get("marks") or "-"))
        if len(rows) > 8:
            lines.append(" … 외 {}명".format(len(rows) - 8))
        messagebox.showinfo("화면 진단 (읽기만 함)", "\n".join(lines))

    def _show_roster(self, payload):
        got = payload.get("got") or {}
        status = payload.get("status", "")
        rows = got.get("rows") or []
        self.att_cols = got.get("cols") or []
        self.att_exceptions = {}
        self.att_tree.delete(*self.att_tree.get_children())
        for r in rows:
            iid = str(r.get("no"))
            if self.att_tree.exists(iid):
                iid = iid + "_" + (r.get("name") or "")
            now = r.get("magam") or ""
            if r.get("marks"):
                now = (now + " / " if now else "") + r["marks"]
            self.att_tree.insert("", "end", iid=iid,
                                 values=(r.get("no"), r.get("name"), now or "출석", "", r.get("reason") or ""))
        if rows:
            gyosi = [c for c in self.att_cols if c.endswith("교시")]
            self.att_load_status.set("명단 {}명 · 이 날 {}".format(
                len(rows), "~".join([gyosi[0], gyosi[-1]]) if gyosi else "교시 정보 없음"))
        else:
            self.att_load_status.set(
                "명단을 못 읽음 — '일일출결관리(담임용)' 화면인지 확인 ({})".format(status))

    def _att_gyosi_num(self, label):
        """교시 콤보 값 → 숫자(조회=0). 비었으면 None."""
        label = (label or "").strip()
        if not label:
            return None
        if label == "조회":
            return 0
        if label.endswith("교시") and label[:-2].isdigit():
            return int(label[:-2])
        return None

    def attendance_set_exception(self):
        no = self.ex_no.get().strip()
        if not no or not self.att_tree.exists(no):
            messagebox.showinfo("안내", "먼저 명단을 불러오고, 표에 있는 번호를 입력(또는 행 클릭)하세요."); return
        g, j = self.ex_gubun.get(), self.ex_jong.get()
        gy, r = self.ex_gyosi.get().strip(), self.ex_reason.get().strip()
        if j in ("조퇴", "지각") and not gy:
            messagebox.showwarning("교시가 필요합니다",
                "{}는 교시를 골라야 합니다.\n\n"
                "교시가 빈 줄이 하나라도 있으면 나이스가 그날 저장을 통째로 거부해서,\n"
                "같은 날 다른 학생까지 안 들어갑니다.".format(j)); return
        if j not in ("조퇴", "지각") and gy:
            gy = ""   # 결석·결과에는 교시가 없다
        if gy and self.att_cols and gy not in self.att_cols:
            have = [c for c in self.att_cols if c.endswith("교시")]
            messagebox.showwarning("없는 교시",
                "이 날 화면에는 '{}' 칸이 없습니다.\n있는 교시: {}".format(
                    gy, ", ".join(have) or "없음")); return
        self.att_exceptions[no] = (g, j, gy, r)
        v = self.att_tree.item(no, "values")
        self.att_tree.item(no, values=(v[0], v[1], v[2], g + j + (" " + gy if gy else ""), r))

    def attendance_clear_exception(self):
        no = self.ex_no.get().strip()
        self.att_exceptions.pop(no, None)
        if self.att_tree.exists(no):
            v = self.att_tree.item(no, "values")
            self.att_tree.item(no, values=(v[0], v[1], v[2], "", v[4]))

    def _att_tasks(self):
        """예외 → 입력 작업 목록."""
        out = []
        for no, (g, j, gy, r) in self.att_exceptions.items():
            if not self.att_tree.exists(no):
                continue
            out.append({"no": no, "name": self.att_tree.item(no, "values")[1],
                        "gubun": g, "jongryu": j,
                        "gyosi": self._att_gyosi_num(gy), "reason": r})
        return out

    def attendance_preview(self):
        kids = self.att_tree.get_children()
        if not kids:
            messagebox.showinfo("미리보기", "먼저 명단을 불러오세요."); return
        tasks = self._att_tasks()
        lines = ["일자: {}".format(self.att_date.get().strip()),
                 "총 {}명 중 {}명만 입력합니다 (나머지는 건드리지 않음)".format(len(kids), len(tasks)), ""]
        for t in tasks:
            gy = "" if t["gyosi"] is None else (" 조회" if t["gyosi"] == 0 else " {}교시".format(t["gyosi"]))
            lines.append(" · {}번 {} : {}{}{} ({})".format(
                t["no"], t["name"], t["gubun"], t["jongryu"], gy, t["reason"] or "사유없음"))
        messagebox.showinfo("미리보기", "\n".join(lines))

    def attendance_apply(self):
        """예외를 나이스 화면에 «입력»한다. 저장은 끝난 뒤 따로 물어본다."""
        if not HAS_WS:
            messagebox.showerror("오류", "websocket-client가 필요합니다."); return
        if not self.att_tree.get_children():
            messagebox.showinfo("안내", "먼저 명단을 불러오세요."); return
        tasks = self._att_tasks()
        if not tasks:
            messagebox.showinfo("안내", "입력할 예외가 없습니다. 먼저 예외를 지정하세요."); return

        lines = []
        for t in tasks:
            gy = "" if t["gyosi"] is None else (" 조회" if t["gyosi"] == 0 else " {}교시".format(t["gyosi"]))
            lines.append(" · {}번 {} : {}{}{}".format(t["no"], t["name"], t["gubun"], t["jongryu"], gy))
        if not messagebox.askyesno("나이스에 입력할까요?",
                "{} 화면에 아래 {}명을 입력합니다.\n(저장은 끝난 뒤 다시 물어봅니다)\n\n{}".format(
                    self.att_date.get().strip(), len(tasks), "\n".join(lines))):
            return

        self.att_apply_btn.state(["disabled"])
        self.log("[{}] 출결 입력 시작 — {}명".format(datetime.now().strftime("%H:%M:%S"), len(tasks)))
        match = self.cfg.get("attendance", {}).get("match", "sen.neis.go.kr")

        def work():
            res, status = do_write_attendance(
                self._port(), match, tasks, logf=lambda s: self.ui_post("log", s))
            self.ui_post("attwrite", {"res": res, "status": status})
        threading.Thread(target=work, daemon=True).start()

    def _show_attwrite(self, payload):
        self.att_apply_btn.state(["!disabled"])
        res, status = payload.get("res"), payload.get("status", "")
        if not res:
            messagebox.showerror("입력 실패", "입력하지 못했습니다.\n\n{}".format(status)); return
        done, fail = res.get("done") or [], res.get("fail") or []
        self.log("[{}] 출결 입력 끝 — 성공 {} / 실패 {}".format(
            datetime.now().strftime("%H:%M:%S"), len(done), len(fail)))

        msg = ["화면에 넣은 학생 {}명".format(len(done))]
        for d in done:
            msg.append("  ✅ {} : {}".format(d["name"], d["what"]))
        if fail:
            msg.append("")
            msg.append("못 넣은 학생 {}명 — 나이스에서 직접 넣으세요".format(len(fail)))
            for f in fail:
                msg.append("  ❌ {} — {}".format(f["name"], f["why"]))
        if status != "ok":
            msg.append("")
            msg.append("도중에 멈춤: {}".format(status))

        if not done:
            messagebox.showwarning("입력 결과", "\n".join(msg)); return

        msg.append("")
        msg.append("아직 «화면에만» 들어가 있습니다. 지금 저장할까요?")
        msg.append("(아니오를 누르면 나이스 화면에서 직접 확인하고 저장하시면 됩니다)")
        if not messagebox.askyesno("저장할까요?", "\n".join(msg)):
            self.log("  · 저장은 하지 않았습니다 (화면에만 입력됨)")
            return

        match = self.cfg.get("attendance", {}).get("match", "sen.neis.go.kr")

        def work():
            ok, why = do_save_attendance(self._port(), match, logf=lambda s: self.ui_post("log", s))
            self.ui_post("attsave", {"ok": ok, "why": why})
        threading.Thread(target=work, daemon=True).start()

    def _show_attsave(self, payload):
        if payload.get("ok"):
            messagebox.showinfo("저장 완료",
                "나이스가 저장을 확인했습니다.\n\n{}\n\n"
                "마감(확정)은 나이스에서 직접 '출결마감' → '확인' 하세요.".format(payload.get("why", "")))
        else:
            messagebox.showwarning("저장을 확인하지 못했습니다",
                "{}\n\n나이스 화면을 직접 보고 확인해 주세요.".format(payload.get("why", "")))

    # ===================== 컴시간 시간표 (로컬 캐시 읽기) =====================
    def _build_comci(self):
        frm = self.tab_comci
        top = ttk.Frame(frm)
        top.pack(fill="x")
        ttk.Label(top, text="컴시간 시간표", font=("Malgun Gothic", 15, "bold")).pack(side="left")
        ttk.Button(top, text="다시 읽기", command=self._comci_load).pack(side="left", padx=10)
        self.comci_updated = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.comci_updated, foreground="#777").pack(side="left")

        ctl = ttk.Frame(frm)
        ctl.pack(fill="x", pady=(8, 4))
        self.comci_mode = tk.StringVar(value="교사")
        ttk.Radiobutton(ctl, text="교사별", value="교사", variable=self.comci_mode,
                        command=self._comci_mode_changed).pack(side="left")
        ttk.Radiobutton(ctl, text="학급별", value="학급", variable=self.comci_mode,
                        command=self._comci_mode_changed).pack(side="left", padx=(4, 12))
        self.comci_pick = tk.StringVar()
        self.comci_combo = ttk.Combobox(ctl, textvariable=self.comci_pick, state="readonly", width=18)
        self.comci_combo.pack(side="left")
        self.comci_combo.bind("<<ComboboxSelected>>", lambda e: self._comci_render())

        ttk.Label(ctl, text="주차").pack(side="left", padx=(14, 2))
        self.comci_week = tk.StringVar()
        self.comci_wcombo = ttk.Combobox(ctl, textvariable=self.comci_week, state="readonly", width=22)
        self.comci_wcombo.pack(side="left")
        self.comci_wcombo.bind("<<ComboboxSelected>>", lambda e: self._comci_render())

        ttk.Label(frm, foreground="#a33", justify="left", wraplength=940, text=(
            "컴시간 알림이가 받아둔 시간표를 서버 없이 로컬 캐시에서 읽어옵니다. 주차를 고르면 그 주의 "
            "수업교환까지 반영되고, 교환된 칸은 노란색으로 표시됩니다. '기본(교환 전)'은 원본 시간표예요. "
            "알림이에서 최신화한 뒤 [다시 읽기]를 누르면 갱신됩니다.")
        ).pack(anchor="w", pady=(2, 6))

        # 표: 헤더(요일) + 7교시 × 5요일 라벨 격자
        grid = ttk.Frame(frm, relief="solid", borderwidth=1)
        grid.pack(fill="both", expand=True)
        self.comci_cells = {}
        headers = ["교시"] + list(ComciReader.DAYS)
        for c, h in enumerate(headers):
            grid.columnconfigure(c, weight=(0 if c == 0 else 1), uniform="col")
            tk.Label(grid, text=h, font=("Malgun Gothic", 10, "bold"),
                     bg="#ecdff3", relief="solid", borderwidth=1, padx=6, pady=6
                     ).grid(row=0, column=c, sticky="nsew")
        for p in range(1, 8):
            grid.rowconfigure(p, weight=1, uniform="row")
            tk.Label(grid, text="{}교시".format(p), font=("Malgun Gothic", 10, "bold"),
                     bg="#f6f0fa", relief="solid", borderwidth=1, padx=6
                     ).grid(row=p, column=0, sticky="nsew")
            for d in range(1, 6):
                lb = tk.Label(grid, text="", font=("Malgun Gothic", 10), justify="center",
                              relief="solid", borderwidth=1, bg="white")
                lb.grid(row=p, column=d, sticky="nsew")
                self.comci_cells[(p, d)] = lb

        self.root.after(1400, self._comci_load)   # 탭 준비되면 한 번 자동 로드

    def _comci_load(self):
        configured = (self.cfg.get("paths", {}).get("comci_dir", "") or "").strip()
        tmp = find_comci_dir(configured)     # 설치 위치 자동 탐지(PC마다 달라도 OK)
        if tmp != configured:
            self.log("컴시간 폴더 자동 탐지: {}".format(tmp))
        try:
            self.comci = ComciReader(tmp).load()
        except Exception as e:
            self.comci = None
            self.comci_updated.set("· 읽기 실패(컴시간 알림이 설치 확인)")
            self.comci_combo["values"] = []
            self.comci_pick.set("")
            for lb in getattr(self, "comci_cells", {}).values():
                lb.config(text="", bg="white")
            self.log("컴시간 캐시 읽기 실패: {} (경로: {})".format(e, tmp))
            return
        self.comci_updated.set("· 캐시 갱신 {}".format(
            self.comci.mtime.strftime("%Y-%m-%d %H:%M")))
        self.log("컴시간 시간표 로드: 교사 {}명 / 학급 {}개 / 주차 {}개".format(
            len(self.comci.teachers), len(self.comci.classes), self.comci.nweeks))
        # 주차 드롭다운: '기본(교환 전)' + 1..nweeks
        self._comci_weeks = [(None, "기본(교환 전)")] + \
            [(w, self.comci.week_label(w)) for w in range(1, self.comci.nweeks + 1)]
        self.comci_wcombo["values"] = [lbl for _w, lbl in self._comci_weeks]
        cur = self.comci.current_week()
        # 기본 선택 = 오늘 주차(있으면), 없으면 기본
        default_lbl = next((lbl for w, lbl in self._comci_weeks if w == cur),
                           self._comci_weeks[0][1])
        self.comci_week.set(default_lbl)
        self._comci_mode_changed()

    def _comci_selected_week(self):
        """드롭다운에서 고른 주차(1-based) 또는 None(기본)."""
        lbl = self.comci_week.get()
        for w, l in getattr(self, "_comci_weeks", []):
            if l == lbl:
                return w
        return None

    def _comci_mode_changed(self):
        if not getattr(self, "comci", None):
            return
        if self.comci_mode.get() == "교사":
            vals = list(self.comci.teachers)
        else:
            vals = [name for (_u, _lab, name) in self.comci.classes]
        self.comci_combo["values"] = vals
        if vals:
            self.comci_pick.set(vals[0])
            self._comci_render()

    def _comci_render(self):
        cells = getattr(self, "comci_cells", {})
        for lb in cells.values():
            lb.config(text="", bg="white")
        rd = getattr(self, "comci", None)
        if not rd:
            return
        week = self._comci_selected_week()
        pick = self.comci_pick.get()
        if self.comci_mode.get() == "교사":
            if pick not in rd.teachers:
                return
            ti = rd.teachers.index(pick)
            grid = rd.teacher_grid(ti, week)
            base = rd.teacher_grid(ti, None)     # 교환 표시용 비교 기준
        else:
            unit = next((u for (u, _l, name) in rd.classes if name == pick), None)
            if unit is None:
                return
            grid = rd.class_grid(unit, week)
            base = rd.class_grid(unit, None)
        # 그 주에만 있고 기본과 다른 칸 = 수업교환 → 노란색
        for (p, d), txt in grid.items():
            lb = cells.get((p, d))
            if not lb:
                continue
            changed = week is not None and base.get((p, d)) != txt
            lb.config(text=txt, bg=("#fff2b0" if changed else "#f3f7ff"))

    # ===================== 교실 호출 (교실 스마트폰에 큰 알림) =====================
    def _build_call(self):
        frm = self.tab_call
        cc = self.cfg.get("classroom_call", {})
        topic = cc.get("topic", "")
        ttk.Label(frm, text="교실 호출", font=("Malgun Gothic", 15, "bold")).pack(anchor="w")
        ttk.Label(frm, foreground="#777", text=(
            "받는 반을 골라 보내면 그 반 전자칠판에만 크게 뜹니다. 여러 반은 각각 눌러 함께 선택.   채널(base): {}".format(topic or "(미설정)"))
        ).pack(anchor="w", pady=(0, 8))

        row1 = ttk.Frame(frm); row1.pack(fill="x", pady=3)
        ttk.Label(row1, text="받는 학생", width=10).pack(side="left")
        self.call_name = tk.StringVar()
        ttk.Entry(row1, textvariable=self.call_name, width=16).pack(side="left")
        self.call_name.trace_add("write", lambda *a: self._call_preview())
        ttk.Label(row1, text="보내는 사람", width=11).pack(side="left", padx=(16, 0))
        self.call_from = tk.StringVar(value=cc.get("sender", ""))
        ttk.Entry(row1, textvariable=self.call_from, width=14).pack(side="left")
        self.call_from.trace_add("write", lambda *a: self._call_preview())

        # ---- 받는 반 선택 (반별 토픽 base-학년-반). 전체 방송은 없음 — 필요한 반을 각각 누른다 ----
        self.call_sel = set()          # 선택된 반 {"1-1", ...}
        self.call_class_btns = {}
        sel_wrap = ttk.LabelFrame(frm, text="받는 반 (여러 반 동시 선택 가능)", padding=6)
        sel_wrap.pack(fill="x", pady=(8, 2))
        top = ttk.Frame(sel_wrap); top.pack(fill="x")
        self.call_target_var = tk.StringVar(value="받는 반: (미선택)")
        ttk.Label(top, textvariable=self.call_target_var, foreground="#c0392b").pack(side="left")
        counts = cc.get("class_counts", {"1": 6, "2": 8, "3": 6})
        for g in sorted(counts, key=lambda x: int(x)):
            gr = ttk.Frame(sel_wrap); gr.pack(fill="x", pady=1)
            ttk.Label(gr, text="{}학년".format(g), width=7).pack(side="left")
            for c in range(1, int(counts[g]) + 1):
                key = "{}-{}".format(g, c)
                b = tk.Button(gr, text="{}반".format(c), width=5, relief="raised", cursor="hand2",
                              command=lambda k=key: self._call_toggle_class(k))
                b.pack(side="left", padx=2)
                self.call_class_btns[key] = b

        chips = ttk.Frame(frm); chips.pack(fill="x", pady=(8, 2))
        ttk.Label(chips, text="빠른 선택:", foreground="#555").pack(side="left", padx=(0, 6))
        for p in cc.get("presets", []):
            ttk.Button(chips, text=p, command=lambda t=p: self._call_set_msg(t)).pack(side="left", padx=2)

        row2 = ttk.Frame(frm); row2.pack(fill="x", pady=(8, 3))
        ttk.Label(row2, text="메시지", width=10).pack(side="left")
        self.call_msg = tk.StringVar()
        e2 = ttk.Entry(row2, textvariable=self.call_msg)
        e2.pack(side="left", fill="x", expand=True)
        self.call_msg.trace_add("write", lambda *a: self._call_preview())
        e2.bind("<Return>", lambda ev: self.send_call())

        pv = tk.Frame(frm, bg="black", height=90)
        pv.pack(fill="x", pady=10)
        pv.pack_propagate(False)
        self.call_pv = tk.Label(pv, text="화면 미리보기", bg="black", fg="white",
                                font=("Malgun Gothic", 18, "bold"))
        self.call_pv.pack(expand=True)

        btnrow = ttk.Frame(frm); btnrow.pack(fill="x")
        self.call_btn = tk.Button(btnrow, text="보내기", command=self.send_call,
                                  bg="#c0392b", fg="white", font=("Malgun Gothic", 12, "bold"),
                                  relief="flat", cursor="hand2", padx=20, pady=8)
        self.call_btn.pack(side="left")
        ttk.Button(btnrow, text="📡 연결 확인", command=self.check_boards).pack(side="left", padx=(10, 0))
        # ⚡ 전원 켜기(Wake-on-LAN)는 2026-09-28 수업 중 칠판이 꺼지는 현상 보고로 버튼을 뺌.
        # 원인이 확인되기 전까지 수업 중 칠판 전원에 영향을 줄 수 있는 기능은 노출하지 않는다.
        ttk.Button(btnrow, text="교실 설치 도우미…", command=self.open_call_setup).pack(side="left", padx=(6, 0))
        self.call_status = tk.StringVar(value="")
        ttk.Label(btnrow, textvariable=self.call_status).pack(side="left", padx=12)

        # ---- 보낸 내역: 반마다 한 줄. 칠판이 '받았음'을 돌려주면 수신확인, 안 오면 송신 실패 ----
        ttk.Label(frm, text="보낸 내역  (수신: 칠판 화면에 떴는지 · 답장: 교실에서 누른 버튼)",
                  foreground="#555").pack(anchor="w", pady=(14, 2))
        logwrap = ttk.Frame(frm); logwrap.pack(fill="both", expand=True)
        cols = ("time", "cls", "msg", "state", "reply")
        self.call_tree = ttk.Treeview(logwrap, columns=cols, show="headings", height=7)
        for c, t, w, anc in (("time", "보낸 시각", 80, "center"), ("cls", "받는 반", 110, "w"),
                             ("msg", "메시지", 330, "w"), ("state", "수신", 190, "w"),
                             ("reply", "답장", 220, "w")):
            self.call_tree.heading(c, text=t)
            self.call_tree.column(c, width=w, anchor=anc)
        self.call_tree.tag_configure("ok", foreground="#1b7a3a")
        self.call_tree.tag_configure("wait", foreground="#8a6d00")
        self.call_tree.tag_configure("fail", foreground="#c0392b")
        self.call_tree.tag_configure("reply", foreground="#1b5fa8")
        sb = ttk.Scrollbar(logwrap, orient="vertical", command=self.call_tree.yview)
        self.call_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.call_tree.pack(side="left", fill="both", expand=True)

    def _call_compose(self):
        name = self.call_name.get().strip()
        body = self.call_msg.get().strip()
        if not body:
            return ""
        if name:
            last = name[-1]
            if "가" <= last <= "힣":              # 받침 있으면 '아~', 없으면 '야~'
                josa = "아~ " if (ord(last) - 0xAC00) % 28 else "야~ "
            else:
                josa = "야~ "
            line = name + josa + body
        else:
            line = body
        sender = self.call_from.get().strip()   # 송신자 → '\n— OOO 로부터' (표시기에서 작은 줄로 뜸)
        if sender:
            line = line + "\n— " + sender + " 로부터"
        return line

    def _call_preview(self):
        self.call_pv.config(text=self._call_compose() or "화면 미리보기")

    def _call_set_msg(self, text):
        self.call_msg.set(text)
        self._call_preview()

    # ---- 받는 반 선택 ----
    def _class_label(self, key):
        g, c = key.split("-")
        return "{}학년{}반".format(g, c)

    def _call_toggle_class(self, key):
        if key in self.call_sel:
            self.call_sel.discard(key)
        else:
            self.call_sel.add(key)
        self._call_refresh_sel()

    CALL_MARK = {"on": "●", "old": "◐", "off": "○", "unk": "?"}

    def _call_refresh_sel(self):
        online = getattr(self, "call_online", {})
        for key, b in self.call_class_btns.items():
            st = online.get(key, {}).get("state")
            b.config(text="{}반{}".format(key.split("-")[1], self.CALL_MARK.get(st, "")))
            if key in self.call_sel:
                b.config(bg="#c0392b", fg="white")            # 선택됨(기호로 상태는 계속 보임)
            elif st == "on":
                b.config(bg="#d9f2df", fg="#1b5e20")          # ● 지금 받을 수 있음
            elif st == "old":
                b.config(bg="#fff4cc", fg="#8a6d00")          # ◐ 핑엔 무응답이지만 오늘 수신 기록 있음(구버전 추정)
            elif st == "off":
                b.config(bg="#ececec", fg="#a0a0a0")          # ○ 응답 없음(v1.4+ 앱인데 무응답 = 꺼짐/끊김)
            elif st == "unk":
                b.config(bg="#f6f6f6", fg="#7a7a7a")          # ? 확인 불가(핑을 못 듣는 구버전·미설치)
            else:
                b.config(bg="SystemButtonFace", fg="black")   # 아직 확인 안 함
        if self.call_sel:
            names = ", ".join(self._class_label(k) for k in sorted(self.call_sel,
                              key=lambda k: (int(k.split("-")[0]), int(k.split("-")[1]))))
            self.call_target_var.set("받는 반: " + names)
        else:
            self.call_target_var.set("받는 반: (미선택)")

    def send_call(self):
        text = self._call_compose()
        if not text:
            self.call_status.set("보낼 내용을 입력하세요")
            return
        base = self.cfg.get("classroom_call", {}).get("topic", "")
        # 대상 = 고른 반마다 반별 채널(+등록된 칠판이면 학교망 직접)
        if self.call_sel:
            targets = [(self._class_label(k), k, "{}-{}".format(base, k))
                       for k in sorted(self.call_sel, key=lambda k: (int(k.split("-")[0]), int(k.split("-")[1])))]
        else:
            self.call_status.set("받는 반을 선택하세요")
            return

        sender = self.call_from.get().strip()   # 마지막 송신자 기억
        if sender and self.cfg.get("classroom_call", {}).get("sender", "") != sender:
            self.cfg.setdefault("classroom_call", {})["sender"] = sender
            try:
                save_config(self.cfg)
            except Exception:
                pass
        self.call_btn.config(state="disabled")
        self.call_status.set("보내는 중…")

        # 호출 번호 — 칠판이 '받았음/답장'을 이 번호로 돌려준다. 반마다 보낸 내역 한 줄씩.
        cid = new_call_id()
        now = datetime.now().strftime("%H:%M:%S")
        msg1 = text.split("\n")[0]
        for label, cls, _tp in targets:
            iid = "{}|{}".format(cid, cls)
            self.call_rows[iid] = {"cid": cid, "cls": cls, "label": label, "acked": False,
                                   "failed": False, "replies": []}
            self.call_tree.insert("", 0, iid=iid, values=(now, label, msg1, "보내는 중…", ""), tags=("wait",))

        def work():
            results = []
            for label, cls, tp in targets:
                ok, detail = self._deliver(cls, tp, text, cid)
                results.append((label, cls, ok, detail))
            self.ui_post("call", {"cid": cid, "results": results, "text": text,
                                  "target": ", ".join(l for l, _, _ in targets)})
        threading.Thread(target=work, daemon=True).start()

    def _deliver(self, cls, topic, text, cid=None):
        """한 대상에게 보내기. 학교망 직접(전자칠판 앱) → 안 되면 인터넷 중계(ntfy)로 자동 전환.
           ※ 같은 호출을 두 길로 동시에 보내지 않는다(칠판에 두 번 뜨는 것 방지)."""
        cc = self.cfg.get("classroom_call", {})
        mode = cc.get("send_mode", "lan_first")
        boards = cc.get("boards", {}) or {}
        port = cc.get("lan_port", 8787)
        key = cc.get("lan_key", "")

        if mode == "lan_only":                        # 그 반 칠판에 직접만
            b = boards.get(cls) or {}
            ok, detail = do_send_lan(b.get("ip"), b.get("port", port), text, key, cid)
            return ok, ("직접 " + detail if ok else "직접 실패: " + detail)

        if mode == "lan_first":
            b = boards.get(cls) or {}
            if b.get("ip"):
                ok, detail = do_send_lan(b.get("ip"), b.get("port", port), text, key, cid)
                if ok:
                    return True, "직접"
                # 학교망이 막혔거나 칠판이 꺼진 경우 → 중계로 자동 전환
                ok2, detail2 = do_send_call(topic, text, cid)
                return ok2, ("중계(직접 실패: {})".format(detail) if ok2 else detail2)

        ok, detail = do_send_call(topic, text, cid)
        return ok, ("중계" if ok else detail)

    CALL_ACK_SEC = 15      # 이 시간 안에 칠판이 '받았음'을 안 돌려주면 송신 실패로 본다

    def _call_row(self, iid, state=None, reply=None, tag=None):
        if not self.call_tree.exists(iid):
            return
        vals = list(self.call_tree.item(iid, "values"))
        if state is not None:
            vals[3] = state
        if reply is not None:
            vals[4] = reply
        self.call_tree.item(iid, values=vals, tags=((tag,) if tag else self.call_tree.item(iid, "tags")))

    def _show_call_result(self, p):
        """서버/칠판까지 '보내기'가 끝난 결과. 여기서 끝이 아니라, 칠판의 수신확인을 기다린다."""
        self.call_btn.config(state="normal")
        now = datetime.now().strftime("%H:%M:%S")
        cid = p.get("cid")
        results = p.get("results", [])
        sent_fail, waiting = [], 0
        for label, cls, ok, detail in results:
            iid = "{}|{}".format(cid, cls)
            row = self.call_rows.get(iid)
            if not ok:
                # 인터넷 끊김 등으로 서버까지도 못 감
                sent_fail.append(label)
                if row:
                    row["failed"] = True
                self._call_row(iid, "✗ 송신 실패 — 보내지 못함 ({})".format(str(detail)[:40]), tag="fail")
            elif str(detail).startswith("직접"):
                # 학교망 직접: 칠판 앱이 200으로 답했다 = 받았음
                if row:
                    row["acked"] = True
                self._call_row(iid, "✓ 수신 확인 {} (학교망 직접)".format(now[:5]), tag="ok")
            else:
                waiting += 1
                self._call_row(iid, "… 칠판 응답 기다리는 중", tag="wait")

        if results and not sent_fail:
            self.call_status.set("보냈습니다 ({}) — 칠판 수신확인 기다리는 중…".format(now))
        elif sent_fail and len(sent_fail) < len(results):
            self.call_status.set("일부 송신 실패: {}".format(", ".join(sent_fail)))
        elif sent_fail:
            self.call_status.set("✗ 송신 실패 — 인터넷/서버 연결을 확인하세요")
        if waiting:
            self.root.after(self.CALL_ACK_SEC * 1000, lambda c=cid: self._call_ack_timeout(c))
        self.log("[{}] 교실 호출({}): {}".format(now, p.get("target", ""), p.get("text")))

    def _call_ack_timeout(self, cid):
        """기다려도 '받았음'이 안 온 반 → 송신 실패(칠판 꺼짐/앱 꺼짐/인터넷 끊김)."""
        missing = []
        for iid, row in self.call_rows.items():
            if row["cid"] != cid or row["acked"] or row["failed"]:
                continue
            row["failed"] = True
            missing.append(row["label"])
            self._call_row(iid, "✗ 송신 실패 — 칠판이 꺼져 있거나 앱이 꺼짐", tag="fail")
        if missing:
            self.call_status.set("✗ 송신 실패: {} — 칠판이 꺼져 있거나 앱이 켜져 있지 않습니다".format(
                ", ".join(missing)))
            self.log("[{}] 교실 호출 송신 실패(수신확인 없음): {}".format(
                datetime.now().strftime("%H:%M:%S"), ", ".join(missing)))
            try:
                self.root.bell()
            except Exception:
                pass

    def _on_call_reply(self, b):
        """교실에서 돌아온 신호 처리. ack=화면에 떴음, reply=교실에서 누른 답장 버튼, pong=연결확인 응답."""
        self._note_seen(b)
        if b.get("t") == "pong":
            self._mark_board(b.get("pid"), b.get("cls"), "인터넷", b.get("ver"), b.get("ip"), b.get("port"),
                             b.get("mac"), info=b)
            return
        if b.get("t") == "hello":
            self._on_board_hello(b)
            return
        cid, cls = b.get("cid"), (b.get("cls") or "").strip()
        kind = b.get("t", "ack")
        at = datetime.fromtimestamp(b.get("_time") or time.time()).strftime("%H:%M")
        iid = "{}|{}".format(cid, cls)
        row = self.call_rows.get(iid) if cls else None
        label = self._class_label(cls) if cls else "(반 미지정)"

        if not row:
            return                               # 이 콘솔이 보낸 호출이 아님(다른 선생님 콘솔 등)
        if kind == "reply":
            row["replies"].append(b.get("reply", ""))
            if not row["acked"]:
                row["acked"] = True
                self._call_row(iid, "✓ 수신 확인 {}".format(at), tag="ok")
            self._call_row(iid, reply="💬 {} ({})".format(b.get("reply", ""), at), tag="reply")
        elif not row["acked"]:
            late = row["failed"]
            row["acked"] = True
            row["failed"] = False
            self._call_row(iid, ("✓ 늦게 수신 확인 {}" if late else "✓ 수신 확인 {}").format(at), tag="ok")

        if kind == "reply":
            self.call_status.set("💬 {} 답장: {} ({})".format(label, b.get("reply", ""), at))
            self.log("[{}] 교실 호출 답장 — {}: {}".format(at, label, b.get("reply", "")))
            try:
                self.root.bell()
            except Exception:
                pass

    HELLO_LIVE_SEC = 15 * 60   # 이 시간 안에 온 '켜짐 알림'만 지금 켜져 있는 것으로 표시

    def _on_board_hello(self, b):
        """칠판 앱(v1.6+)이 켜져 인터넷에 붙었을 때 스스로 보내는 알림. [연결 확인] 없이도 ●로 바뀐다."""
        cls = (b.get("cls") or "").strip()
        if not cls:
            return
        self._save_board_info(cls, b)
        at = float(b.get("_time") or time.time())
        if time.time() - at > self.HELLO_LIVE_SEC:
            return                                   # 콘솔을 켤 때 받아온 지난 기록 — 저장만 하고 표시는 안 함
        online = getattr(self, "call_online", None)
        if online is None:
            online = self.call_online = {}
        online[cls] = {"state": "on", "via": {"켜짐 알림"}, "ver": b.get("ver"), "at": at}
        self._call_refresh_sel()
        why = {"boot": "칠판 켜짐", "start": "앱 시작", "reconnect": "다시 연결"}.get(b.get("reason"), "연결")
        if b.get("down_sec"):
            why += "(끊겼던 시간 {}분)".format(int(b.get("down_sec")) // 60)
        net = {"eth": "유선", "wifi": "와이파이"}.get(b.get("net"), b.get("net") or "?")
        self.log("[{}] ● {} 칠판 {} — {} {}".format(datetime.fromtimestamp(at).strftime("%H:%M"),
                                                    self._class_label(cls), why, net, b.get("ip", "")))

    def _save_board_info(self, cls, info, arp_mac_val=None):
        """칠판이 알려준 주소·MAC을 직접 전송/전원 켜기용으로 저장.
           mac = 유선(전원 켜기는 유선 MAC으로만 됨), mac_wifi = 무선, mac_arp = 이 PC가 그 IP에서 본 MAC.
           (예전엔 arp 값이 앱이 알려준 유선 MAC을 덮어써서 무선 MAC이 들어간 반이 있었다)"""
        boards = self.cfg.setdefault("classroom_call", {}).setdefault("boards", {})
        cur = dict(boards.get(cls) or {})
        new = dict(cur)
        ip = info.get("ip")
        if ip and ip != "주소없음":
            new["ip"] = ip
            new["port"] = info.get("port") or cur.get("port", 8787)
        new.setdefault("room", "")
        eth = norm_mac(info.get("mac_eth"))
        if not eth and info.get("mac") and not self._is_v16(info.get("ver")):
            eth = norm_mac(info.get("mac"))          # v1.5는 유선 우선으로 보냈음
        if eth:
            new["mac"] = eth
        wifi = norm_mac(info.get("mac_wifi"))
        if wifi:
            new["mac_wifi"] = wifi
        if arp_mac_val:
            new["mac_arp"] = norm_mac(arp_mac_val)
        for k in ("net", "eth_link", "ver"):
            if info.get(k) not in (None, ""):
                new[k] = info.get(k)
        if new != cur:
            boards[cls] = new
            try:
                save_config(self.cfg)
            except Exception:
                pass
            return True
        return False

    @staticmethod
    def _is_v16(ver):
        try:
            major, minor = (str(ver or "0.0").split(".") + ["0"])[:2]
            return (int(major), int(minor)) >= (1, 6)
        except Exception:
            return False

    # ---- 연결 확인: 메시지를 띄우지 않고 각 반 칠판이 지금 받을 수 있는 상태인지 확인 ----
    CALL_PING_SEC = 10     # 이 시간 안에 응답이 없으면 '응답 없음'

    def check_boards(self):
        """두 길로 동시에 확인한다.
           ① 인터넷: <base>-ping 에 핑 한 번 → 칠판 앱(v1.4+)이 조용히 pong 을 돌려줌
           ② 학교망: 등록된 칠판 주소로 상태 조회 + 브로드캐스트 찾기(구버전 앱도 응답)
           어느 쪽이든 응답이 오면 '연결됨'. 칠판 화면에는 아무것도 뜨지 않는다."""
        cc = self.cfg.get("classroom_call", {})
        base = cc.get("topic", "")
        port = cc.get("lan_port", 8787)
        boards = dict(cc.get("boards", {}) or {})
        pid = new_call_id()
        self.call_ping = {"pid": pid, "at": time.time(), "done": False, "ip_changed": False}
        self.call_online = {}
        self._call_refresh_sel()
        self.call_status.set("📡 연결 확인 중… ({}초)".format(self.CALL_PING_SEC))

        def work():
            ok, detail = do_ping(base, pid)
            if not ok:
                self.ui_post("log", "[연결 확인] 인터넷 핑 실패: {}".format(detail))
            # 학교망: 등록된 칠판 하나씩 조회
            def probe(cls, b):
                info = probe_lan(b.get("ip"), b.get("port", port))
                if info and (info.get("class") or cls):
                    self.ui_post("call_presence", dict(info, pid=pid, cls=info.get("class") or cls, via="학교망",
                                                       ip=b.get("ip"), port=b.get("port", port),
                                                       mac_arp=arp_mac(b.get("ip"))))
            ts = [threading.Thread(target=probe, args=(k, v), daemon=True) for k, v in boards.items()]
            for t in ts:
                t.start()
            # 학교망: 브로드캐스트로 새로 켜진 칠판도 찾기
            for info in discover_boards(port=port, wait=2.5):
                if info.get("class"):
                    self.ui_post("call_presence", dict(info, pid=pid, cls=info.get("class"), via="학교망",
                                                       port=info.get("port", port),
                                                       mac_arp=arp_mac(info.get("ip"))))
        threading.Thread(target=work, daemon=True).start()
        self.root.after(self.CALL_PING_SEC * 1000, lambda p=pid: self._finish_board_check(p))

    WOL_BOOT_SEC = 90     # 전원 신호 뒤 칠판 부팅 + 앱 자동 시작까지 기다리는 시간

    def power_on_selected(self):
        """고른 반 칠판에 '랜으로 켜기(Wake-on-LAN)' 신호를 보내고, 부팅 시간 뒤 자동으로 연결 확인.
           칠판이 대기 상태(전원 버튼으로 끈 상태)이고 '랜으로 켜기'가 켜져 있어야 동작한다.
           전원 코드가 빠졌거나 차단기가 내려간 상태는 어떤 방법으로도 못 켠다."""
        if not self.call_sel:
            self.call_status.set("⚡ 켤 반을 먼저 고르세요")
            return
        cc = self.cfg.setdefault("classroom_call", {})
        boards = cc.setdefault("boards", {})
        order = sorted(self.call_sel, key=lambda k: (int(k.split("-")[0]), int(k.split("-")[1])))
        sent, skipped = [], []
        for cls in order:
            b = boards.get(cls) or {}
            mac = norm_mac(b.get("mac"))
            if not mac:
                raw = simpledialog.askstring(
                    "칠판 MAC 주소",
                    "{} 칠판의 MAC 주소를 입력하세요.\n"
                    "(칠판 설정 → 정보/네트워크에 있는 12자리, 예: 00:1A:2B:3C:4D:5E)\n"
                    "칠판 앱이 v1.5 이상이면 [연결 확인] 한 번으로 자동 등록되니 입력할 필요가 없습니다.".format(
                        self._class_label(cls)),
                    parent=self.root)
                mac = norm_mac(raw)
                if not mac:
                    skipped.append(cls)
                    continue
                boards[cls] = dict(b, mac=mac)
                try:
                    save_config(self.cfg)
                except Exception:
                    pass
            n = send_wol(mac, b.get("ip"))                 # ① 이 PC에서 직접(같은 구간일 때만 닿음)
            (sent if n else skipped).append(cls)
        if sent:                                             # ② 켜져 있는 칠판들이 같은 구간에서 대신 쏨
            macs = [norm_mac((boards.get(k) or {}).get("mac")) for k in sent]
            ok, detail = do_wake_relay(cc.get("topic", ""), [m for m in macs if m], new_call_id())
            if not ok:
                self.log("⚡ 칠판 중계 요청 실패: {}".format(detail))
        now = datetime.now().strftime("%H:%M")
        if sent:
            self.log("[{}] ⚡ 전원 켜기 신호 보냄: {}".format(now, ", ".join(self._class_label(k) for k in sent)))
            self.call_status.set("⚡ {}반에 전원 켜기 신호 보냄 — {}초 뒤 자동으로 연결 확인합니다".format(
                len(sent), self.WOL_BOOT_SEC))
            self.root.after(self.WOL_BOOT_SEC * 1000, self.check_boards)
        if skipped:
            self.log("[{}] ⚡ MAC 주소가 없어 건너뜀: {}".format(now, ", ".join(self._class_label(k) for k in skipped)))
            if not sent:
                self.call_status.set("⚡ MAC 주소가 없어 신호를 보내지 못했습니다")

    def _mark_board(self, pid, cls, via, ver=None, ip=None, port=None, mac=None, info=None):
        ping = getattr(self, "call_ping", None)
        cls = (cls or "").strip()
        if not ping or pid != ping["pid"] or not cls:
            return                                    # 다른(이전) 확인에 대한 늦은 응답
        rec = self.call_online.setdefault(cls, {"state": "on", "via": set()})
        rec["state"] = "on"
        rec["via"].add(via)
        rec["ver"] = ver or rec.get("ver")
        # 응답에 실린 칠판 주소·MAC으로 학교망 직접 전송/전원 켜기용 등록을 최신화
        info = dict(info or {}, ip=ip, port=port, ver=ver)
        if mac and not info.get("mac"):
            info["mac"] = mac
        self._save_board_info(cls, info, arp_mac_val=info.get("mac_arp"))
        self._call_refresh_sel()
        n = sum(1 for r in self.call_online.values() if r["state"] == "on")
        if not ping["done"]:
            self.call_status.set("📡 연결 확인 중… 응답 {}반".format(n))
        else:
            self._board_check_summary()               # 마감 후 늦게 온 응답도 반영

    def _finish_board_check(self, pid):
        ping = getattr(self, "call_ping", None)
        if not ping or pid != ping["pid"]:
            return
        ping["done"] = True
        counts = self.cfg.get("classroom_call", {}).get("class_counts", {"1": 6, "2": 8, "3": 6})
        seen = self.state.get("call_last_seen", {})
        now = time.time()
        for g in counts:
            for c in range(1, int(counts[g]) + 1):
                key = "{}-{}".format(g, c)
                if key in self.call_online:
                    continue
                last = seen.get(key) or {}
                recent = now - float(last.get("at") or 0) <= self.CALL_SEEN_SEC
                if recent and not self._is_v14(last.get("ver")):
                    # 핑엔 무응답이지만 최근에 실제 호출을 받았던 반 = 핑을 못 듣는 구버전 앱일 가능성이 큼
                    self.call_online[key] = {"state": "old", "via": set(), "at": last.get("at")}
                elif self._is_v14(last.get("ver")):
                    # 핑을 듣는 앱이 깔린 걸 아는 반인데 무응답 = 정말로 꺼짐/네트워크 끊김
                    self.call_online[key] = {"state": "off", "via": set(), "at": last.get("at")}
                else:
                    # 구버전이거나 앱 기록이 전혀 없는 반 = 켜져 있어도 핑엔 답할 수 없음 → 판단 불가
                    self.call_online[key] = {"state": "unk", "via": set(), "at": last.get("at") if recent else None}
        if ping.get("ip_changed"):
            try:
                save_config(self.cfg)
            except Exception:
                pass
        self._call_refresh_sel()
        self._board_check_summary(log=True)

    CALL_SEEN_SEC = 12 * 3600     # 이 시간 안에 수신확인·답장·응답이 온 반은 '최근 살아 있었음'

    @staticmethod
    def _is_v14(ver):
        try:
            major, minor = (str(ver or "0.0").split(".") + ["0"])[:2]
            return (int(major), int(minor)) >= (1, 4)
        except Exception:
            return False          # 'html'·'pc' 같은 비(非)앱 수신기는 버전 비교 대상이 아님

    def _note_seen(self, b):
        """수신확인(ack)·답장(reply)·연결응답(pong) 모두 '그 반 칠판이 그때 살아 있었다'는 증거다."""
        cls = (b.get("cls") or "").strip()
        if not cls:
            return
        seen = self.state.setdefault("call_last_seen", {})
        at = float(b.get("_time") or time.time())
        cur = seen.get(cls) or {}
        if at >= float(cur.get("at") or 0):
            # 수신확인·답장엔 버전이 없으므로, 한 번이라도 pong 으로 알게 된 버전은 유지한다
            seen[cls] = {"at": at, "ver": b.get("ver") or cur.get("ver", ""), "t": b.get("t"), "via": b.get("via")}
            save_state(self.state)

    def _board_check_summary(self, log=False):
        order = lambda k: (int(k.split("-")[0]), int(k.split("-")[1]))
        pick = lambda st: sorted([k for k, r in self.call_online.items() if r["state"] == st], key=order)
        on, old, off, unk = pick("on"), pick("old"), pick("off"), pick("unk")
        now = datetime.now().strftime("%H:%M")
        self.call_status.set("📡 {} 확인 — ● 연결됨 {} · ◐ 구버전 추정 {} · ○ 꺼짐/끊김 {} · ? 확인 불가 {}".format(
            now, len(on), len(old), len(off), len(unk)))
        if not log:
            return
        lab = lambda ks: ", ".join(self._class_label(k) for k in ks)
        hm = lambda ts: datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "?"
        self.log("[{}] 연결 확인 — ● 연결됨({}): {}".format(now, len(on), lab(on) or "없음"))
        if old:
            self.log("   ◐ 구버전 추정({}): {} — 핑엔 무응답이지만 최근 실제 호출을 받았던 반입니다. "
                     "앱을 v1.6으로 올리면 ●로 확인됩니다.".format(
                         len(old), ", ".join("{}({} 수신)".format(self._class_label(k), hm(self.call_online[k].get("at")))
                                              for k in old)))
        if off:
            self.log("   ○ 꺼짐/끊김({}): {} — 새 앱이 깔린 칠판인데 응답 없음: 칠판 전원 꺼짐 또는 와이파이 끊김.".format(
                len(off), lab(off)))
        if unk:
            self.log("   ? 확인 불가({}): {} — 구버전 앱(v1.3 이하)이거나 앱이 없어 켜져 있어도 대답을 못 하는 반. "
                     "v1.6을 깔면 확인됩니다.".format(len(unk), lab(unk)))

    def _start_call_listener(self):
        base = self.cfg.get("classroom_call", {}).get("topic", "")
        if not base:
            return
        self.call_listener = CallReplyListener(base + "-reply", self.ui_post)
        self.call_listener.start()

    # ---- 교실 설치 도우미: 반마다 무엇을 넣어야 하는지 한 번에 보여주고 복사 ----
    def open_call_setup(self):
        """반별 채널 규칙(base-학년-반)에 맞춰 교실마다 넣을 값을 만들어 준다.
           · 표시기.html 주소 →  <주소>?c=2-8
           · 교실수신기 옆 '반.txt' 내용 →  2-8
           · 전자칠판 ntfy 앱 구독 토픽 →  base-2-8"""
        cc = self.cfg.get("classroom_call", {})
        base = cc.get("topic", "")
        counts = cc.get("class_counts", {"1": 6, "2": 8, "3": 6})

        win = tk.Toplevel(self.root)
        win.title("교실 설치 도우미 — 반별 채널")
        win.geometry("980x560")
        ttk.Label(win, text="반별 채널 설치표", font=("Malgun Gothic", 13, "bold")).pack(anchor="w", padx=12, pady=(12, 2))
        ttk.Label(win, foreground="#777", justify="left", text=(
            "교실마다 '자기 반 채널'만 받도록 설치하면 됩니다. (base = {} , 모든 교실 공통)\n"
            "· 전자칠판 앱 → '우리 반' 칸에 '반' 값   · 스마트폰 표시기 → 아래 '표시기 주소'로 열기\n"
            "· 교실PC 수신기 → exe 옆 '반.txt'에 '반' 값 적기   · 전자칠판 ntfy 앱 → '반별 채널' 하나 구독".format(base or "(미설정)")
        )).pack(anchor="w", padx=12, pady=(0, 8))

        urlrow = ttk.Frame(win); urlrow.pack(fill="x", padx=12)
        ttk.Label(urlrow, text="표시기.html 주소", width=15).pack(side="left")
        url_var = tk.StringVar(value=cc.get("display_url", ""))
        ttk.Entry(urlrow, textvariable=url_var).pack(side="left", fill="x", expand=True)
        ttk.Label(win, foreground="#999", text=(
            "   (예: https://아이디.github.io/call/표시기.html — 비워두면 주소 칸은 '(주소 미설정)'으로 나옵니다)"
        )).pack(anchor="w", padx=12, pady=(2, 8))

        tvwrap = ttk.Frame(win); tvwrap.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        # ---- 전자칠판 전용 앱(교실호출.apk) 쪽: 보내는 방법 + 칠판 자동 찾기 ----
        lanrow = ttk.Frame(win); lanrow.pack(fill="x", padx=12, pady=(0, 6))
        ttk.Label(lanrow, text="보내는 방법", width=15).pack(side="left")
        mode_var = tk.StringVar(value=cc.get("send_mode", "lan_first"))
        for val, txt in (("lan_first", "학교망 직접 먼저 → 안 되면 인터넷 중계(권장)"),
                         ("ntfy_only", "인터넷 중계만"),
                         ("lan_only", "학교망 직접만(외부 서버 안 씀)")):
            ttk.Radiobutton(lanrow, text=txt, value=val, variable=mode_var).pack(side="left", padx=(0, 10))

        def save_mode():
            self.cfg.setdefault("classroom_call", {})["send_mode"] = mode_var.get()
            try:
                save_config(self.cfg)
                msg_var.set("보내는 방법을 저장했습니다: " + mode_var.get())
            except Exception as e:
                messagebox.showerror("오류", str(e), parent=win)
        mode_var.trace_add("write", lambda *a: save_mode())

        def scan():
            msg_var.set("학교망에서 전자칠판 앱을 찾는 중…")
            win.update_idletasks()
            port = self.cfg.get("classroom_call", {}).get("lan_port", 8787)
            found = discover_boards(port=port, wait=2.0)
            if not found:
                msg_var.set("찾지 못했습니다 — 같은 학교망(같은 대역)인지, 칠판 앱이 켜져 있는지 확인하세요.")
                return
            boards = self.cfg.setdefault("classroom_call", {}).setdefault("boards", {})
            added = []
            for b in found:
                cls = (b.get("class") or "").strip()
                if not cls:
                    continue
                boards[cls] = {"ip": b.get("ip"), "port": b.get("port", port), "room": b.get("room", "")}
                added.append("{}={}".format(self._class_label(cls), b.get("ip")))
            try:
                save_config(self.cfg)
            except Exception as e:
                messagebox.showerror("오류", str(e), parent=win); return
            refill()
            msg_var.set("칠판 {}대 등록: {}".format(len(added), ", ".join(added)))

        cols = ("cls", "topic", "clsfile", "url", "board")
        tv = ttk.Treeview(tvwrap, columns=cols, show="headings", height=14, selectmode="extended")
        for c, t, w in (("cls", "반", 90), ("topic", "반별 채널(토픽)", 240),
                        ("clsfile", "반.txt 내용", 80), ("url", "표시기 주소", 380),
                        ("board", "칠판 앱 주소(학교망)", 150)):
            tv.heading(c, text=t); tv.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(tvwrap, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y"); tv.pack(side="left", fill="both", expand=True)

        def rows():
            boards = self.cfg.get("classroom_call", {}).get("boards", {}) or {}
            out = []
            for g in sorted(counts, key=lambda x: int(x)):
                for c in range(1, int(counts[g]) + 1):
                    key = "{}-{}".format(g, c)
                    u = url_var.get().strip()
                    b = boards.get(key) or {}
                    out.append((self._class_label(key), "{}-{}".format(base, key), key,
                                (u + "?c=" + key) if u else "(주소 미설정)",
                                b.get("ip", "") or "(미등록)"))
            return out

        def refill(*_a):
            tv.delete(*tv.get_children())
            for r in rows():
                tv.insert("", "end", values=r)
        url_var.trace_add("write", refill)
        refill()

        def copy_col(idx, what):
            sel = tv.selection()
            if not sel:
                messagebox.showinfo("안내", "먼저 목록에서 반을 고르세요.", parent=win); return
            vals = [tv.item(i, "values")[idx] for i in sel]
            self.root.clipboard_clear(); self.root.clipboard_append("\n".join(vals))
            msg_var.set("복사됨({}): {}".format(what, ", ".join(vals)[:70]))

        def save_url():
            self.cfg.setdefault("classroom_call", {})["display_url"] = url_var.get().strip()
            try:
                save_config(self.cfg)
                msg_var.set("표시기 주소를 설정에 저장했습니다.")
            except Exception as e:
                messagebox.showerror("오류", str(e), parent=win)

        def save_txt():
            path = APP_DIR / "교실호출_반별채널표.txt"
            lines = ["교실 호출 — 반별 채널 설치표",
                     "기본 채널(base, 모든 교실 공통): {}".format(base), ""]
            for cls, tp, cf, u, bip in rows():
                lines.append("{:<12} 채널={}  반.txt={}  칠판앱={}  주소={}".format(cls, tp, cf, bip, u))
            lines += ["", "설치 방법",
                      " · 전자칠판 전용 앱(교실호출.apk): 설치 후 '우리 반'만 적고 권한 2개 허용 → 수신 시작",
                      "     (앱은 학교망 직접 수신 + 우리 반 중계 채널을 동시에 받음)",
                      " · 스마트폰 표시기: 위 '주소'를 그 교실 폰 크롬으로 열고 화면 한 번 터치 → 홈 화면에 추가",
                      " · 교실PC 수신기: 교실수신기.exe 옆에 메모장으로 '반.txt' 만들고 그 반 값(예: 2-8)만 적기",
                      " · 전자칠판 ntfy 앱(전용 앱 대신 쓸 때): 그 반 채널 하나만 구독"]
            try:
                path.write_text("\n".join(lines), encoding="utf-8")
                msg_var.set("저장: {}".format(path))
                os.startfile(str(path))
            except Exception as e:
                messagebox.showerror("오류", str(e), parent=win)

        btns = ttk.Frame(win); btns.pack(fill="x", padx=12, pady=(0, 6))
        ttk.Button(btns, text="주소 저장", command=save_url).pack(side="left")
        ttk.Button(btns, text="표시기 주소 복사", command=lambda: copy_col(3, "주소")).pack(side="left", padx=6)
        ttk.Button(btns, text="반별 채널 복사", command=lambda: copy_col(1, "채널")).pack(side="left")
        ttk.Button(btns, text="반.txt 값 복사", command=lambda: copy_col(2, "반.txt")).pack(side="left", padx=6)
        ttk.Button(btns, text="설치표 파일로 저장", command=save_txt).pack(side="left", padx=6)
        ttk.Button(btns, text="칠판 앱 자동 찾기", command=scan).pack(side="left", padx=(16, 4))

        def test_direct():
            sel = tv.selection()
            if not sel:
                messagebox.showinfo("안내", "먼저 목록에서 반을 고르세요.", parent=win); return
            cls = tv.item(sel[0], "values")[2]
            b = (self.cfg.get("classroom_call", {}).get("boards", {}) or {}).get(cls) or {}
            if not b.get("ip"):
                messagebox.showinfo("안내", "그 반 칠판이 아직 등록되지 않았습니다. '칠판 앱 자동 찾기'를 먼저 눌러주세요.",
                                    parent=win); return
            cc2 = self.cfg.get("classroom_call", {})
            ok, detail = do_send_lan(b["ip"], b.get("port", cc2.get("lan_port", 8787)),
                                     "연결 테스트입니다\n— 교무실 로부터", cc2.get("lan_key", ""))
            msg_var.set(("직접 전송 성공: " if ok else "직접 전송 실패: ") + str(detail))

        ttk.Button(btns, text="직접 전송 테스트", command=test_direct).pack(side="left")
        ttk.Button(btns, text="닫기", command=win.destroy).pack(side="right")
        msg_var = tk.StringVar(value="")
        ttk.Label(win, textvariable=msg_var, foreground="#2d6cdf").pack(anchor="w", padx=12, pady=(0, 10))

        def open_url():
            sel = tv.selection()
            if not sel:
                return
            u = tv.item(sel[0], "values")[3]
            if u.startswith("http"):
                webbrowser.open(u)
        tv.bind("<Double-1>", lambda e: open_url())

    def _build_settings(self):
        frm = self.tab_set
        self.var_ahk = tk.StringVar(value=self.cfg["paths"].get("ahk_script", ""))
        self.var_ahk_exe = tk.StringVar(value=self.cfg["paths"].get("ahk_exe", ""))
        self.var_chrome = tk.StringVar(value=self.cfg["paths"].get("chrome_exe", ""))
        self.var_port = tk.StringVar(value=str(self.cfg.get("debug_port", 9222)))
        self.var_interval = tk.StringVar(value=str(self.cfg.get("check_interval_sec", 600)))
        rows = [
            ("AHK 로그인 스크립트", self.var_ahk),
            ("AutoHotkey 실행파일(선택)", self.var_ahk_exe),
            ("크롬 실행파일(비우면 자동탐지)", self.var_chrome),
            ("디버그 포트", self.var_port),
            ("세션 갱신 주기(초)", self.var_interval),
        ]
        for i, (label, var) in enumerate(rows):
            ttk.Label(frm, text=label).grid(row=i, column=0, sticky="w", pady=6)
            ttk.Entry(frm, textvariable=var, width=72).grid(row=i, column=1, sticky="we", padx=6)
        ttk.Button(frm, text="설정 저장", command=self.save_settings).grid(row=len(rows), column=1, sticky="w", pady=12)
        ttk.Label(frm, foreground="#555", justify="left", text=(
            "• 크롬 프로필: {}\n"
            "• 설정 파일: {}\n"
            "• 주기를 바꾸면 '세션 유지 중지 후 다시 시작'해야 적용됩니다.").format(PROFILE_DIR, CONFIG_PATH)
        ).grid(row=len(rows) + 1, column=0, columnspan=2, sticky="w", pady=(4, 0))
        frm.columnconfigure(1, weight=1)

    # ---------- 동작 ----------
    def log(self, text):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text if text.endswith("\n") else text + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def ui_post(self, kind, payload):
        self.ui_queue.put((kind, payload))

    def _drain_ui(self):
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "log":
                    self.log(payload)
                elif kind == "status":
                    iid = self.status_rows.get(payload["name"])
                    if iid:
                        self.tree.item(iid, values=(payload["name"], payload["state"],
                                                    payload["last"], payload["detail"]))
                elif kind == "next":
                    self.next_at = time.time() + payload
                elif kind == "monitor":
                    self.mon_tree.delete(*self.mon_tree.get_children())
                    for row in payload:
                        self.mon_tree.insert("", "end", values=(
                            row["src"], row["label"], row["value"], row["delta"], row["last"]))
                elif kind == "doclist":
                    self._show_doclist(payload)
                elif kind == "attendance":
                    self._show_attendance(payload)
                elif kind == "roster":
                    self._show_roster(payload)
                elif kind == "attdiag":
                    self._show_attdiag(payload)
                elif kind == "attwrite":
                    self._show_attwrite(payload)
                elif kind == "attsave":
                    self._show_attsave(payload)
                elif kind == "perf":
                    self._show_perf(payload)
                elif kind == "today":
                    self._show_today(payload)
                elif kind == "call":
                    self._show_call_result(payload)
                elif kind == "call_reply":
                    self._on_call_reply(payload)
                elif kind == "call_presence":
                    self._mark_board(payload.get("pid"), payload.get("cls"), payload.get("via"),
                                     payload.get("ver"), payload.get("ip"), payload.get("port"), payload.get("mac"),
                                     info=payload)
                elif kind == "alim":
                    self._show_alim(payload)
                elif kind == "meal":
                    self._show_meal(payload)
                elif kind == "stat":
                    self._record_stat(payload)
                elif kind == "conn":
                    self.conn_var.set(payload)
                elif kind == "gian_list":
                    self._show_gian_list(payload)
                elif kind == "gian_read":
                    self._show_gian_read(payload)
                elif kind == "gian_apply":
                    self._show_gian_apply(payload)
                elif kind == "gian_att":
                    self._show_gian_att(payload)
                elif kind == "gian_std":
                    self._show_gian_std(payload)
                elif kind == "gian_check":
                    self._show_gian_check(payload)
                elif kind == "gian_prog":
                    self.gian_status.set(payload)
                elif kind == "gian_case":
                    self._show_gian_case(payload)
        except queue.Empty:
            pass
        # 다음 갱신까지 남은 시간 표시
        if self.worker and self.next_at:
            remain = int(self.next_at - time.time())
            if remain > 0:
                self.next_var.set("다음 갱신까지 {}분 {}초".format(remain // 60, remain % 60))
            else:
                self.next_var.set("갱신 중…")
        self.root.after(500, self._drain_ui)

    def _refresh_conn_status(self):
        port = self._port()

        def check():
            try:
                pages = list_pages(port)
                n = sum(1 for p in pages if p.get("type") == "page")
                self.ui_post("conn", "크롬 디버그: 연결됨 (탭 {}개)".format(n))
            except Exception:
                self.ui_post("conn", "크롬 디버그: 끊김 (먼저 로그인 자동화 실행)")
        threading.Thread(target=check, daemon=True).start()
        self.root.after(5000, self._refresh_conn_status)

    def _port(self):
        try:
            return int(self.var_port.get()) if hasattr(self, "var_port") else self.cfg["debug_port"]
        except Exception:
            return self.cfg["debug_port"]

    def run_login(self):
        script = resolve_path(self.cfg["paths"].get("ahk_script", ""))
        if not script or not script.exists():
            messagebox.showerror("오류", "AHK 스크립트를 찾지 못했습니다. 설정 탭에서 경로를 확인하세요.")
            return
        ahk_exe = self.cfg["paths"].get("ahk_exe", "").strip()
        try:
            if ahk_exe:
                subprocess.Popen([ahk_exe, str(script)])
            elif os.name == "nt":
                os.startfile(str(script))  # noqa
            else:
                subprocess.Popen([str(script)])
            self.log("로그인 자동화 실행: {}".format(script.name))
        except Exception as e:
            messagebox.showerror("오류", "로그인 자동화 실행 실패\n{}".format(e))

    def _open_neis_smart(self):
        """[나이스 열기]: 접속이 살아 있으면 그 탭을 앞으로, 죽었으면 '깨끗한 재접속'.
           (나이스는 주소(main.jsp)를 그냥 열면 로그인이 안 되고, 업무포털 SSO 주소로 들어가야 한다.)"""
        port = self._port()
        try:
            page = find_target_page(port, "neis.go.kr")
            if page and neis_session_ok(port):
                try:
                    urllib.request.urlopen("http://127.0.0.1:{}/json/activate/{}".format(port, page["id"]), timeout=4).read()
                except Exception:
                    pass
                self.ui_post("log", "나이스: 이미 접속 중인 탭을 앞으로 가져왔습니다.")
                return
            ok, d = neis_reenter(port, find_chrome(self.cfg))
            self.ui_post("log", "나이스 열기: " + d)
            if ok and self.worker and self.worker.is_alive():
                time.sleep(8)
                self.worker.trigger_now()
        except Exception as e:
            self.ui_post("log", "나이스 열기 실패: {}".format(e))

    def open_site(self, name):
        url = self.cfg["urls"].get(name)
        if not url:
            return
        if name == "나이스" and HAS_WS:
            threading.Thread(target=self._open_neis_smart, daemon=True).start()
            return
        chrome = find_chrome(self.cfg)
        # 디버그 크롬(같은 프로필)에 탭으로 열어야 세션 유지 대상이 됨
        if chrome and PROFILE_DIR:
            try:
                subprocess.Popen([chrome, '--user-data-dir=' + PROFILE_DIR, url])
                self.log("{} 열기(디버그 크롬): {}".format(name, url))
                return
            except Exception as e:
                self.log("디버그 크롬 열기 실패({}) → 기본 브라우저로 대체".format(e))
        webbrowser.open(url)
        self.log("{} 열기(기본 브라우저): {}".format(name, url))

    def toggle_keeper(self):
        if self.worker and self.worker.is_alive():
            self.worker.stop()
            self.worker = None
            self._stop_anchor()
            if self.neis_guard:
                self.neis_guard.stop()
                self.neis_guard = None
            self.keep_btn.config(text="세션 유지 시작")
            self.keep_state_var.set("세션 유지: 중지")
            self.next_var.set("")
            self.log("세션 유지를 중지했습니다.")
            return
        if not HAS_WS:
            messagebox.showerror("오류", "websocket-client가 설치되어 있지 않습니다.\npip install websocket-client")
            return
        interval = self.cfg.get("check_interval_sec", 600)
        try:
            interval = int(self.var_interval.get())
        except Exception:
            pass
        self.worker = KeepAliveWorker(self._port(), self.cfg["keepalive_targets"], interval, self.ui_post)
        self.worker.auto_reenter = bool(self.cfg.get("neis_auto_reenter", True))
        self.worker.chrome_path = find_chrome(self.cfg)
        self.worker.start()
        # 탭 지킴이: 대상 탭에 계속 붙어 있으면서, 크롬이 버리거나 얼린 탭을 되살린다
        self._start_anchor()
        # 나이스 연장창 감시자(빠른 주기) 동시 시작
        neis_match = next((t["match"] for t in self.cfg["keepalive_targets"] if _infer_mode(t) == "neis"), None)
        if neis_match:
            gsec = self.cfg.get("neis_guard_sec", 20)
            rbelow = self.cfg.get("neis_reload_below_min", 12)
            rcrit = self.cfg.get("neis_reload_critical_min", 4)
            rext = self.cfg.get("neis_extend_below_min", 30)
            self.neis_guard = NeisGuardWorker(self._port(), neis_match, gsec, self.ui_post,
                                              str(APP_DIR / "neis_popup_capture.html"),
                                              reload_below_min=rbelow, critical_min=rcrit,
                                              extend_below_min=rext)
            self.neis_guard.start()
            self.log("나이스 접속시간 감시 시작 (매 {}초) — 잔여 {}분 이하면 새로고침 없이 연장 (안 되면 새로고침: 백그라운드 ≤{}분/전면 ≤{}분)".format(
                gsec, rext, rbelow, rcrit))
        self.keep_btn.config(text="세션 유지 중지")
        self.keep_state_var.set("세션 유지: 실행 중 (주기 {}분)".format(max(1, interval // 60)))
        self.log("세션 유지를 시작했습니다. (주기 {}초)".format(interval))

    def _start_anchor(self):
        global TAB_ANCHOR
        self._stop_anchor()

        def revived():
            if self.worker and self.worker.is_alive():
                self.worker.trigger_now()

        self.tab_anchor = TabAnchorWorker(self._port(), self.cfg["keepalive_targets"], self.ui_post, revived)
        TAB_ANCHOR = self.tab_anchor
        self.tab_anchor.start()
        self.log("탭 지킴이 시작 — 나이스·에듀파인 탭이 멈추면(크롬이 탭을 버리거나 재우면) 자동으로 되살립니다")

    def _stop_anchor(self):
        global TAB_ANCHOR
        a = getattr(self, "tab_anchor", None)
        if a is not None:
            a.stop()
        self.tab_anchor = None
        TAB_ANCHOR = None

    def refresh_now(self):
        if self.worker and self.worker.is_alive():
            self.worker.trigger_now()
            self.log("지금 갱신 요청.")
        else:
            messagebox.showinfo("안내", "먼저 '세션 유지 시작'을 눌러주세요.")

    def toggle_monitor(self):
        if self.monitor_worker and self.monitor_worker.is_alive():
            self.monitor_worker.stop()
            self.monitor_worker = None
            self.mon_btn.config(text="관제 시작")
            self.mon_state_var.set("관제: 중지")
            self.log("결재 관제를 중지했습니다.")
            return
        if not HAS_WS:
            messagebox.showerror("오류", "websocket-client가 필요합니다.\npip install websocket-client")
            return
        interval = self.cfg.get("monitor_interval_sec", 180)
        self.monitor_worker = MonitorWorker(self._port(), self.cfg.get("monitors", []),
                                            interval, self.ui_post, self.state)
        self.monitor_worker.start()
        self.mon_btn.config(text="관제 중지")
        self.mon_state_var.set("관제: 실행 중 (주기 {}분)".format(max(1, interval // 60)))
        self.log("결재 관제를 시작했습니다. (주기 {}초)".format(interval))

    def monitor_now(self):
        if self.monitor_worker and self.monitor_worker.is_alive():
            self.monitor_worker.trigger_now()
            self.log("관제 즉시 확인 요청.")
        else:
            # 관제가 꺼져 있어도 1회 확인은 백그라운드로 수행
            if not HAS_WS:
                messagebox.showerror("오류", "websocket-client가 필요합니다.")
                return

            def once():
                w = MonitorWorker(self._port(), self.cfg.get("monitors", []),
                                  9999, self.ui_post, self.state)
                w.cycle()
            threading.Thread(target=once, daemon=True).start()
            self.log("관제 1회 확인(관제 중지 상태).")

    def query_load(self):
        if not HAS_WS:
            messagebox.showerror("오류", "websocket-client가 필요합니다.")
            return
        name = self.query_target.get()
        match = next((s["match"] for s in self.cfg.get("doclist_sources", []) if s["name"] == name), None)
        self.q_tree.delete(*self.q_tree.get_children())
        self.doclist_rows = {}
        self.q_tree.insert("", "end", values=("불러오는 중…", "", ""))

        def work():
            rows, status = do_read_doclist(self._port(), match)
            self.ui_post("doclist", {"name": name, "match": match, "rows": rows, "status": status})
        threading.Thread(target=work, daemon=True).start()

    def query_open(self):
        sel = self.q_tree.selection()
        if not sel:
            messagebox.showinfo("안내", "열 항목을 표에서 선택하세요.")
            return
        info = self.doclist_rows.get(sel[0])
        if not info:
            messagebox.showinfo("안내", "좌표 정보가 없는 행입니다. 목록을 다시 불러오세요.")
            return
        match, x, y = info

        def work():
            ok, status = do_click_xy(self._port(), match, x, y)
            self.ui_post("log", "[{}] 문서 열기 {} ({},{}) {}".format(
                datetime.now().strftime("%H:%M:%S"), "성공" if ok else "실패", x, y, status))
        threading.Thread(target=work, daemon=True).start()

    def attendance_check(self):
        if not HAS_WS:
            messagebox.showerror("오류", "websocket-client가 필요합니다.")
            return
        att = dict(self.cfg.get("attendance", {}))
        att["deadline"] = self.att_deadline.get().strip() or att.get("deadline", "15:00")
        self.att_result.set("점검 중…")

        def work():
            data, status = do_attendance_check(self._port(), att)
            self.ui_post("attendance", {"data": data, "status": status, "deadline": att["deadline"]})
        threading.Thread(target=work, daemon=True).start()

    def _show_doclist(self, payload):
        self.q_tree.delete(*self.q_tree.get_children())
        self.doclist_rows = {}
        rows = payload["rows"]
        if rows is None:
            self.q_tree.insert("", "end", values=("결과 없음 ({})".format(payload["status"]),
                                                   "해당 사이트 탭/로그인 확인", ""))
            return
        if not rows:
            self.q_tree.insert("", "end", values=("목록을 찾지 못함 — 결재/문서 '목록 화면'을 연 뒤 다시 시도", "", ""))
            return
        for r in rows:
            iid = self.q_tree.insert("", "end", values=(r.get("title", ""), r.get("sub", ""), r.get("date", "")))
            self.doclist_rows[iid] = (payload["match"], r.get("x", 0), r.get("y", 0))
        self.log("[{}] {} 목록 {}건 불러옴 (행 더블클릭/‘선택 항목 열기’로 이동)".format(
            datetime.now().strftime("%H:%M:%S"), payload["name"], len(rows)))

    def _show_attendance(self, payload):
        data = payload["data"]
        if data is None:
            self.att_result.set("결과 없음 ({}). 나이스 '출결 현황' 화면을 연 상태인지 확인하세요.".format(payload["status"]))
            return
        if not data:
            self.att_result.set("학급/마감 표시를 찾지 못했습니다. (출결 현황 화면 용어에 맞게 설정에서 패턴 조정)")
            return
        unclosed = [d["cls"] for d in data if d.get("status") == "미마감"]
        closed = [d["cls"] for d in data if d.get("status") == "마감"]
        msg = "미마감 {}개: {}   |   마감 {}개".format(len(unclosed), ", ".join(unclosed) or "없음", len(closed))
        self.att_result.set(msg)
        self.log("[{}] 출결 점검 — {}".format(datetime.now().strftime("%H:%M:%S"), msg))
        if unclosed and datetime.now().strftime("%H:%M") >= payload.get("deadline", "15:00"):
            toast("출결 미마감", "기준시각 지남 — 미마감 학급: {}".format(", ".join(unclosed)))

    def perf_check(self):
        if not HAS_WS:
            messagebox.showerror("오류", "websocket-client가 필요합니다.")
            return
        match = self.cfg.get("attendance", {}).get("match", "sen.neis.go.kr")
        ban_hint = self.perf_ban.get().strip()

        def work():
            data, status = do_check_perf(self._port(), match)
            self.ui_post("perf", {"data": data, "status": status, "ban_hint": ban_hint})
        threading.Thread(target=work, daemon=True).start()

    def perf_reset(self):
        self.perf_box.delete("1.0", "end")
        self.perf_seq = 0
        self._perf_last_sig = None

    def _show_perf(self, payload):
        # 누적: 기존 내용 지우지 않고 이어붙임
        data = payload["data"]
        if data is None:
            self.perf_box.insert("end", "\n결과 없음 ({}). 수행평가성적관리를 [조회]한 상태인지 확인.\n".format(payload["status"]))
            self.perf_box.see("end"); return
        if data.get("error"):
            msg = {
                "no-score-header": "수행평가 표를 못 찾음 — 수행평가성적관리를 [조회]했는지 확인.",
                "no-summary": "하단 합계 행을 못 읽음 — 표가 보이게 조회했는지 확인.",
            }.get(data["error"], data["error"])
            self.perf_box.insert("end", "\n읽기 실패: " + msg + "\n"); self.perf_box.see("end"); return
        items = data.get("items", [])
        if not items:
            self.perf_box.insert("end", "\n수행평가 합계를 못 찾음.\n"); self.perf_box.see("end"); return

        # 직전 읽기와 완전히 동일 = 나이스 화면을 안 바꾼 것 → 경고하고 추가 안 함
        sig = tuple((it["assessment"], it["total"], it["filled"], it["gyeolsi"], it["hakjeok"]) for it in items)
        if sig == self._perf_last_sig:
            self.perf_box.insert("end",
                "\n⚠ 직전 반과 데이터가 완전히 같습니다 — 나이스에서 강의실을 바꾸고 [조회]한 뒤 다시 누르세요. (추가 안 함)\n")
            self.perf_box.see("end")
            return
        self._perf_last_sig = sig

        self.perf_seq += 1
        ban_hint = payload.get("ban_hint", "")
        ban = data.get("ban", "")
        label = ("{}반".format(ban_hint) if ban_hint else
                 ("{}반".format(ban) if ban else "확인 #{}".format(self.perf_seq)))
        # 정상 추가된 경우에만 반 칸을 다음 번호로 자동 증가 (1→2→…)
        if ban_hint.isdigit():
            self.perf_ban.set(str(int(ban_hint) + 1))
        lines = ["", "━━━━━  ({})  ━━━━━".format(label),
                 "[수행평가 누락 점검]  ({})".format(datetime.now().strftime("%H:%M:%S"))]
        anymiss = False
        for it in items:
            miss = it.get("missing", 0)
            tags = []
            if it.get("gyeolsi"):
                tags.append("결시 {}".format(it["gyeolsi"]))
            if it.get("hakjeok"):
                tags.append("학적변동 {}".format(it["hakjeok"]))
            extra = ("  [" + ", ".join(tags) + "]") if tags else ""
            mark = "●" if miss > 0 else "○"
            if miss > 0:
                anymiss = True
            lines.append("{} {} — 누락 {}명  (수강 {} / 입력 {}){}".format(
                mark, it["assessment"], miss, it.get("total", 0), it.get("filled", 0), extra))
        lines.append("✓ 누락 없음" if not anymiss else
                     "누락 = 수강 − 입력(응시) − 결시 − 학적변동.")
        self.perf_box.insert("end", "\n".join(lines) + "\n")
        self.perf_box.see("end")
        self.log("[{}] 수행평가 누락: {} (수행평가 {}개)".format(
            datetime.now().strftime("%H:%M:%S"), label, len(items)))

    def gen_leave(self):
        lt = self.leave_type.get()
        txt = self.cfg["leave_reason_presets"].get(lt, "")
        self.leave_box.delete("1.0", "end")
        self.leave_box.insert("1.0", txt)

    def gen_doc(self):
        subject = self.doc_subject.get().strip() or "업무"
        purpose = self.doc_purpose.get().strip() or "보고"
        attach = self.doc_attach.get().strip() or "붙임자료"
        txt = (
            "[기안 초안]\n"
            "- 제목 제안: {s} {p}\n"
            "- 본문 예시: 위와 관련하여 {s} {p}하고자 합니다.\n"
            "- 붙임 문구: 붙임  {a} 1부.  끝.\n\n"
            "점검: 수신/경유/공람 · 시행일자 · 공개여부 · 붙임 파일명 일치"
        ).format(s=subject, p=purpose, a=attach)
        self.doc_box.delete("1.0", "end")
        self.doc_box.insert("1.0", txt)

    def copy_widget(self, widget):
        text = widget.get("1.0", "end").strip()
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log("클립보드에 복사했습니다.")

    def save_settings(self):
        self.cfg["paths"]["ahk_script"] = self.var_ahk.get().strip()
        self.cfg["paths"]["ahk_exe"] = self.var_ahk_exe.get().strip()
        self.cfg["paths"]["chrome_exe"] = self.var_chrome.get().strip()
        try:
            self.cfg["debug_port"] = int(self.var_port.get())
        except Exception:
            pass
        try:
            self.cfg["check_interval_sec"] = max(60, int(self.var_interval.get()))
        except Exception:
            pass
        save_config(self.cfg)
        self.log("설정을 저장했습니다. (주기 변경은 세션 유지 재시작 시 적용)")

    def on_close(self):
        if self.worker:
            self.worker.stop()
        if self.neis_guard:
            self.neis_guard.stop()
        if self.monitor_worker:
            self.monitor_worker.stop()
        if self.reminder:
            self.reminder.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except Exception:
        pass
    app = ConsoleApp(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
