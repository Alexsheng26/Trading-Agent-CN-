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

**实时价**：新浪的 hq.sinajs.cn 不需要 token、一个请求能取多只，股票和 ETF 都走它
（见 app/data/sina_hq.py）。东财返回空、雪球要 token 的机器上，盘中价格就靠它。

**ETF**：两个源都能提供 ETF 行情，新浪还能提供 ETF 日线和全部 ETF 列表。
ETF 的实时价先走 hq.sinajs.cn，取不到再退回新浪那张"全部 ETF 列表"
（一个请求带齐所有 ETF 的价格）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

from app.core.clock import now_utc
from app.core.errors import ProviderUnavailable
from app.data import sina_hq
from app.data.base import (
    CAP_BARS,
    CAP_ETF_BARS,
    CAP_ETF_LIST,
    CAP_ETF_MINUTE_BARS,
    CAP_ETF_QUOTE,
    CAP_INDEXES,
    CAP_MINUTE_BARS,
    CAP_QUOTE,
    CAP_SEARCH,
    Bar,
    IndexQuote,
    Quote,
    StockBasic,
)
from app.data.symbols import Symbol, try_normalize

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
    # 雪球的行情接口同样支持场内基金（akshare 文档写明"A 股场内基金代码"）
    capabilities = frozenset({CAP_QUOTE, CAP_ETF_QUOTE})

    async def quote(self, symbol: Symbol) -> Quote:
        from app.core.config import get_settings  # noqa: PLC0415 —— 避免循环导入
        from app.core.runtime import restart_hint  # noqa: PLC0415

        token = get_settings().xueqiu_token.strip()
        kwargs: dict[str, Any] = {"symbol": symbol.xueqiu}
        if token:
            kwargs["token"] = token

        try:
            df = await self._call("stock_individual_spot_xq", **kwargs)
        except ProviderUnavailable as exc:
            # 雪球未登录时返回 400016。原样透出去用户看不懂该干什么，
            # 这里补一句怎么配。
            if not token and ("400016" in str(exc) or "token" in str(exc).lower()):
                raise ProviderUnavailable(
                    "雪球行情需要登录凭证：浏览器打开 xueqiu.com，F12 → 应用 → Cookie，"
                    f"复制 xq_a_token 的值，填到 .env 的 XUEQIU_TOKEN，然后{restart_hint()}。"
                    "不配也没关系，行情会自动降级到后面的数据源。"
                ) from exc
            raise
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
            # ETF 没有估值。雪球对基金偶尔也回这两个字段（通常为空），一律不用
            pe_ttm=None if symbol.is_etf else _num(f.get("市盈率(TTM)")),
            pb=None if symbol.is_etf else _num(f.get("市净率")),
            total_market_cap=_num(f.get("资产净值/总市值", f.get("总市值"))),
            float_market_cap=_num(f.get("流通值")),
            updated_at=now_utc(),
        )


