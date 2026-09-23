# -*- coding: utf-8 -*-
"""
EVPN 비밀번호 자동 설치 도우미
------------------------------------------------------------
사용법:
  1) 크롬 주소창에 chrome://password-manager/passwords 열기
  2) 'sen.go.kr'(또는 evpn.sen.go.kr) 항목 → 비밀번호 옆 '복사' 버튼 클릭
  3) 이 파일(비번_설치.py)을 실행

  → 클립보드의 비번을 evpn_login.py 의 EVPN_PW 에 정확히 기록합니다.
     (원문은 화면에 안 띄우고, 길이/특수문자 여부만 알려줌 — 눈에 안 보이는
      전각/lookalike 글자까지 그대로 보존됩니다.)
"""
import os, re, json, ctypes

PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'evpn_login.py')

def get_clipboard_text():
    CF_UNICODETEXT = 13
    u = ctypes.windll.user32; k = ctypes.windll.kernel32
    if not u.OpenClipboard(0):
        return None
    try:
        h = u.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = k.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.c_wchar_p(p).value
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()

def main():
    pw = get_clipboard_text()
    if not pw:
        print('❌ 클립보드가 비어 있어요. 크롬 비번관리자에서 비번 [복사]를 먼저 누르고 다시 실행하세요.')
        input('엔터를 누르면 닫힙니다...'); return
    pw = pw.strip('\r\n')   # 복사 시 붙는 개행만 제거 (비번 자체는 그대로)
    if len(pw) > 64 or len(pw) < 2:
        print('❌ 클립보드 내용이 비번 같지 않아요 (길이 %d). 복사가 제대로 됐는지 확인하세요.' % len(pw))
        input('엔터...'); return

    weird = [(i, hex(ord(c))) for i, c in enumerate(pw) if ord(c) > 127]
    print('설치할 비번 길이:', len(pw))
    print('눈에 안 보이는(비ASCII) 글자:', weird or '없음 — 전부 일반 문자')
    print('기존 "예시비번" 와 동일?', pw == '예시비번')

    if not os.path.exists(PY):
        print('❌ evpn_login.py 를 찾을 수 없습니다:', PY); input('엔터...'); return
    src = open(PY, encoding='utf-8').read()
    line = 'EVPN_PW = %s        # ← EVPN 로그인 비밀번호 (크롬 저장비번에서 복사·설치)' % json.dumps(pw, ensure_ascii=True)
    new = re.sub(r'EVPN_PW\s*=.*', line, src, count=1)
    if new == src:
        print('❌ EVPN_PW 줄을 못 찾았어요. evpn_login.py 를 확인하세요.'); input('엔터...'); return
    open(PY, 'w', encoding='utf-8').write(new)
    print('✅ evpn_login.py 에 EVPN_PW 설치 완료!')
    print('   이제 크롬 다 닫고 Ctrl+Alt+E 다시 실행해 보세요.')
    input('엔터를 누르면 닫힙니다...')

if __name__ == '__main__':
    main()
