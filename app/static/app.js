// app.js — SPA 진입점. self-host Vue(ESM) 로 AppRoot 를 #app 에 마운트. 빌드 없음(네이티브 ES 모듈).
// updated: 2026-07-31
import { createApp } from "./vendor/vue.esm-browser.prod.js";
import AppRoot from "./components/app-root.js";

createApp(AppRoot).mount("#app");

// index.html 의 감시자에게 "떴다"고 알린다. 이 줄까지 못 오면(모듈 로드 실패 등) 감시자가
// 캐시를 새것으로 갈아끼우고 한 번 새로고침한다 — 흰 화면에 갇히지 않게 하는 안전장치다.
window.__lakeUp = 1;
