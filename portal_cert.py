# -*- coding: utf-8 -*-
"""
업무포털 인증서 로그인 — 좌표 없이 DOM(CDP)으로 처리.
------------------------------------------------------------------
인증서 팝업이 브라우저 안 HTML이라(해상도/모니터/줌 무관) 좌표가 필요 없다.
디버그 크롬(9222)의 업무포털 탭에서:
  1) 인증서 목록에서 CERT_NAME 인증서 선택 (이미 선택돼 있으면 유지)
  2) 인증서 암호칸(input[name=certPassword])에 암호 입력
  3) '확인'(.kc-btn-blue) 클릭 → 로그인
성공(암호칸+확인 처리) → 종료코드 0, 실패 → 1  (AHK는 실패 시 좌표 클릭으로 폴백)

※ 인증서 이름/암호는 아래 두 줄만 수정.
"""
import sys, json, time, urllib.request

try:
    from websocket import create_connection
except ImportError:
    print("websocket-client 없음"); sys.exit(1)

PORT = 9222
URL_MATCH = "eduptl.kr"
CERT_NAME = ""              # ← 선택할 인증서(사용자명). 여러 인증서 중 이 이름을 고름. 본인 이름으로 채우세요
CERT_PW = ""      # ← 인증서 암호

JS = """
(function(){
  var NAME=%s, PW=%s;
  function vis(el){try{var r=el.getBoundingClientRect();return r.width>1&&r.height>1&&el.offsetParent!==null;}catch(e){return false;}}
  // 1) 인증서 선택: 현재 선택된 행이 NAME이 아니면 NAME 든 행을 클릭
  var picked="default";
  var sel=document.querySelector(".kc-tableview-selected-row");
  if(!(sel && (sel.innerText||"").indexOf(NAME)!==-1)){
    var rows=document.querySelectorAll("table tr, tr");
    for(var i=0;i<rows.length;i++){
      if((rows[i].innerText||"").indexOf(NAME)!==-1){
        try{rows[i].click();}catch(e){}
        var td=rows[i].querySelector("td"); if(td){try{td.click();}catch(e){}}
        picked="clicked"; break;
      }
    }
  }
  // 2) 인증서 암호 입력
  var pw=document.querySelector('input[name="certPassword"]');
  if(!pw || !vis(pw)) return JSON.stringify({ok:false, reason:"no-pw-field"});
  pw.focus(); pw.value=PW;
  pw.dispatchEvent(new Event("input",{bubbles:true}));
  pw.dispatchEvent(new Event("change",{bubbles:true}));
  pw.dispatchEvent(new KeyboardEvent("keyup",{bubbles:true}));
  // 3) 확인 버튼 클릭 (보이는 확인/로그인 버튼)
  var btn=null;
  var cand=document.querySelectorAll(".kc-btn-blue, button.btn-primary, button, a.btn, input[type=button]");
  for(var b=0;b<cand.length;b++){
    var t=(cand[b].innerText||cand[b].value||"").replace(/\\s+/g,"").trim();
    if(/^(확인|로그인)$/.test(t) && vis(cand[b])){ btn=cand[b]; break; }
  }
  if(!btn) return JSON.stringify({ok:false, reason:"no-confirm-btn", picked:picked});
  btn.click();
  return JSON.stringify({ok:true, picked:picked, btn:(btn.innerText||"").trim()});
})();
""" % (json.dumps(CERT_NAME, ensure_ascii=False), json.dumps(CERT_PW, ensure_ascii=False))


def find_page():
    data = json.loads(urllib.request.urlopen("http://127.0.0.1:%d/json/list" % PORT, timeout=5).read().decode())
    for p in data:
        if p.get("type") == "page" and URL_MATCH in (p.get("url") or ""):
            return p
    return None


def main():
    page = None
    for _ in range(8):
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
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"expression": JS, "returnByValue": True}}))
        deadline = time.time() + 10
        val = None
        while time.time() < deadline:
            d = json.loads(ws.recv())
            if d.get("id") == 1:
                val = d.get("result", {}).get("result", {}).get("value")
                break
        info = {}
        try:
            info = json.loads(val or "{}")
        except Exception:
            pass
        print("인증서 로그인 결과:", info)
        sys.exit(0 if info.get("ok") else 1)
    finally:
        ws.close()


if __name__ == "__main__":
    main()
