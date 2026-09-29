# -*- coding: utf-8 -*-
"""
分时图窗口：QPainter 自绘价格线/均价线/昨收参考线/成交量柱。

风格跟随浮窗配色（前景/涨/跌/背景色由调用方传入），
弹窗内 60 秒自动刷新一次（复用 core.intraday 后台抓取线程）。
"""

from PySide6.QtCore import Qt, QRectF, QTimer, QPoint, QPointF
from PySide6.QtGui import (QColor, QFont, QPainter, QPainterPath, QPen,
                           QLinearGradient)
from PySide6.QtWidgets import QVBoxLayout, QWidget, QLabel

from stockwidget.core.intraday import (
    IntradayFetchThread, SLOT_COUNT,
)

# 网格竖线槽位: 09:30 | 10:30 | 11:30-13:00 中线 | 14:00 | 15:00
_GRID_SLOTS = (0, 60, 120, 180, SLOT_COUNT - 1)
_GRID_LABELS = ("09:30", "10:30", "11:30/13:00", "14:00", "15:00")
# 上午段结束于槽 120(11:30)，下午段 121(13:01)~240(15:00)；中线画在 120.5
_NOON = 120.5

_AVG_COLOR = QColor(235, 180, 60)      # 均价线（黄）


def _fmt_vol(v):
    if v is None:
        return "-"
    if v >= 1e8:
        return "%.2f亿" % (v / 1e8)
    if v >= 1e4:
        return "%.1f万" % (v / 1e4)
    return "%.0f" % v


