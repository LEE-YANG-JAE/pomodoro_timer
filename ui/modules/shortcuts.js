// 전역 키보드 단축키.
//
// ★ e.key 가 아니라 e.code 로 판정한다. 한글 IME 로 입력 중에는 e.key 가 "ㄴ" 같은
//   조합 문자라 매칭이 되지 않거나 엉뚱하게 걸린다. 또 isComposing 인 동안에는
//   아무것도 하지 않는다 — 조합 중 입력을 단축키로 삼키면 글자가 사라진다.

import { state } from "./state.js";
import { $, closeModal } from "./utils.js";

const EDITABLE = new Set(["INPUT", "TEXTAREA", "SELECT"]);

function inEditable(target) {
  if (!target) return false;
  if (EDITABLE.has(target.tagName)) return true;
  return Boolean(target.isContentEditable);
}

export function initShortcuts(handlers) {
  document.addEventListener("keydown", (e) => {
    // IME 조합 중이면 절대 개입하지 않는다
    if (e.isComposing || e.keyCode === 229) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    // Esc 는 입력 중에도 동작해야 한다 (모달 닫기)
    if (e.code === "Escape") {
      if (!closeModal()) return;
      if (state.ui.focusMode) handlers.setFocusMode(false);
      return;
    }

    // 모달이 떠 있으면 Esc 외엔 아무 단축키도 배경으로 새지 않는다 — 특히 절전 복구
    // 모달 위에서 "S"가 skipPhase() 를 실행해 복구 대상(pendingGap)을 지워버리면
    // 모달의 기록/이어서/버리기 버튼이 전부 조용히 no-op 되어 방금 끝난 세션이 사라진다.
    if (!$("#modal-backdrop")?.hidden) return;

    if (inEditable(e.target)) return;

    switch (e.code) {
      case "Space":
        e.preventDefault();          // 페이지 스크롤 방지
        handlers.toggle();
        break;
      case "KeyS":
        e.preventDefault();
        handlers.skip();
        break;
      case "KeyR":
        e.preventDefault();
        handlers.reset();
        break;
      case "KeyF":
        e.preventDefault();
        handlers.toggleFocusMode();
        break;
      case "KeyM":
        e.preventDefault();
        handlers.toggleMute();
        break;
      case "KeyN":
        e.preventDefault();
        handlers.nextTrack();
        break;
      case "KeyW":
        e.preventDefault();
        handlers.toggleNoise();
        break;
      case "KeyE":
        e.preventDefault();
        handlers.extend();
        break;
      case "KeyP":
        e.preventDefault();
        handlers.toggleMusic();
        break;
      case "KeyO":
        e.preventDefault();
        handlers.toggleShuffle();
        break;
      case "Digit1":
        handlers.switchView("timer");
        break;
      case "Digit2":
        handlers.switchView("records");
        break;
      case "Digit3":
        handlers.switchView("music");
        break;
      case "Digit4":
        handlers.switchView("settings");
        break;
      case "Slash":
        if (e.shiftKey) {
          e.preventDefault();
          handlers.help();
        }
        break;
      default:
        break;
    }
  });
}
