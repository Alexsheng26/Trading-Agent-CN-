"""HTTP 接口。重点覆盖 SSE 断线续传 —— 手机端最容易出问题的地方。"""

from __future__ import annotations

import time


def _wait_done(client, run_id: str, timeout: float = 20.0) -> dict:
    """轮询等分析跑完。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/v1/analysis/runs/{run_id}").json()
        if run["status"] in ("done", "error", "cancelled"):
            return run
        time.sleep(0.05)
    raise AssertionError(f"分析在 {timeout}s 内未结束")


def _read_sse(response, max_events: int | None = None) -> list[tuple[str, str]]:
    """把 SSE 流读成 [(id, event_type)]。"""
    out: list[tuple[str, str]] = []
    current_id = ""
    for line in response.iter_lines():
        if line.startswith("id: "):
            current_id = line[4:]
        elif line.startswith("event: "):
            out.append((current_id, line[7:]))
            if max_events and len(out) >= max_events:
                break
    return out


# ---------------------------------------------------------------- 系统


def test_health(client) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/v1/health").json() == {"status": "ok"}


def test_status_reports_stub_mode(client) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/v1/status").json()
    assert body["llm"]["is_stub"] is True  # 测试环境必须是桩
    assert any(p["name"] == "mock" and p["healthy"] for p in body["data_providers"])


def test_root_carries_disclaimer(client) -> None:  # type: ignore[no-untyped-def]
    assert "投资建议" in client.get("/").json()["disclaimer"]


# ---------------------------------------------------------------- 行情


def test_search(client) -> None:  # type: ignore[no-untyped-def]
    results = client.get("/api/v1/market/search", params={"q": "茅台"}).json()
    assert any(r["name"] == "贵州茅台" for r in results)


def test_quote(client) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/v1/market/quote/600519").json()
    assert body["symbol"] == "600519.SH"
    assert body["price"] > 0


def test_quote_accepts_all_symbol_formats(client) -> None:  # type: ignore[no-untyped-def]
    for raw in ("600519", "sh600519", "600519.SH"):
        assert client.get(f"/api/v1/market/quote/{raw}").json()["symbol"] == "600519.SH"


def test_bad_symbol_returns_400(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/v1/market/quote/999999")
    assert resp.status_code == 400
    assert "推断交易所" in resp.json()["detail"]


def test_bars_and_indicators(client) -> None:  # type: ignore[no-untyped-def]
    bars = client.get("/api/v1/market/bars/600519", params={"days": 120}).json()
    assert len(bars) > 40
    assert {"date", "open", "high", "low", "close"} <= set(bars[0])

    ind = client.get("/api/v1/market/indicators/600519").json()
    assert ind["ma20"] is not None
    assert isinstance(ind["patterns"], list)


def test_snapshot_returns_all_dimensions(client) -> None:  # type: ignore[no-untyped-def]
    body = client.get("/api/v1/market/snapshot/600519").json()
    for key in ("basic", "quote", "bars", "indicators", "financials", "news", "money_flow"):
        assert body[key], f"snapshot 缺少 {key}"
    assert body["sources"]  # 溯源信息要带上


# ---------------------------------------------------------------- 自选股


def test_watchlist_crud(client) -> None:  # type: ignore[no-untyped-def]
    created = client.post("/api/v1/watchlist", json={"symbol": "300750", "note": "观察"})
    assert created.status_code == 201
    item = created.json()
    assert item["symbol"] == "300750.SZ"
    assert item["name"] == "宁德时代"
    assert item["quote"] is not None  # 列表页省一轮请求

    # 同一只票的不同写法也算重复
    assert client.post("/api/v1/watchlist", json={"symbol": "sz300750"}).status_code == 409

    patched = client.patch(
        f"/api/v1/watchlist/{item['id']}", json={"group_name": "核心池", "note": "加仓中"}
    ).json()
    assert patched["group_name"] == "核心池"
    assert patched["note"] == "加仓中"

    assert client.delete(f"/api/v1/watchlist/{item['id']}").status_code == 204
    assert client.get("/api/v1/watchlist").json() == []


def test_watchlist_rejects_bad_symbol(client) -> None:  # type: ignore[no-untyped-def]
    assert client.post("/api/v1/watchlist", json={"symbol": "乱码"}).status_code == 400


def test_watchlist_patch_404(client) -> None:  # type: ignore[no-untyped-def]
    assert client.patch("/api/v1/watchlist/99999", json={"note": "x"}).status_code == 404


# ---------------------------------------------------------------- 分析


def test_analysis_full_flow(client) -> None:  # type: ignore[no-untyped-def]
    started = client.post(
        "/api/v1/analysis/runs",
        json={"symbol": "600519", "horizon": "波段", "user_note": "该不该减仓"},
    )
    assert started.status_code == 202
    run_id = started.json()["run_id"]
    assert started.json()["stream_url"].endswith(f"{run_id}/stream")

    run = _wait_done(client, run_id)
    assert run["status"] == "done"
    assert run["error"] == ""
    assert run["user_note"] == "该不该减仓"
    assert len(run["outputs"]) == 7  # 4 分析师 + 1 轮辩论 2 人 + 风控
    assert run["decision"]["action"] in ("买入", "加仓", "持有", "减仓", "卖出", "观望")
    assert run["decision"]["disclaimer"]
    assert run["data_sources"]  # 溯源已落库
    assert run["llm_provider"] == "echo"


def test_analysis_rejects_bad_symbol(client) -> None:  # type: ignore[no-untyped-def]
    assert client.post("/api/v1/analysis/runs", json={"symbol": "乱码"}).status_code == 400
    assert client.post("/api/v1/analysis/runs", json={"symbol": "  "}).status_code == 422


def test_analysis_rejects_bad_horizon(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.post("/api/v1/analysis/runs", json={"symbol": "600519", "horizon": "超短线"})
    assert resp.status_code == 422


def test_sse_streams_events(client) -> None:  # type: ignore[no-untyped-def]
    run_id = client.post("/api/v1/analysis/runs", json={"symbol": "600519"}).json()["run_id"]

    with client.stream("GET", f"/api/v1/analysis/runs/{run_id}/stream") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        assert resp.headers["x-accel-buffering"] == "no"  # 别让 nginx 攒着
        events = _read_sse(resp)

    types = [t for _, t in events]
    assert types[0] == "run_start"
    assert types[-1] == "run_done"
    # 事件 id 必须严格递增，前端才能靠它续传
    ids = [int(i) for i, _ in events]
    assert ids == list(range(len(ids)))


def test_sse_resumes_from_last_event_id(client) -> None:  # type: ignore[no-untyped-def]
    """模拟手机锁屏断连后重连：不能丢事件，也不能重复。"""
    run_id = client.post("/api/v1/analysis/runs", json={"symbol": "600519"}).json()["run_id"]

    # 第一段：读 8 条就断开
    with client.stream("GET", f"/api/v1/analysis/runs/{run_id}/stream") as resp:
        first = _read_sse(resp, max_events=8)
    assert len(first) == 8
    last_id = first[-1][0]

    # 第二段：带 Last-Event-ID 续传
    with client.stream(
        "GET",
        f"/api/v1/analysis/runs/{run_id}/stream",
        headers={"Last-Event-ID": last_id},
    ) as resp:
        second = _read_sse(resp)

    assert second, "续传没拿到任何事件"
    assert int(second[0][0]) == int(last_id) + 1  # 精确接上，无重复无缺口
    assert second[-1][1] == "run_done"

    # 两段拼起来 = 完整事件流
    combined = [int(i) for i, _ in first + second]
    assert combined == list(range(len(combined)))


def test_sse_replays_from_zero_for_late_subscriber(client) -> None:  # type: ignore[no-untyped-def]
    """分析跑完后再连上来，也要能把全过程回放一遍。"""
    run_id = client.post("/api/v1/analysis/runs", json={"symbol": "600519"}).json()["run_id"]
    _wait_done(client, run_id)

    with client.stream("GET", f"/api/v1/analysis/runs/{run_id}/stream") as resp:
        events = _read_sse(resp)
    assert [t for _, t in events][0] == "run_start"
    assert [t for _, t in events][-1] == "run_done"


def test_sse_unknown_run_returns_409(client) -> None:  # type: ignore[no-untyped-def]
    resp = client.get("/api/v1/analysis/runs/deadbeefdeadbeef/stream")
    assert resp.status_code == 409
    assert "analysis/runs" in resp.json()["detail"]  # 指引前端去读落库结果


def test_run_history_and_delete(client) -> None:  # type: ignore[no-untyped-def]
    run_id = client.post("/api/v1/analysis/runs", json={"symbol": "300750"}).json()["run_id"]
    _wait_done(client, run_id)

    listing = client.get("/api/v1/analysis/runs", params={"symbol": "300750.SZ"}).json()
    assert listing["total"] >= 1
    assert listing["items"][0]["symbol"] == "300750.SZ"
    assert listing["items"][0]["action"]  # 列表页直接能看到结论

    assert client.delete(f"/api/v1/analysis/runs/{run_id}").status_code == 204
    assert client.get(f"/api/v1/analysis/runs/{run_id}").status_code == 404


def test_missing_run_returns_404(client) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/v1/analysis/runs/nope").status_code == 404