class IntradayChart(QWidget):
    """分时图绘制区。set_data(dict) 后自动重绘。"""

    def __init__(self, parent=None, fg=None, up=None, down=None, bg=None):
        super().__init__(parent)
        self.fg = QColor(fg) if fg else QColor(220, 220, 220)
        self.up_color = QColor(up) if up else QColor(230, 60, 60)
        self.down_color = QColor(down) if down else QColor(40, 170, 90)
        self.bg = QColor(bg) if bg else QColor(20, 20, 20)
        self.bg.setAlpha(255)  # 绘图区内画布必须实心，不透传浮窗的半透明背景
        self.data = None
        self._hover_pt = None      # (slot, price) 十字光标跟随
        self.setMouseTracking(True)

    # ---- 配色更新（浮窗设置变化时可重设） ----
    def update_scheme(self, fg=None, up=None, down=None, bg=None):
        if fg:
            self.fg = QColor(fg)
        if up:
            self.up_color = QColor(up)
        if down:
            self.down_color = QColor(down)
        if bg:
            self.bg = QColor(bg)
            self.bg.setAlpha(255)
        self.update()

    def set_data(self, data):
        self.data = data
        self._hover_pt = None
        self.update()

    def mouseMoveEvent(self, event):
        if self.data and len(self.data["prices"]) >= 2:
            slot = self._slot_at(event.pos().x())
            if slot is not None:
                self._hover_pt = slot
                self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover_pt = None
        self.update()
        super().leaveEvent(event)

    # ---- 布局换算 ----
    def _plot_rect(self):
        m_l, m_r, m_t, m_b = 62, 62, 10, 22
        w = self.width() - m_l - m_r
        h = self.height() - m_t - m_b
        vol_h = max(18, int(h * 0.22))
        price_h = h - vol_h - 6
        return m_l, m_r, m_t, w, price_h, m_t + price_h + 6, vol_h

    def _slot_at(self, x):
        m_l, m_r, m_t, w, *_ = self._plot_rect()
        if w <= 0:
            return None
        s = (x - m_l) / w * (SLOT_COUNT - 1)
        s = int(round(s))
        return s if 0 <= s < SLOT_COUNT else None

    # ---- 主绘制 ----
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.fillRect(self.rect(), self.bg)

        f = QFont(self.font())
        f.setPointSizeF(max(7.5, f.pointSizeF() * 0.85))
        p.setFont(f)

        if not self.data or not self.data["prices"]:
            p.setPen(self.fg)
            p.drawText(self.rect(), Qt.AlignCenter, "暂无分时数据")
            return

        d = self.data
        prev = d["prev_close"] or 0.0
        m_l, m_r, m_t, w, price_h, vol_y, vol_h = self._plot_rect()
        plotr = QRectF(m_l, m_t, w, price_h)

        # 价格→y 对称量程（围绕昨收上下等距，国内分时惯例）
        # 只按价格线取量程：均价是个股分钟价的加权平均，必落在价格带内；
        # 指数无有效均价（已在上游丢弃），纳入会防止脏数据撑爆纵轴。
        hi = max(d["prices"])
        lo = min(d["prices"])
        rng = max(hi - prev, prev - lo, prev * 0.001, 0.01) * 1.08

        def x_of(slot):
            return m_l + slot / (SLOT_COUNT - 1) * w

        def y_of(price):
            return plotr.bottom() - (price - (prev - rng)) / (2 * rng) * price_h

        grid = QColor(self.fg)
        grid.setAlpha(45)
        axis = QColor(self.fg)
        axis.setAlpha(160)
        pm = p.pen()
        pm.setWidthF(1.0)

        # 网格：竖线 + 时间标签
        for i, s in enumerate(_GRID_SLOTS):
            sx = x_of(_NOON if i == 2 else s) if i == 2 else x_of(s)
            pm.setColor(grid)
            p.setPen(pm)
            p.drawLine(QPointF(sx, m_t), QPointF(sx, vol_y + vol_h))
            pm.setColor(axis)
            p.setPen(pm)
            lbl = _GRID_LABELS[i]
            if i == 0:
                p.drawText(QRectF(sx - 30, m_t + price_h + vol_h + 4, 60, 16),
                           Qt.AlignLeft, lbl)
            elif i == 4:
                p.drawText(QRectF(sx - 30, m_t + price_h + vol_h + 4, 60, 16),
                           Qt.AlignRight, lbl)
            else:
                p.drawText(QRectF(sx - 40, m_t + price_h + vol_h + 4, 80, 16),
                           Qt.AlignCenter, lbl)
        # 横线：价格轴 上/昨收/下
        pm.setColor(grid)
        p.setPen(pm)
        for frac in (0.0, 0.5, 1.0):
            yy = m_t + price_h * frac
            p.drawLine(QPointF(m_l, yy), QPointF(m_l + w, yy))
        # 昨收虚线
        pm.setColor(axis)
        pm.setStyle(Qt.DashLine)
        p.setPen(pm)
        p.drawLine(QPointF(m_l, y_of(prev)), QPointF(m_l + w, y_of(prev)))
        pm.setStyle(Qt.SolidLine)

        # 左右轴标签
        p.setPen(axis)
        p.drawText(QRectF(2, m_t - 8, m_l - 8, 16), Qt.AlignRight,
                   "%.2f" % (prev + rng))
        p.drawText(QRectF(2, m_t + price_h / 2 - 8, m_l - 8, 16), Qt.AlignRight,
                   "%.2f" % prev)
        p.drawText(QRectF(2, m_t + price_h - 8, m_l - 8, 16), Qt.AlignRight,
                   "%.2f" % max(prev - rng, 0))
        pct = rng / prev * 100 if prev else 0.0
        up_c, dn_c = self.up_color, self.down_color
        p.setPen(up_c)
        p.drawText(QRectF(m_l + w + 6, m_t - 8, m_r - 8, 16), Qt.AlignLeft,
                   "+%.2f%%" % pct)
        p.setPen(axis)
        p.drawText(QRectF(m_l + w + 6, m_t + price_h / 2 - 8, m_r - 8, 16),
                   Qt.AlignLeft, "0.00%")
        p.setPen(dn_c)
        p.drawText(QRectF(m_l + w + 6, m_t + price_h - 8, m_r - 8, 16),
                   Qt.AlignLeft, "-%.2f%%" % pct)

        # 价格序列（盘中缺口按最后已知价走平补齐，只画首→末数据点之间）
        px, py = [], []
        first_slot = d["slots"][0]
        last_slot = d["slots"][-1]
        known = d["prices"][0]
        cursor = 1
        px.append(x_of(first_slot))
        py.append(known)
        for slot in range(first_slot + 1, last_slot + 1):
            if cursor < len(d["slots"]) and d["slots"][cursor] == slot:
                known = d["prices"][cursor]
                cursor += 1
            px.append(x_of(slot))
            py.append(known)

        last = d["prices"][-1]
        line_c = up_c if last >= prev else dn_c

        # 面积渐变填充
        if len(px) >= 2:
            path = QPainterPath()
            path.moveTo(px[0], y_of(py[0]))
            for x, v in zip(px[1:], py[1:]):
                path.lineTo(x, y_of(v))
            path.lineTo(px[-1], plotr.bottom())
            path.lineTo(px[0], plotr.bottom())
            path.closeSubpath()
            grad = QLinearGradient(0, m_t, 0, plotr.bottom())
            fill_c = QColor(line_c)
            fill_c.setAlpha(46)
            fill_c2 = QColor(line_c)
            fill_c2.setAlpha(8)
            grad.setColorAt(0.0, fill_c)
            grad.setColorAt(1.0, fill_c2)
            p.fillPath(path, grad)

            # 价格线
            pm.setColor(line_c)
            pm.setWidthF(1.4)
            p.setPen(pm)
            line_path = QPainterPath()
            line_path.moveTo(px[0], y_of(py[0]))
            for x, v in zip(px[1:], py[1:]):
                line_path.lineTo(x, y_of(v))
            p.drawPath(line_path)

        # 均价线（黄）
        ax = [(x_of(s), a) for s, a in zip(d["slots"], d["avgs"]) if a is not None]
        if len(ax) >= 2:
            pm.setColor(_AVG_COLOR)
            pm.setWidthF(1.1)
            p.setPen(pm)
            ap = QPainterPath()
            ap.moveTo(ax[0][0], y_of(ax[0][1]))
            for x, a in ax[1:]:
                ap.lineTo(x, y_of(a))
            p.drawPath(ap)

        # 末点标记 + 现价水平虚线
        if px:
            ex, ey = px[-1], y_of(py[-1])
            p.setPen(QPen(line_c, 1, Qt.DashLine))
            p.drawLine(QPointF(ex, ey), QPointF(m_l + w, ey))
            p.setBrush(line_c)
            p.drawEllipse(QPointF(ex, ey), 2.6, 2.6)

        # 成交量柱（每分钟增量，涨红跌绿按相邻分钟价）
        vmax = max((v for v in d["vols"] if v is not None), default=0.0)
        if vmax > 0:
            bw = max(1.0, w / SLOT_COUNT * 0.86)
            prev_price = None
            for s, v, price in zip(d["slots"], d["vols"], d["prices"]):
                if v is None:
                    continue
                c = line_c
                if prev_price is not None:
                    c = up_c if price >= prev_price else dn_c
                prev_price = price
                bh = max(1.0, v / vmax * (vol_h - 2))
                pm.setColor(c)
                p.setPen(pm)
                bx = x_of(s) - bw / 2
                p.fillRect(QRectF(bx, vol_y + vol_h - bh, bw, bh), c)
            # 量区顶线与最大量标签
            pm.setColor(grid)
            p.setPen(pm)
            p.drawLine(QPointF(m_l, vol_y), QPointF(m_l + w, vol_y))
            p.setPen(axis)
            p.drawText(QRectF(2, vol_y - 9, m_l - 8, 16), Qt.AlignRight,
                       _fmt_vol(vmax))
            p.drawText(QRectF(2, vol_y + vol_h - 8, m_l - 8, 16),
                       Qt.AlignRight, "0")

        # 十字光标
        if self._hover_pt is not None and len(px) >= 2:
            hs = self._slot_at_x(self._hover_pt, m_l, w)
            hv = self._price_at_slot(self._hover_pt)
            if hv is not None:
                pm.setColor(axis)
                p.setPen(pm)
                p.drawLine(QPointF(hs, m_t), QPointF(hs, vol_y + vol_h))
                yy = y_of(hv)
                p.drawLine(QPointF(m_l, yy), QPointF(m_l + w, yy))
                tag = "%s  %.2f" % (self._slot_time(self._hover_pt), hv)
                fm = p.fontMetrics()
                tw = fm.horizontalAdvance(tag) + 10
                tx = min(max(hs - tw / 2, m_l), m_l + w - tw)
                p.fillRect(QRectF(tx, m_t + 2, tw, 18), QColor(self.bg))
                p.setPen(self.fg)
                p.drawText(QRectF(tx, m_t + 2, tw, 18), Qt.AlignCenter, tag)

        p.end()

    # 十字光标辅助
    def _slot_at_x(self, slot, m_l, w):
        return m_l + slot / (SLOT_COUNT - 1) * w

    def _price_at_slot(self, slot):
        d = self.data
        best = None
        for s, pr in zip(d["slots"], d["prices"]):
            if s <= slot:
                best = pr
            else:
                break
        return best

    @staticmethod
    def _slot_time(slot):
        from stockwidget.core.intraday import _SLOTS
        return _SLOTS[min(max(slot, 0), SLOT_COUNT - 1)]


