// 先套用主題，避免閃爍（獨立檔案而不是內嵌，CSP 才能禁止頁面內嵌程式）
try { const t = localStorage.getItem("ai-theme"); if (t) document.documentElement.setAttribute("data-theme", t); } catch (e) { /* 無痕模式 */ }
