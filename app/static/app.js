// app.js — SPA 진입점. self-host Vue(ESM) 로 AppRoot 를 #app 에 마운트. 빌드 없음(네이티브 ES 모듈).
// updated: 2026-07-08
import { createApp } from "./vendor/vue.esm-browser.prod.js";
import AppRoot from "./components/app-root.js";

createApp(AppRoot).mount("#app");
