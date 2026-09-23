#Requires AutoHotkey v2.0
; ─────────────────────────────────────────────────────────────
;  인증서 창 좌표/제목 진단 도구
;  실행 후, 인증서 암호 팝업이 떠 있는 상태에서
;  마우스를 해당 위치에 올리고 아래 단축키를 누르세요.
;
;   Ctrl+Alt+Q : 마우스 밑 창의 제목/클래스 + 절대좌표 + 창기준 상대좌표
;   Ctrl+Alt+X : 종료
; ─────────────────────────────────────────────────────────────

CoordMode("Mouse", "Screen")

^!q:: {
    MouseGetPos(&mx, &my, &winId)
    if !winId {
        MsgBox("마우스 밑에서 창을 찾지 못했습니다. 인증서 창 위에 올리고 다시 시도하세요.", "좌표찾기")
        return
    }
    title := WinGetTitle("ahk_id " winId)   ; v2는 리턴값 방식
    cls   := WinGetClass("ahk_id " winId)
    WinGetPos(&wx, &wy, &ww, &wh, "ahk_id " winId)

    relX := mx - wx
    relY := my - wy

    A_Clipboard := Format("제목={1} / class={2}`n절대좌표=({3},{4})`n창위치=({5},{6}) 크기=({7}x{8})`n창기준 상대좌표=({9},{10})",
        title, cls, mx, my, wx, wy, ww, wh, relX, relY)

    MsgBox(
        "▼ 마우스 밑 창 정보 (클립보드에도 복사됨)`n`n"
        "제목: " title "`n"
        "class: " cls "`n`n"
        "마우스 절대좌표: (" mx ", " my ")`n"
        "창 좌상단: (" wx ", " wy ")   크기: " ww "x" wh "`n"
        "창 기준 상대좌표: (" relX ", " relY ")",
        "좌표찾기"
    )
}

^!x:: ExitApp
