"""时区处理与交易时段判断。

这块之前有个真实 bug：`created_at` 走 SQLite 的 func.now()（UTC），
`finished_at` 走 Python 的 datetime.now()（容器本地时区），两个都不带
时区信息 —— 前端 new Date(iso) 按本地时区解释，"N 分钟前"直接差 8 小时。
所以这里把"存 UTC、判断用固定 UTC+8"这两条规则钉死在测试里。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.core.clock import CST, now_cst, now_utc, to_cst
from app.services.market_session import session_state

# ---------------------------------------------------------------- clock


def test_cst_is_fixed_utc8_without_dst() -> None:
    """固定偏移，不查 tzdata —— 精简镜像里没有 tzdata 也不会静默差 8 小时。"""
    assert CST.utcoffset(None) == timedelta(hours=8)
    # 一月和七月偏移相同（中国无夏令时）
    jan = datetime(2025, 1, 15, 12, tzinfo=CST)
    jul = datetime(2025, 7, 15, 12, tzinfo=CST)
    assert jan.utcoffset() == jul.utcoffset() == timedelta(hours=8)


def test_now_utc_is_aware_and_utc() -> None:
    n = now_utc()
    assert n.tzinfo is not None
    assert n.utcoffset() == timedelta(0)


def test_now_cst_is_aware_and_eight_hours_ahead() -> None:
    assert now_cst().utcoffset() == timedelta(hours=8)
    # 同一时刻的两种表示必须相等
    assert abs((now_cst() - now_utc()).total_seconds()) < 2


def test_to_cst_treats_naive_as_utc() -> None:
    """库里的历史数据是 naive UTC，不能当成本地时间解释。"""
    naive = datetime(2025, 9, 26, 1, 30)  # 存的是 UTC
    got = to_cst(naive)
    assert got is not None
    assert (got.hour, got.minute) == (9, 30)  # 北京时间 09:30 开盘
    assert got.date().isoformat() == "2025-09-26"


def test_to_cst_converts_other_zones() -> None:
    ny = datetime(2025, 9, 26, 21, 30, tzinfo=timezone(timedelta(hours=-4)))
    got = to_cst(ny)
    assert got is not None
    assert got.hour == 9 and got.minute == 30  # 次日北京时间
    assert got.date().isoformat() == "2025-09-27"


def test_to_cst_passes_none_through() -> None:
    assert to_cst(None) is None


# ---------------------------------------------------------------- 交易时段

# 2025-09-26 是周五，2025-09-27 是周六
def _cst(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2025, 9, day, hour, minute, tzinfo=CST)


@pytest.mark.parametrize(
    ("at", "phase", "is_live", "poll"),
    [
        (_cst(26, 8, 0), "盘前", False, None),
        (_cst(26, 9, 14), "盘前", False, None),
        (_cst(26, 9, 15), "集合竞价", True, 15),
        (_cst(26, 9, 24), "集合竞价", True, 15),
        (_cst(26, 9, 25), "盘前", False, None),  # 撮合完到开盘之间
        (_cst(26, 9, 30), "连续竞价", True, 5),
        (_cst(26, 11, 29), "连续竞价", True, 5),
        (_cst(26, 11, 30), "午间休市", False, None),
        (_cst(26, 12, 59), "午间休市", False, None),
        (_cst(26, 13, 0), "连续竞价", True, 5),
        (_cst(26, 14, 59), "连续竞价", True, 5),
        (_cst(26, 15, 0), "已收盘", False, None),
        (_cst(26, 23, 59), "已收盘", False, None),
        (_cst(27, 10, 30), "非交易日", False, None),  # 周六
        (_cst(28, 10, 30), "非交易日", False, None),  # 周日
    ],
)
def test_session_phases(at: datetime, phase: str, is_live: bool, poll: int | None) -> None:
    st = session_state(at)
    assert st.phase == phase
    assert st.is_live is is_live
    assert st.poll_interval == poll


def test_session_accepts_utc_input() -> None:
    """传 UTC 时刻也要判对 —— 接口层拿到的可能是任意时区。"""
    utc = datetime(2025, 9, 26, 2, 0, tzinfo=UTC)  # 北京 10:00
    st = session_state(utc)
    assert st.phase == "连续竞价"
    assert st.as_of.hour == 10


def test_next_change_points_forward() -> None:
    for at in (_cst(26, 8, 0), _cst(26, 10, 0), _cst(26, 16, 0), _cst(27, 10, 0)):
        st = session_state(at)
        assert st.next_change is not None
        assert st.next_change > at, f"{st.phase} 的 next_change 必须在未来"


def test_weekend_next_change_is_monday_auction() -> None:
    st = session_state(_cst(27, 10, 30))  # 周六
    assert st.next_change is not None
    assert st.next_change.weekday() == 0  # 周一
    assert (st.next_change.hour, st.next_change.minute) == (9, 15)


def test_friday_close_next_change_is_monday() -> None:
    """周五收盘后不能指向周六。"""
    st = session_state(_cst(26, 15, 30))
    assert st.next_change is not None
    assert st.next_change.weekday() == 0
    assert st.next_change.day == 29


def test_session_state_defaults_to_now() -> None:
    st = session_state()
    assert st.as_of.utcoffset() == timedelta(hours=8)
    assert st.phase in (
        "盘前",
        "集合竞价",
        "连续竞价",
        "午间休市",
        "已收盘",
        "非交易日",
    )


def test_holidays_are_not_handled_and_say_so() -> None:
    """节假日会被误判为交易日，这是已知取舍 —— note 必须把话说清楚。"""
    st = session_state(_cst(26, 10, 0))  # 工作日
    assert "节假日" in st.note


# ---------------------------------------------------------------- 库里进出的时区

async def test_db_returns_timezone_aware_datetimes() -> None:
    """SQLite 没有时区类型，会把偏移丢掉 —— UtcDateTime 必须在读出时补回来。

    不补的话：naive 时间戳传到前端，浏览器按本地时区解释，
    "3 分钟前"会显示成"8 小时前"。
    """
    from sqlalchemy import select

    from app.db.models import SymbolDirectory
    from app.db.session import get_sessionmaker, init_db

    await init_db()
    naive = datetime(2026, 1, 2, 3, 4, 5)  # 故意写入 naive 值
    async with get_sessionmaker()() as session:
        session.add(
            SymbolDirectory(
                symbol="000998.SZ", code="000998", name="时区测试", updated_at=naive
            )
        )
        await session.commit()

    async with get_sessionmaker()() as session:
        row = await session.scalar(
            select(SymbolDirectory).where(SymbolDirectory.symbol == "000998.SZ")
        )
        assert row is not None
        assert row.updated_at.tzinfo is not None, "读出来必须带时区"
        assert row.updated_at.utcoffset() == timedelta(0), "必须是 UTC"
        # naive 按 UTC 解释，不该被当成本地时间挪动 8 小时
        assert row.updated_at.replace(tzinfo=None) == naive
        await session.delete(row)
        await session.commit()


def test_api_serializes_datetimes_with_offset(client) -> None:  # type: ignore[no-untyped-def]
    """接口返回的时间戳必须带偏移，否则前端算相对时间必错。"""
    run_id = client.post(
        "/api/v1/analysis/runs", json={"symbol": "600519", "horizon": "短线"}
    ).json()["run_id"]
    created = client.get(f"/api/v1/analysis/runs/{run_id}").json()["created_at"]
    assert created.endswith("+00:00") or created.endswith("Z"), created

    st = client.get("/api/v1/market/symbols/status").json()
    if st["updated_at"]:
        assert st["updated_at"].endswith("+00:00") or st["updated_at"].endswith("Z")