class SinaProvider(_AkshareBacked):
    """新浪的实时价、日线、大盘指数与全市场列表。主机是 *.sina.com.cn，和东方财富无关。"""

    name = "sina"
    capabilities = frozenset(
        {
            CAP_QUOTE,
            CAP_BARS,
            CAP_MINUTE_BARS,
            CAP_INDEXES,
            CAP_SEARCH,
            CAP_ETF_QUOTE,
            CAP_ETF_BARS,
            CAP_ETF_MINUTE_BARS,
            CAP_ETF_LIST,
        }
    )

    def __init__(self) -> None:
        super().__init__()
        # 同一时刻的多只行情合并成一个请求（首页自选股是并发取的）
        self._hq = sina_hq.Batcher()

    #: 首页展示的指数。新浪用的代码前面带市场前缀
    _WANTED_INDEXES = {
        "sh000001": ("000001", "上证指数"),
        "sz399001": ("399001", "深证成指"),
        "sz399006": ("399006", "创业板指"),
        "sh000300": ("000300", "沪深300"),
        "sh000688": ("000688", "科创50"),
    }

    async def search(self, keyword: str, limit: int = 20) -> list[StockBasic]:
        """按代码或名称搜全市场。

        ⚠️ **只适合后台建代码目录用。** 这个接口要翻 60 多页
        （虽然页间不 sleep，仍需几十秒），而且 akshare 的文档明确写着
        "大量采集会被新浪封禁 IP"。所以：
        - 请求路径上的搜索走本地代码目录（services/symbol_index.py），不碰它；
        - 目录一天建一次，正好是它合适的用法。
        """
        df = await self._call("stock_zh_a_spot")
        if df is None or df.empty:
            return []

        keyword = keyword.strip()
        out: list[StockBasic] = []
        for _, row in df.iterrows():
            # 新浪的代码带市场前缀（sh600519），try_normalize 能认
            raw_code = str(row.get("代码") or "")
            name = str(row.get("名称") or "")
            if keyword and keyword not in raw_code and keyword not in name:
                continue
            sym = try_normalize(raw_code)
            if sym is None:
                continue
            out.append(
                StockBasic(
                    symbol=sym.canonical,
                    name=name,
                    exchange=sym.exchange,
                    board=sym.board,
                )
            )
            if len(out) >= limit:
                break
        return out

    async def daily_bars(
        self, symbol: Symbol, start: date, end: date, adjust: str = "qfq"
    ) -> list[Bar]:
        if symbol.is_etf:
            return await self._etf_bars(symbol, start, end)
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
                    change_pct=None,
                    turnover_rate=_num(row.get("turnover")),
                )
            )
        # 新浪这个接口不给涨跌幅。不补的话，简报里"最近 20 日量价"那张表
        # 的涨跌幅一栏整列都是"数据缺失"，模型看不到每天的涨跌
        return _fill_change_pct(bars)

    async def minute_bars(self, symbol: Symbol, period: int, start: date, end: date) -> list[Bar]:
        """新浪分钟线（股票、ETF 同一个接口，最近约 1970 根）。

        不复权：akshare 的复权要再把全部日线拉一遍、逐日折算，太慢。2 小时图看的是
        最近一两个月，期间赶上除权的概率不大；赶上了会在那天看到一个缺口。
        东财通的时候走东财（前复权），这里只是备份。
        """
        df = await self._call(
            "stock_zh_a_minute", symbol=symbol.sina, period=str(period), adjust=""
        )
        if df is None or df.empty:
            return []
        bars: list[Bar] = []
        for _, row in df.iterrows():
            t = _as_datetime(row.get("day"))
            close = _num(row.get("close"))
            if t is None or close is None or not start <= t.date() <= end:
                continue
            bars.append(
                Bar(
                    date=t.date(),
                    time=t,
                    open=_num(row.get("open")) or close,
                    high=_num(row.get("high")) or close,
                    low=_num(row.get("low")) or close,
                    close=close,
                    volume=_num(row.get("volume")) or 0.0,
                    amount=_num(row.get("amount")) or 0.0,
                )
            )
        bars.sort(key=lambda b: b.time or datetime.min)
        return _fill_change_pct(bars)

    # ---------------- ETF ----------------

    #: ETF 列表缓存多久（秒）。它一次带齐所有 ETF 的实时价，
    #: 前端每 5 秒轮询一次，10 秒内的请求共用一次下载
    _ETF_TABLE_TTL = 10

    async def quote(self, symbol: Symbol) -> Quote:
        """实时行情。股票和 ETF 都先走 hq.sinajs.cn；ETF 取不到再查全部 ETF 列表。

        hq 那边没有换手率和估值，这几项留空 —— 宁缺毋滥。
        """
        hq_error: ProviderUnavailable | None = None
        try:
            fields = await self._hq.get(symbol.sina)
        except ProviderUnavailable as exc:
            hq_error, fields = exc, None
        if fields is not None:
            quote = sina_hq.to_quote(symbol, fields)
            if quote is not None:
                return quote

        if symbol.is_etf:
            return await self._etf_list_quote(symbol)
        if hq_error is not None:
            raise hq_error
        raise ProviderUnavailable(f"新浪实时行情里没有 {symbol.canonical}（可能停牌或已退市）")

    async def _etf_list_quote(self, symbol: Symbol) -> Quote:
        """从全部 ETF 列表里取一行。hq 接口取不到 ETF 时的退路。"""
        row = (await self._etf_table()).get(symbol.sina)
        if row is None:
            raise ProviderUnavailable(f"新浪 ETF 列表里没有 {symbol.canonical}")

        price = _num(row.get("最新价"))
        prev_close = _num(row.get("昨收"))
        if price is None or price <= 0:
            price = prev_close  # 停牌或开盘前现价为 0，用昨收兜底
        if price is None:
            raise ProviderUnavailable(f"新浪没返回 {symbol.canonical} 的价格")
        change_pct = _num(row.get("涨跌幅"))
        if change_pct is None and prev_close:
            change_pct = round((price - prev_close) / prev_close * 100, 2)

        return Quote(
            symbol=symbol.canonical,
            name=str(row.get("名称") or ""),
            price=price,
            prev_close=prev_close,
            open=_num(row.get("今开")),
            high=_num(row.get("最高")),
            low=_num(row.get("最低")),
            change_pct=change_pct,
            volume=_num(row.get("成交量")),
            amount=_num(row.get("成交额")),
            updated_at=now_utc(),
        )

    async def etf_list(self) -> list[StockBasic]:
        out: list[StockBasic] = []
        for code, row in (await self._etf_table()).items():
            sym = try_normalize(code)
            if sym is None or not sym.is_etf:
                continue
            out.append(
                StockBasic(
                    symbol=sym.canonical,
                    name=str(row.get("名称") or ""),
                    exchange=sym.exchange,
                    board=sym.board,
                )
            )
        return out

    async def _etf_table(self) -> dict[str, dict[str, Any]]:
        """全部 ETF 的行情表，按新浪代码（sh512480）索引。单请求。"""
        from app.data.cache import cache  # noqa: PLC0415

        async def load() -> dict[str, dict[str, Any]]:
            df = await self._call("fund_etf_category_sina", symbol="ETF基金")
            if df is None or df.empty:
                raise ProviderUnavailable("新浪 ETF 列表为空")
            return {
                str(r.get("代码") or "").strip().lower(): r.to_dict() for _, r in df.iterrows()
            }

        return await cache.get_or_set("sina:etf_table", self._ETF_TABLE_TTL, load)

    async def _etf_bars(self, symbol: Symbol, start: date, end: date) -> list[Bar]:
        """ETF 日线。

        ⚠️ 新浪给的是**不复权**价格。ETF 分红通常很小，但份额折算（拆分）
        会让价格一夜之间变成一半 —— 技术指标会把它当成暴跌。
        所以只保留最近一次异常跳变之后的数据：K 线短一些，但每根都可比。
        """
        df = await self._call("fund_etf_hist_sina", symbol=symbol.sina)
        if df is None or df.empty:
            return []
        bars: list[Bar] = []
        for _, row in df.iterrows():
            d = _as_date(row.get("date"))
            close = _num(row.get("close"))
            if d is None or close is None or not (start <= d <= end):
                continue
            bars.append(
                Bar(
                    date=d,
                    open=_num(row.get("open")) or close,
                    high=_num(row.get("high")) or close,
                    low=_num(row.get("low")) or close,
                    close=close,
                    volume=_num(row.get("volume")) or 0.0,
                    # 这个接口不给成交额
                    amount=0.0,
                    change_pct=None,
                )
            )
        return _fill_change_pct(_drop_before_last_jump(bars, symbol))

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

