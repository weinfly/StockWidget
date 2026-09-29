# -*- coding: utf-8 -*-
"""
分时数据获取（腾讯分钟接口，仅沪深京 A 股）。

接口: https://web.ifzq.gtimg.cn/appstock/app/minute/query?code=sh600519
分钟行格式（真机校准 2026-09）: "HHMM 价格 累计量(手) 累计成交额(元)"，
北交所部分行只有 3 段（缺累计额），解析必须容忍。
qt 快照: qt[1]=名称 qt[3]=现价 qt[4]=昨收 qt[5]=今开。
接口会带 15:01-15:30 盘后定价分钟的点，画图只取 09:30-11:30 / 13:01-15:00。
"""

import json

import requests
from PySide6.QtCore import QThread, Signal

MINUTE_URL = "https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}"
_UA = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}

# 标准分时横轴槽位: 上午 09:30-11:30 共 121 个点, 下午 13:01-15:00 共 120 个点
_SLOTS = (
    ["%02d%02d" % (9 + (30 + m) // 60, (30 + m) % 60) for m in range(121)]
    + ["%02d%02d" % (13 + (m - 1) // 60, (m - 1) % 60) for m in range(1, 121)]
)
_SLOT_INDEX = {t: i for i, t in enumerate(_SLOTS)}
SLOT_COUNT = len(_SLOTS)  # 241


def is_ashare(code) -> bool:
    """分时仅支持沪深京 A 股（sh/sz/bj 前缀）。"""
    return bool(code) and str(code)[:2] in ("sh", "sz", "bj")


def _f(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def fetch_minute(code: str):
    """抓取并解析某只 A 股当日分时序列。

    成功返回 dict，失败/无数据返回 None:
      slots/prices/avgs/vols: 等长列表，按槽位对齐（量缺失点为 None）
      prev_close / last_price / name / date / code
    """
    try:
        r = requests.get(MINUTE_URL.format(code=code), timeout=8, headers=_UA)
        r.raise_for_status()
        # 响应体是 UTF-8；显式解码避免 requests 猜错编码
        j = json.loads(r.content.decode("utf-8", errors="replace"))
        d = (j.get("data") or {}).get(code) or {}
        block = d.get("data") or {}
        rows = block.get("data")
        if not rows:
            return None
        qt = (d.get("qt") or {}).get(code) or []
        prev_close = _f(qt[4]) if len(qt) > 4 else None
        if not prev_close:
            # 兜底: 用首个分钟价当昨收（涨跌轴退化，但不至于除零）
            prev_close = _f(str(rows[0]).split(" ")[1]) if rows else None
        if not prev_close:
            return None

        prices = [None] * SLOT_COUNT
        avgs = [None] * SLOT_COUNT
        vols = [None] * SLOT_COUNT
        prev_cum_vol = None
        cum_vol = cum_amt = None
        for raw in rows:
            parts = str(raw).split(" ")
            if len(parts) < 2:
                continue
            idx = _SLOT_INDEX.get(parts[0])
            if idx is None:  # 盘后(15:01-15:30)或异常时间点丢弃
                continue
            p = _f(parts[1])
            if p is None:
                continue
            prices[idx] = p
            if len(parts) >= 3:
                cum_vol = _f(parts[2])
            if len(parts) >= 4:
                cum_amt = _f(parts[3])
            # 行按时间顺序返回，每分钟量 = 本次累计量 - 上一行累计量
            if cum_vol is not None:
                base = prev_cum_vol if prev_cum_vol is not None else 0.0
                vols[idx] = max(0.0, cum_vol - base)
                prev_cum_vol = cum_vol
            if cum_vol and cum_amt:
                avg = cum_amt / (cum_vol * 100.0)  # 手→股 ×100
                # 均价仅对个股有意义；指数的 成交额/成交量 与点位无关
                # （会得到个位数），用当分钟价做合理性闸(0.5x~2x)，异常则丢弃
                if p and 0.5 * p <= avg <= 2.0 * p:
                    avgs[idx] = avg

        slots, ps, ags, vs = [], [], [], []
        for i, p in enumerate(prices):
            if p is None:
                continue
            slots.append(i)
            ps.append(p)
            ags.append(avgs[i])
            vs.append(vols[i])
        if not ps:
            return None

        last_price = _f(qt[3]) if len(qt) > 3 else None
        if not last_price:
            last_price = ps[-1]
        elif abs(ps[-1] - last_price) > 1e-9:
            # 国内分时惯例：末点钉在现价/收盘价上（收盘集合竞价价
            # 只体现在被丢弃的 15:01+ 盘后行里，15:00 行是旧价）
            ps[-1] = last_price
        return {
            "code": code,
            "name": (qt[1] if len(qt) > 1 else code),
            "date": block.get("date") or "",
            "prev_close": prev_close,
            "last_price": last_price,
            "slots": slots,
            "prices": ps,
            "avgs": ags,
            "vols": vs,
        }
    except Exception:
        return None


class IntradayFetchThread(QThread):
    """后台抓取一次分时数据，避免阻塞 UI 线程。

    信号 fetched 携带 dict（成功）或 None（失败）。线程一次性使用，
    每次刷新由窗口新建，结束后自行退出。"""

    fetched = Signal(object)

    def __init__(self, code, parent=None):
        super().__init__(parent)
        self._code = code

    def run(self):
        self.fetched.emit(fetch_minute(self._code))