class IntradayWindow(QWidget):
    """分时图弹窗：标题栏 + 头部行情摘要 + 图形区，60 秒自动刷新。"""

    def __init__(self, code, fg=None, up=None, down=None, bg=None, parent=None):
        super().__init__(parent, Qt.Window)
        # 关闭即销毁（调用方通过 destroyed 信号释放引用，下次重新创建）
        self.setAttribute(Qt.WA_DeleteOnClose)
        self._code = code
        self._thread = None
        self.setWindowTitle("分时图")
        self.resize(760, 470)
        self.setMinimumSize(460, 300)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        self.header = QLabel("加载中…")
        hf = QFont(self.font())
        hf.setPointSizeF(hf.pointSizeF() * 1.15)
        self.header.setFont(hf)
        self.header.setTextFormat(Qt.RichText)
        chart_bg = QColor(bg) if bg else QColor(20, 20, 20)
        chart_bg.setAlpha(255)
        self.chart = IntradayChart(fg=fg, up=up, down=down, bg=chart_bg)
        lay.addWidget(self.header)
        lay.addWidget(self.chart, 1)
        self.setStyleSheet("background: %s; color: %s;" % (
            chart_bg.name(), (QColor(fg) if fg else QColor(220, 220, 220)).name()))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(60_000)
        QTimer.singleShot(0, self.refresh)

    def set_code(self, code):
        if code == self._code and self.chart.data:
            return
        self._code = code
        self.chart.set_data(None)
        self.header.setText("加载中…")
        self.refresh()

    def refresh(self):
        # 上一次抓取未结束就跳过本轮，避免线程堆积；已销毁的 C++ 对象防 RuntimeError
        try:
            if self._thread is not None and self._thread.isRunning():
                return
        except RuntimeError:
            self._thread = None
        self._thread = IntradayFetchThread(self._code, self)
        self._thread.fetched.connect(self._on_fetched)
        self._thread.finished.connect(self._release_thread)
        self._thread.start()

    def _release_thread(self):
        th, self._thread = self._thread, None
        if th is not None:
            th.deleteLater()

    def _on_fetched(self, data):
        if not data:
            self.header.setText("<span style='color:#999'>%s 分时数据获取失败</span>"
                                % self._code)
            return
        self.chart.set_data(data)
        self.setWindowTitle("%s %s 分时图" % (data["name"], data["code"]))
        prev = data["prev_close"] or 0
        last = data["last_price"] or 0
        diff = last - prev
        pct = (diff / prev * 100) if prev else 0
        col = "#e63c3c" if diff > 0 else ("#28aa5a" if diff < 0 else "#cccccc")
        sign = "+" if diff > 0 else ""
        self.header.setText(
            "<b>%s</b>&nbsp;&nbsp;<span style='color:%s;font-size:15pt'><b>%s</b></span>"
            "&nbsp;<span style='color:%s'>%s%.2f&nbsp;&nbsp;%s%.2f%%</span>"
            "&nbsp;<span style='color:#888'>昨收 %.2f</span>"
            % (data["name"], col, ("%g" % last), col,
               sign, diff, sign, pct, prev))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
        super().keyPressEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start(60_000)

    def closeEvent(self, event):
        self._timer.stop()
        try:
            if self._thread is not None and self._thread.isRunning():
                self._thread.wait(3000)
        except RuntimeError:
            pass
        super().closeEvent(event)