#: 单日涨跌超过这个比例就不可能是正常交易（A股 / ETF 涨跌幅上限是 20%，北交所 30%
#: 但北交所没有 ETF），只能是拆分、折算这类公司行为造成的价格断层
_JUMP_THRESHOLD = 0.25


def _drop_before_last_jump(bars: list[Bar], symbol: Symbol) -> list[Bar]:
    for i in range(len(bars) - 1, 0, -1):
        prev = bars[i - 1].close
        if prev > 0 and abs(bars[i].close / prev - 1) > _JUMP_THRESHOLD:
            log.warning(
                "%s 在 %s 出现 %.0f%% 的价格断层（不复权数据里的份额折算），"
                "只保留此后的 %d 根 K 线",
                symbol.canonical,
                bars[i].date,
                (bars[i].close / prev - 1) * 100,
                len(bars) - i,
            )
            return bars[i:]
    return bars


def _fill_change_pct(bars: list[Bar]) -> list[Bar]:
    """按前一根收盘价补上缺失的涨跌幅。第一根没有前值，保持空。"""
    for prev, cur in zip(bars, bars[1:], strict=False):
        if cur.change_pct is None and prev.close:
            cur.change_pct = round((cur.close / prev.close - 1) * 100, 2)
    return bars


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


def _as_datetime(value: Any) -> datetime | None:
    """"2026-10-09 10:30:00" -> datetime。只有日期没有时刻的不算（分钟线必须有时刻）。"""
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
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
