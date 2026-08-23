// 페인트 전에 테마를 적용한다 — 새로고침 때 밝은 화면이 번쩍이는 걸 막는다.
// main.js 보다 먼저, 동기로 실행되어야 하므로 별도 파일로 둔다.
(function () {
  try {
    var pref = JSON.parse(localStorage.getItem("pomo.theme") || '"auto"');
    var mode = pref === "auto"
      ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
      : pref;
    document.documentElement.dataset.theme = mode;
    document.documentElement.dataset.themePref = pref;
  } catch (e) {
    document.documentElement.dataset.theme = "light";
  }
})();
