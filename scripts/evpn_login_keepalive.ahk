#Requires AutoHotkey v2.0
; 서울시교육청 EVPN(원격업무) 자동 로그인 → VPN 연결 → 업무포털 인증서 로그인 → 콘솔 실행
; 단축키: Ctrl+Alt+E
; ※ 처음 1회 "프로필_최초설정.bat" 를 먼저 실행해 두세요.
; ※ EVPN ID/비밀번호는 evpn_login.py 의 EVPN_ID / EVPN_PW 두 줄에 넣으세요.

; ===================================================================
;  [설정] 크롬을 띄울 모니터 번호  (0=주 모니터, 1/2/3...=특정 모니터)
; ===================================================================
global TARGET_MONITOR := 2


ClickAbs(x, y) {
    MouseMove(x, y, 10)
    Sleep(200)
    Click("left")
}

; 창(win) 좌상단 기준 상대좌표로 클릭. 창 없으면 false 반환(→ 폴백)
ClickWinRel(win, rx, ry) {
    if WinExist(win) {
        WinActivate(win)
        Sleep(150)
        WinGetPos(&wx, &wy, , , win)
        MouseMove(wx + rx, wy + ry, 10)
        Sleep(200)
        Click("left")
        return true
    }
    return false
}


^!e:: {
    evpnUrl    := "https://evpn.sen.go.kr/custom/index.html"
    portalUrl  := "https://sen.eduptl.kr"
    chromePath := "C:\Program Files\Google\Chrome\Application\chrome.exe"
    profileDir := EnvGet("LOCALAPPDATA") . "\SenKeepAliveProfile"

    ; 뒤쪽 freezeOff = 백그라운드 탭 얼림/스로틀 방지 (나이스/에듀파인 탭이 뒤에 있어도 안 얼게 → 세션 안 죽음)
    freezeOff := '--disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-renderer-backgrounding --disable-features=IntensiveWakeUpThrottling,CalculateNativeWinOcclusion'
    flags := '--remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir="' profileDir '" --no-first-run --no-default-browser-check ' freezeOff

    ; -----------------------------------------------------------------
    ; 1. 크롬 실행 → EVPN 로그인 페이지
    ; -----------------------------------------------------------------
    Run(Format('"{}" {} "{}"', chromePath, flags, evpnUrl))
    Sleep(4000)   ; 페이지 + AXGATE 구동 로딩 대기

    ; -----------------------------------------------------------------
    ; 2. EVPN 로그인 + VPN 연결 대기
    ;    evpn_login.py 가 ID/PW 입력 → 로그인 → '연결 Yes' 까지 대기
    ;    성공 시 종료코드 0, 실패 시 1
    ; -----------------------------------------------------------------
    evpnScript := A_ScriptDir "\evpn_login.py"
    ec := 1
    if FileExist(evpnScript)
        ec := RunWait(Format('python "{}"', evpnScript), , "Hide")
    else {
        MsgBox "evpn_login.py 를 찾을 수 없습니다.`n이 AHK 파일과 같은 폴더에 두세요."
        return
    }
    if (ec != 0) {
        MsgBox "EVPN 로그인 / VPN 연결에 실패했습니다.`n- evpn_login.py 의 ID/비밀번호 확인`n- AXGATE 클라이언트 '실행' 상태 확인`n- 잠시 후 다시 시도"
        return
    }

    ; -----------------------------------------------------------------
    ; 3. 업무포털로 이동 (VPN 연결됐으니 접근 가능)
    ; -----------------------------------------------------------------
    Run(Format('"{}" {} "{}"', chromePath, flags, portalUrl))
    ; 디버그 크롬이 이미 실행 중이면 기존(백그라운드) 창에 탭만 열려 WinWaitActive가 실패 →
    ; 존재만 확인 후 직접 활성화.
    if !WinWait("업무포털",, 20) {
        MsgBox "업무포털 창을 찾지 못했습니다.`n프로필_최초설정.bat 을 먼저 실행했는지 확인하세요."
        return
    }
    WinActivate("업무포털")
    WinWaitActive("업무포털",, 5)
    Sleep(1500)

    ; -----------------------------------------------------------
    ; 3-1. 지정 모니터로 이동 + 최대화
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
    offNice     := [395, 170]   ; ⑤ 나이스 (폴백용)

    ; 인증서 팝업은 크롬 창 "안"에 뜸 → 창(업무포털) 좌상단 기준 상대좌표 (최대화 크롬, 해상도 무관)
    relCertList := [861, 463]   ; 인증서 목록 첫 항목
    relPw       := [972, 691]   ; 암호 입력칸

    ; 상단 메뉴 [나이스] 는 창 기준 상대좌표 (좌표찾기.ahk 실측값, 글꼴크기 기본 기준)
    ; ※ K-에듀파인은 이제 URL 직접 열기라 좌표 불필요
    relNice := [498, 182]       ; ⑤ 나이스

    ; -----------------------------------------------------------------
    ; 4. 인증서 로그인 버튼 클릭 (글자 찾아 클릭 우선, 실패 시 좌표)
    ; -----------------------------------------------------------------
    clickScript := A_ScriptDir "\portal_click.py"
    ec2 := 1
    if FileExist(clickScript)
        ec2 := RunWait(Format('python "{}"', clickScript), , "Hide")
    if (ec2 != 0)
        ClickAbs(mL + offCertBtn[1], mT + offCertBtn[2])

    ; -----------------------------
    ; 5. 인증서 로그인  (★ DOM 방식 — 좌표 불필요! portal_cert.py)
    ;    인증서 선택 + 암호 입력 + 확인 클릭을 HTML로 처리(라이브 검증됨).
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
    ; 6. 메인 복귀 + (팝업 있으면) Esc
    ; -----------------------------
    if !WinWaitActive("업무포털",, 15) {
        MsgBox "메인 화면으로 돌아오지 못했습니다.`n인증서 비밀번호가 맞는지 확인하세요."
        return
    }
    Send("{Escape}")
    Sleep(500)

    ; -----------------------------
    ; 7. 나이스  (★ 포털 [나이스] 메뉴 SSO — portal_menu.py, 좌표 무관)
    ;    나이스는 URL 직접열기론 SSO 자동로그인이 안 됨 → 포털 메뉴 클릭 필수.
    ;    실패 시에만 예전 좌표 클릭으로 폴백.
    ; -----------------------------
    if WinExist("업무포털")
        WinActivate("업무포털")
    Sleep(300)
    Send("{Escape}")   ; 클릭 직전 안내 팝업 닫기
    Sleep(400)
    menuScript := A_ScriptDir "\portal_menu.py"
    ec7 := 1
    if FileExist(menuScript)
        ec7 := RunWait(Format('python "{}" nais', menuScript), , "Hide")
    if (ec7 != 0) {
        if !ClickWinRel("업무포털", relNice[1], relNice[2])
            ClickAbs(mL + offNice[1], mT + offNice[2])
    }
    Sleep(2000)

    ; -----------------------------
    ; 8. K-에듀파인  (URL 직접 열기 — 인증서 로그인 후 SSO 자동 연결)
    ;    E알리미(8-1) / 콘솔 'K-에듀파인 열기' 버튼과 동일한 검증된 방식
    ;    → 업무포털 중복 열기 + 좌표 클릭 제거 (탭 6→5개)
    ; -----------------------------
    Sleep(1000)
    Run(Format('"{}" {} "{}"', chromePath, flags, "https://klef.sen.go.kr"))
    Sleep(2000)

    ; -----------------------------
    ; 8-1. E알리미  (같은 디버그 크롬에 탭으로 오픈 → 프로필에 로그인 유지)
    ;      최초 1회만 로그인하면 다음부터 세션 유지됨
    ; -----------------------------
    Sleep(1500)
    Run(Format('"{}" {} "{}"', chromePath, flags, "https://www.ealimi.com/Document/SignListReady"))
    Sleep(2000)

    ; =============================================
    ; 9. ★ 통합 콘솔 자동 시작 (세션 유지 포함) ★
    ; =============================================
    Sleep(2000)
    consoleBat := A_ScriptDir "\콘솔_실행.bat"
    keeperBat  := A_ScriptDir "\세션유지_실행.bat"
    if FileExist(consoleBat)
        Run(Format('"{}"', consoleBat))
    else if FileExist(keeperBat)
        Run(Format('"{}"', keeperBat))
    else
        MsgBox "콘솔_실행.bat(또는 세션유지_실행.bat)을 찾을 수 없습니다."

    ExitApp
}
