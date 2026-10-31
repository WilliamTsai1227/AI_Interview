"""合格標準：測試結束時檢查，不合格就讓 Locust 以結束碼 1 離開（可以放進 CI 自動判斷）。

規則格式：(統計名稱, 指標, 上限)
  統計名稱："整體"（所有 API 請求，不含 [async] 背景工作的等待時間）或某一項的名稱
           （例如 "POST /interviews"、"[async] 報告產生"）
  指標：  "p95"（毫秒，95% 的請求在這個時間內完成）、"fail_ratio"（失敗比例，0.01 = 1%）
"""
from locust import events
from locust.stats import StatsEntry

TOTAL = "整體"


def _entry(stats, name):
    if name == TOTAL:      # 背景工作的等待時間以秒計，混進來會把 API 的 p95 拉高，所以排除
        agg = StatsEntry(stats, TOTAL, "", use_response_times_cache=False)
        for (_n, method), e in stats.entries.items():
            if method != "ASYNC":
                agg.extend(e)
        return agg
    found = [e for (n, _m), e in stats.entries.items() if n == name]
    return found[0] if found else None


def install(rules: list[tuple[str, str, float]]) -> None:
    @events.quitting.add_listener
    def _check(environment, **_kw):
        stats = environment.stats
        print("\n========== 合格標準 ==========")
        failed = False
        for name, metric, limit in rules:
            e = _entry(stats, name)
            if e is None or e.num_requests == 0:
                print(f"  -    {name} {metric}：沒有資料")
                continue
            if metric == "p95":
                v = e.get_response_time_percentile(0.95)
                ok, show = v <= limit, f"{v:.0f} ms（上限 {limit:.0f} ms）"
            else:
                v = e.fail_ratio
                ok, show = v <= limit, f"{v:.2%}（上限 {limit:.2%}）"
            failed |= not ok
            print(f"  {'通過' if ok else '失敗'} {name} {metric}：{show}")
        print("================================")
        if failed:
            environment.process_exit_code = 1
