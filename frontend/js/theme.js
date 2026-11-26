// 先套用主題，避免閃爍（獨立檔案而不是內嵌，CSP 才能禁止頁面內嵌程式）
try { document.documentElement.setAttribute("data-theme", localStorage.getItem("ai-theme") || "light"); } catch (e) { document.documentElement.setAttribute("data-theme", "light"); /* 無痕模式 */ }
