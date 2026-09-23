#Requires AutoHotkey v2.0
; 서울시교육청 업무포털 자동 로그인 + 세션 유지 자동 시작 (AutoHotkey v2)
; 단축키: Ctrl+Alt+S
; ※ 처음 1회 "프로필_최초설정.bat" 를 먼저 실행해 두세요.

; ===================================================================
;  [설정] 크롬을 띄울 모니터 번호  (0=주 모니터, 1/2/3...=특정 모니터)
; ===================================================================
global TARGET_MONITOR := 2


ClickAbs(x, y) {
    MouseMove(x, y, 10)
    Sleep(200)
    Click("left")
}


^!s:: {
    portalUrl  := "https://sen.eduptl.kr"
    chromePath := "C:\Program Files\Google\Chrome\Application\chrome.exe"
    profileDir := EnvGet("LOCALAPPDATA") . "\SenKeepAliveProfile"

    ; 크롬 공통 옵션 (디버그 포트 + origin 허용 + 별도 프로필 + 첫실행안내 끄기)
    ;  --remote-allow-origins=*  ← 웹소켓 403 Forbidden 방지 (필수)
    ;  뒤쪽 4개 = 백그라운드 탭 '얼림/스로틀' 방지 (★ 나이스/에듀파인 탭이 뒤에 있어도 안 얼게 →
    ;   keepalive가 항상 탭에 닿아 세션이 안 죽음. 이 크롬을 다른 사이트 브라우징에 같이 써도 안전)
    freezeOff := '--disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding --disable-features=IntensiveWakeUpThrottling,CalculateNativeWinOcclusion'
    flags := '--remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir="' profileDir '" --no-first-run --no-default-browser-check ' freezeOff

    ; -----------------------------
    ; 1. 크롬 실행
    ; -----------------------------
    Run(Format('"{}" {} "{}"', chromePath, flags, portalUrl))

    ; 디버그 크롬이 '이미 실행 중'이면 새 창 대신 기존(백그라운드) 창에 탭만 열려서
    ; WinWaitActive(활성 대기)가 타임아웃되던 문제 → 창 '존재'만 확인 후 직접 앞으로 끌어온다.
    if !WinWait("업무포털",, 20) {
        MsgBox "업무포털 창을 찾지 못했습니다.`n프로필_최초설정.bat 을 먼저 실행했는지 확인하세요."
        return
    }
    WinActivate("업무포털")
    WinWaitActive("업무포털",, 5)
    Sleep(1500)

    ; -----------------------------------------------------------
    ; 1-1. 지정 모니터로 이동 + 최대화 (mL,mT = 모니터 좌상단)
    ; -----------------------------------------------------------
    mon := TARGET_MONITOR
    if (mon = 0 || mon > MonitorGetCount())
        mon := MonitorGetPrimary()
    MonitorGetWorkArea(mon, &mL, &mT, &mR, &mB)

    try {
        WinRestore("업무포털")
        WinMove(mL, mT, mR - mL, mB - mT, "업무포털")
        WinMaximize("업무포털")
        Sleep(800)
    }

    CoordMode("Mouse", "Screen")

    ; ===================================================================
    ;  [클릭 좌표] 모니터 좌상단(mL,mT) 기준 상대좌표 (빗나가면 좌표찾기.ahk)
    ; ===================================================================
    offCertBtn  := [950, 700]   ; ② 인증서 로그인 버튼 (CDP 실패 시 대체용)
    offCertList := [900, 440]   ; ③-① 인증서 목록 첫 항목 (모니터 기준·폴백용)
    offPw       := [960, 620]   ; ③-② 암호 입력칸 (모니터 기준·폴백용)
    offNice     := [395, 170]   ; ⑤ 나이스 (모니터 기준·폴백용)

    ; 업무포털 크롬 창 좌상단 기준 상대좌표 (학교 듀얼모니터 좌표찾기.ahk 실측값 — 해상도 안정)
    relCertList := [1118, 558]  ; ③-① 인증서 목록 첫 항목
    relPw       := [1208, 784]  ; ③-② 인증서 암호 입력칸
    relNice     := [486, 229]   ; ⑤ 업무포털 메인 상단 [나이스] 메뉴
    relKef      := [635, 223]   ; ⑥ 업무포털 메인 상단 [K-에듀파인] 메뉴

    ; -----------------------------------------------------------------
    ; 2. 인증서 로그인 버튼 클릭
    ;    먼저 portal_click.py 로 "글자 찾아 클릭"(좌표 무관) 시도,
    ;    실패하면(반환값≠0) 기존 좌표 클릭으로 대체
    ; -----------------------------------------------------------------
    clickScript := A_ScriptDir "\portal_click.py"
    ec := 1
    if FileExist(clickScript)
        ec := RunWait(Format('python "{}"', clickScript), , "Hide")
    if (ec != 0)
        ClickAbs(mL + offCertBtn[1], mT + offCertBtn[2])

    ; -----------------------------
    ; 3. 인증서 로그인  (★ DOM 방식 — 좌표 불필요! 해상도·모니터·줌 무관)
    ;    portal_cert.py 가 인증서 선택 + 암호 입력 + 확인 클릭을 HTML로 처리(라이브 검증됨).
    ;    실패(반환≠0) 시에만 예전 좌표 클릭 방식으로 폴백.
    ; -----------------------------
    Sleep(2500)   ; 인증서 팝업 뜰 시간
    certPw := ""

    certScript := A_ScriptDir "\portal_cert.py"
    ec3 := 1
    if FileExist(certScript)
        ec3 := RunWait(Format('python "{}"', certScript), , "Hide")

    if (ec3 != 0) {
        ; ---- 폴백: 좌표 클릭 방식(창 기준) ----
        if WinExist("업무포털") {
            WinActivate("업무포털")
            Sleep(200)
            WinGetPos(&wx, &wy, , , "업무포털")
            ClickAbs(wx + relCertList[1], wy + relCertList[2])
            Sleep(400)
            ClickAbs(wx + relPw[1], wy + relPw[2])
        } else {
            ClickAbs(mL + offCertList[1], mT + offCertList[2])
            Sleep(400)
            ClickAbs(mL + offPw[1], mT + offPw[2])
        }
        Sleep(300)
        Send("^a")
        Sleep(100)
        SendText(certPw)
        Sleep(300)
        Send("{Enter}")
    }
    Sleep(2500)

    ; -----------------------------
    ; 4. 메인 복귀 + (팝업 있으면) Esc 로 닫기
    ; -----------------------------
    if !WinWaitActive("업무포털",, 15) {
        MsgBox "메인 화면으로 돌아오지 못했습니다.`n인증서 비밀번호가 맞는지 확인하세요."
        return
    }
    Send("{Escape}")
    Sleep(500)

    ; -----------------------------
    ; 5. 나이스  (★ 포털 [나이스] 메뉴 SSO — portal_menu.py, 좌표 무관)
    ;    나이스는 URL 직접열기론 SSO 자동로그인이 안 됨 → 포털 메뉴 클릭 필수.
    ;    portal_menu.py 가 상단 <a class=menuBtn>[나이스]</a> 를 DOM 위치로 신뢰클릭(검증됨).
    ;    실패 시에만 예전 좌표 클릭으로 폴백.
    ; -----------------------------
    menuScript := A_ScriptDir "\portal_menu.py"
    ec5 := 1
    if FileExist(menuScript)
        ec5 := RunWait(Format('python "{}" nais', menuScript), , "Hide")
    if (ec5 != 0) {
        if WinExist("업무포털") {
            WinActivate("업무포털")
            Sleep(300)
            WinGetPos(&wx, &wy, , , "업무포털")
            ClickAbs(wx + relNice[1], wy + relNice[2])
        } else {
            ClickAbs(mL + offNice[1], mT + offNice[2])
        }
    }
    Sleep(2000)

    ; -----------------------------
    ; 6. K-에듀파인  (URL 직접 열기 — SSO 자동로그인 됨, 좌표 무관, 라이브 검증됨)
    ;    ※ 나이스는 새 창으로 열려 업무포털은 그대로라 '복귀' 단계 불필요.
    ; -----------------------------
    Sleep(500)
    Run(Format('"{}" {} "{}"', chromePath, flags, "https://klef.sen.go.kr"))
    Sleep(2000)

    ; -----------------------------
    ; 6-1. E알리미  (같은 디버그 크롬에 탭으로 오픈 → 프로필에 로그인 유지)
    ;      최초 1회만 로그인하면 다음부터 세션 유지됨
    ; -----------------------------
    Sleep(1500)
    Run(Format('"{}" {} "{}"', chromePath, flags, "https://www.ealimi.com/Document/SignListReady"))
    Sleep(2000)

    ; =============================================
    ; 7. ★ 통합 콘솔 자동 시작 (세션 유지 자동 시작 포함) ★
    ; =============================================
    Sleep(2000)
    consoleBat := A_ScriptDir "\콘솔_실행.bat"
    keeperBat  := A_ScriptDir "\세션유지_실행.bat"
    if FileExist(consoleBat)
        Run(Format('"{}"', consoleBat))           ; GUI 콘솔이 뜨고 세션 유지 자동 시작
    else if FileExist(keeperBat)
        Run(Format('"{}"', keeperBat))            ; 콘솔이 없으면 기존 콘솔창 세션유지로 대체
    else
        MsgBox "콘솔_실행.bat(또는 세션유지_실행.bat)을 찾을 수 없습니다.`n이 AHK 파일과 같은 폴더에 두세요."

    ExitApp
}
