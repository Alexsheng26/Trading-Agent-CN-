"""不走东方财富的备份数据源：雪球（行情）与新浪（K线、指数）。

**为什么需要它们**

用户机器上的诊断结果是这样的：

    新闻       正常        （search-api-web.eastmoney.com）
    实时行情    超时/失败    （push2.eastmoney.com）
    日线       失败        （push2his.eastmoney.com）
    大盘指数    失败        （33.push2.eastmoney.com）

同一个域名下的 `search-api-web` 通、`push2*` 全不通，而且换成浏览器 TLS
指纹之后依旧不通（只是从"被掐断"变成"卡住到超时"）—— 说明问题在
**`push2*.eastmoney.com` 这一组主机本身不可达**，不是客户端指纹。
这种情况在国内的代理软件、企业网络、某些运营商线路上并不罕见。

所以这里补上两个**主机完全无关**的源，让 registry 能按接口降级过去：

    xueqiu  ->  xueqiu.com              单只行情（一个请求就带齐价格/估值/市值）
    sina    ->  finance.sina.com.cn     日线
                vip.stock.finance.sina.com.cn   大盘指数

推荐顺序：`DATA_PROVIDER_ORDER=akshare,xueqiu,sina,tushare`
—— 东财通就用东财（字段最全），不通就自动落到这两个，再不行才是 Tushare。

底层仍然借 akshare 的函数（它已经把这些站点的解析写好了），
所以需要装 akshare；也同样受益于浏览器指纹那层替换。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from app.core.clock import now_utc
from app.core.errors import ProviderUnavailable
from app.data.base import (
    CAP_BARS,
    CAP_INDEXES,
    CAP_QUOTE,
    Bar,
    IndexQuote,
    Quote,
)
from app.data.symbols import Symbol

log = logging.getLogger(__name__)


class _AkshareBacked:
    """借用 akshare 函数的基类：延迟导入 + 线程池 + 浏览器指纹。"""

    name = "override-me"

    def __init__(self) -> None:
        self._ak: Any | None = None

    def _module(self) -> Any:
        if self._ak is None:
            try:
                import akshare  # noqa: PLC0415 —— 延迟导入
            except ImportError as exc:  # pragma: no cover - 取决于安装情况
                raise ProviderUnavailable(
                    f'{self.name} 依赖 akshare（它封装了对应站点的解析），'
                    '执行 pip install -e ".[akshare]"'
                ) from exc
            self._ak = akshare
            self._enable_browser_transport()
        return self._ak

    @staticmethod
    def _enable_browser_transport() -> None:
        from app.core.config import get_settings  # noqa: PLC0415
        from app.data.browser_transport import enable  # noqa: PLC0415

        settings = get_settings()
        if settings.akshare_browser_transport:
            enable(settings.akshare_impersonate)

    async def _call(self, func_name: str, /, **kwargs: Any) -> Any:
        ak = self._module()
        func = getattr(ak, func_name, None)
        if func is None:
            raise ProviderUnavailable(f"当前 akshare 版本没有 {func_name}()，请升级 akshare")
        try:
            return await asyncio.to_thread(lambda: func(**kwargs))
        except Exception as exc:
            raise ProviderUnavailable(f"akshare.{func_name}() 调用失败：{exc}") from exc

    async def healthcheck(self) -> bool:
        try:
            self._module()
        except ProviderUnavailable:
            return False
        return True


class XueqiuProvider(_AkshareBacked):
    """雪球单只行情。

    一个请求就带齐价格、开高低收、成交、换手、市盈率、市净率、市值 ——
    比东财那边要拼三个接口（盘口 + 个股信息 + 估值）省事。
    主机是 xueqiu.com，和东方财富完全无关。
    """

    name = "xueqiu"
    capabilities = frozenset({CAP_QUOTE})

    async def quote(self, symbol: Symbol) -> Quote:
        df = await self._call("stock_individual_spot_xq", symbol=symbol.xueqiu)
        if df is None or df.empty:
            raise ProviderUnavailable(f"雪球没有 {symbol.canonical} 的行情")

        # 该接口返回 item/value 两列的竖表
        f = {str(r["item"]): r["value"] for _, r in df.iterrows()}

        price = _num(f.get("现价"))
        prev_close = _num(f.get("昨收"))
        if price is None or price <= 0:
            price = prev_close  # 停牌时现价可能为 0，用昨收兜底
        if price is None:
            raise ProviderUnavailable(f"雪球没返回 {symbol.canonical} 的价格")

        change_pct = _num(f.get("涨幅"))
        if change_pct is None and prev_close:
            change_pct = round((price - prev_close) / prev_close * 100, 2)

        return Quote(
            symbol=symbol.canonical,
            name=str(f.get("名称") or ""),
            price=price,
            prev_close=prev_close,
            open=_num(f.get("今开")),
            high=_num(f.get("最高")),
            low=_num(f.get("最低")),
            change_pct=change_pct,
            volume=_num(f.get("成交量")),
            amount=_num(f.get("成交额")),
            # 雪球把换手率叫"周转率"
            turnover_rate=_num(f.get("周转率", f.get("换手率"))),
            pe_ttm=_num(f.get("市盈率(TTM)")),
            pb=_num(f.get("市净率")),
            total_market_cap=_num(f.get("资产净值/总市值", f.get("总市值"))),
            float_market_cap=_num(f.get("流通值")),
            updated_at=now_utc(),
        )


class SinaProvider(_AkshareBacked):
    """新浪的日线与大盘指数。主机是 finance.sina.com.cn，和东方财富无关。"""

    name = "sina"
    capabilities = frozenset({CAP_BARS, CAP_INDEXES})

    #: 首页展示的指数。新浪用的代码前面带市场前缀
    _WANTED_INDEXES = {
        "sh000001": ("000001", "上证指数"),
        "sz399001": ("399001", "深证成指"),
        "sz399006": ("399006", "创业板指"),
        "sh000300": ("000300", "沪深300"),
        "sh000688": ("000688", "科创50"),
    }

    async def daily_bars(
        self, symbol: Symbol, start: date, end: date, adjust: str = "qfq"
    ) -> list[Bar]:
        df = await self._call(
            "stock_zh_a_daily",
            symbol=symbol.sina,
            start_date=start.strftime("%Y%m%d"),
            end_date=end.strftime("%Y%m%d"),
            adjust="qfq" if adjust == "qfq" else "",
        )
        if df is None or df.empty:
            return []

        bars: list[Bar] = []
        for _, row in df.iterrows():
            d = _as_date(row.get("date"))
            close = _num(row.get("close"))
            if d is None or close is None:
                continue
            bars.append(
                Bar(
                    date=d,
                    open=_num(row.get("open")) or close,
                    high=_num(row.get("high")) or close,
                    low=_num(row.get("low")) or close,
                    close=close,
                    volume=_num(row.get("volume")) or 0.0,
                    amount=_num(row.get("amount")) or 0.0,
                    # 新浪这个接口不给涨跌幅，留空 —— 指标层会自己按收盘价算
                    change_pct=None,
                    turnover_rate=_num(row.get("turnover")),
                )
            )
        return bars

    async def index_quotes(self) -> list[IndexQuote]:
        df = await self._call("stock_zh_index_spot_sina")
        if df is None or df.empty:
            return []

        out: list[IndexQuote] = []
        for _, row in df.iterrows():
            raw_code = str(row.get("代码") or row.get("code") or "").strip().lower()
            spec = self._WANTED_INDEXES.get(raw_code)
            if spec is None:
                continue
            code, fallback_name = spec
            price = _num(row.get("最新价", row.get("trade")))
            out.append(
                IndexQuote(
                    code=code,
                    name=str(row.get("名称") or row.get("name") or fallback_name),
                    price=price or 0.0,
                    change_pct=_num(row.get("涨跌幅", row.get("changepercent"))),
                )
            )
        if not out:
            raise ProviderUnavailable("新浪指数列表里没有需要的指数")
        return out


# ---------------------------------------------------------------- 小工具


def _num(value: Any) -> float | None:
    """转 float。拿不到就是 None —— 不要用 0 冒充，0 会被当成真实数值。"""
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in ("", "-", "--", "nan", "None", "null"):
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value)[:10].replace("/", "-")
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None
