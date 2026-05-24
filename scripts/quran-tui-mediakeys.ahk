; quran-tui — Windows media-key bridge
;
; Routes media keys to a running quran-tui via its loopback HTTP endpoint,
; falling back to the default Windows handler (Spotify / Edge / etc.) when
; quran-tui isn't running. Bind once, leave it running, never has to be
; reconfigured.
;
; Install via:  quran-tui --install-mediakeys
; Or manually:  copy this file to your Windows side and double-click.
;
; Requires AutoHotkey v2: https://www.autohotkey.com/

#Requires AutoHotkey v2.0
#SingleInstance Force

QuranTuiPort := EnvGet("QURAN_CONTROL_PORT")
if (QuranTuiPort = "") {
    QuranTuiPort := 13938
}

QuranTuiPost(action) {
    global QuranTuiPort
    try {
        whr := ComObject("WinHttp.WinHttpRequest.5.1")
        whr.SetTimeouts(200, 200, 200, 200)
        whr.Open("POST", "http://127.0.0.1:" . QuranTuiPort . "/" . action, false)
        whr.Send()
        return whr.Status >= 200 and whr.Status < 300
    } catch {
        return false
    }
}

$Media_Play_Pause:: {
    if !QuranTuiPost("play-pause") {
        Send "{Media_Play_Pause}"
    }
}
$Media_Next:: {
    if !QuranTuiPost("next") {
        Send "{Media_Next}"
    }
}
$Media_Prev:: {
    if !QuranTuiPost("prev") {
        Send "{Media_Prev}"
    }
}
$Volume_Up:: {
    if !QuranTuiPost("volume-up") {
        Send "{Volume_Up}"
    }
}
$Volume_Down:: {
    if !QuranTuiPost("volume-down") {
        Send "{Volume_Down}"
    }
}
