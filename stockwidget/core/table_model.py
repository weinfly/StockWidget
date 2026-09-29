from PySide6.QtCore import Qt, QRect, QAbstractTableModel, QModelIndex, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QBrush
from PySide6.QtWidgets import QStyledItemDelegate
import re

# ----- 默认颜色（用于恢复默认）-----
DEFAULT_UP_COLOR = QColor("#ff4500")
DEFAULT_DOWN_COLOR = QColor("#2adb5c")
DEFAULT_TABLE_COLOR = QColor("#FFFFFF")

# ----- 表头点击排序 -----
# 支持排序的列（与上游对齐：现价/涨跌/涨幅/浮盈/成交量/成交额/均价/委比，
# 另补充代码/名称/买一/卖一/日高/日低，列不存在的点击自动忽略）
SORTABLE_HEADERS = ("代码", "名称", "现价", "涨跌值", "涨跌幅", "盈亏",
                    "买一", "卖一", "委比", "成交量", "成交额", "均价",
                    "日高", "日低")

_NUM_RE = re.compile(r"^[-+]?\d+(?:\.\d+)?")


def parse_sort_value(header, text):
    """把显示串解析为可比较的排序键（数值列返回 float，名称/代码列返回字符串）。
    兼容：前缀箭头(↑/↓)、千分位逗号、百分号、万/亿/万亿 单位、空串。"""
    if text is None:
        return None
    s = str(text).strip().replace(",", "")
    if header in ("名称", "代码"):
        return s or None
    # 去掉首尾非数字符号：箭头前缀、+/- 保留给符号解析
    m = re.match(r"^[-+]", s)
    sign = 1.0
    if m:
        if m.group(0) == "-":
            sign = -1.0
        s = s[1:]
    s = re.sub(r"[^0-9.\u4e00-\u9fff]", "", s)
    num_m = _NUM_RE.match(s)
    if not num_m:
        return None
    try:
        v = float(num_m.group(0)) * sign
    except ValueError:
        return None
    if "万亿" in s:
        v *= 1e12
    elif "亿" in s:
        v *= 1e8
    elif "万" in s:
        v *= 1e4
    return v


class SimpleTableModel(QAbstractTableModel):
    """
    主浮窗表格数据与格式
    """
    # 排序状态变化（列标题, 排序Qt态：0不排序/1升序/2降序），供持久化与菜单同步
    sort_changed = Signal(str, int)

    def __init__(self, rows=None, headers=None, align_right_cols=None, parent=None):
        super().__init__(parent)
        self._rows = rows or []
        self._headers = headers or []
        self._align_right = align_right_cols or []
        self.up_color = QColor(DEFAULT_UP_COLOR)
        self.down_color = QColor(DEFAULT_DOWN_COLOR)
        self.table_color = QColor(DEFAULT_TABLE_COLOR)
        # 中性色（sign==0 的行情列）与统一颜色开关（开启后所有文字用 table_color）
        self.neutral_color = QColor(DEFAULT_TABLE_COLOR)
        self.unified_color = False
        self._row_meta = []
        # 行置换排序：_rows[i] = _orig_rows[_perm[i]]，不改动原始数据本身
        self._orig_rows = list(self._rows)
        self._perm = list(range(len(self._rows)))
        self._sort_header = None
        self._sort_order = 0  # 0 不排序 / 1 升序 / 2 降序
        self._orig_meta = []
        self._orig_codes = []
        self._row_codes = []

    def set_color_scheme(self, table: QColor, up: QColor, down: QColor,
                         neutral: QColor = None, unified: bool = None):
        self.table_color = QColor(table)
        self.up_color = QColor(up)
        self.down_color = QColor(down)
        # neutral 缺省沿用主文字色（向后兼容）；unified 为 None 时保持当前开关
        self.neutral_color = QColor(
            neutral) if neutral is not None else QColor(table)
        if unified is not None:
            self.unified_color = bool(unified)

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self._rows[0]) if self._rows else len(self._headers)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        r, c = index.row(), index.column()
        cell = "" if c >= len(self._rows[r]) else self._rows[r][c]

        if role == Qt.UserRole:
            if isinstance(cell, dict) and "k" in cell:
                return cell["k"]
            return None

        if role == Qt.DisplayRole:
            return "" if isinstance(cell, dict) else str(cell)

        if role == Qt.TextAlignmentRole:
            return (Qt.AlignRight | Qt.AlignVCenter) if c in self._align_right else (Qt.AlignLeft | Qt.AlignVCenter)

        if role == Qt.ForegroundRole:
            # 统一颜色：不区分涨/跌/平，全部使用主文字色
            if self.unified_color:
                return self.table_color
            meta = self._row_meta[r] if 0 <= r < len(self._row_meta) else {}
            header = self._headers[c] if 0 <= c < len(self._headers) else ""
            sign = 0
            if header in ("涨跌值", "涨跌幅", "现价"):
                sign = int(meta.get("delta", 0))
            elif header == "委比":
                sign = int(meta.get("commi", 0))
            elif header == "均价":
                sign = int(meta.get("avg", 0))
            elif header == "买一":
                sign = int(meta.get("b1", 0))
            elif header == "卖一":
                sign = int(meta.get("s1", 0))
            elif header == "盈亏":
                sign = int(meta.get("pnl", 0))
            elif header == "日高":
                sign = int(meta.get("high", 0))
            elif header == "日低":
                sign = int(meta.get("low", 0))
            else:
                return self.table_color

            if sign > 0:
                return self.up_color
            if sign < 0:
                return self.down_color
            return self.neutral_color

        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal and 0 <= section < len(self._headers):
            return self._headers[section]
        return None

    def set_rows_headers(self, rows, headers, meta=None, codes=None):
        self.beginResetModel()
        # 防御性拷贝：置换只作用于模型内部副本，不影响调用方原始列表
        self._orig_rows = [list(r) for r in (rows or [])]
        n = len(self._orig_rows)
        # 新数据到达：若排序列已不存在则清除排序，否则保持排序状态重施行置换
        if self._sort_header and self._sort_header not in (headers or []):
            self._sort_header, self._sort_order = None, 0
        if self._sort_order:
            self._perm = self._build_perm(
                headers, self._sort_header, self._sort_order)
        else:
            self._sort_header = None
            self._perm = list(range(n))
        self._rows = [self._orig_rows[i] for i in self._perm]
        self._headers = headers or []
        self._orig_meta = list(meta or [{} for _ in range(n)])
        self._row_meta = [self._orig_meta[i] for i in self._perm]
        if codes is not None:
            self._orig_codes = list(codes)
        elif len(getattr(self, "_orig_codes", [])) != n:
            self._orig_codes = [""] * n
        self._row_codes = [self._orig_codes[i] for i in self._perm]
        self.endResetModel()

    def set_align_right_cols(self, cols_idx):
        self._align_right = set(cols_idx or [])

    # ----- 排序 -----
    def _build_perm(self, headers, header, order):
        """基于原始行顺序构建排序后的行置换。"""
        if header not in headers:
            return list(range(len(self._orig_rows)))
        c = headers.index(header)
        n = len(self._orig_rows)
        idx = list(range(n))

        def cell_key(r):
            row = self._orig_rows[r]
            v = parse_sort_value(header, row[c] if c < len(row) else "")
            # 元组首元素把无值行（如未设置成本的盈亏）恒排最后，
            # 避免 None 与数值比较报 TypeError
            return (v is None, v if v is not None else 0)

        try:
            idx.sort(key=cell_key, reverse=(order == 2))
        except TypeError:
            # 混合类型防御（理论上不会发生）：保持原序
            return list(range(n))
        # 降序时把空值段整体移回末尾（reverse 会把 None 段顶到前面）
        if order == 2:
            empties = [i for i in idx if cell_key(i)[0]]
            if empties:
                nonempty = [i for i in idx if not cell_key(i)[0]]
                idx = nonempty + empties
        return idx

    def sort_state(self):
        return self._sort_header, self._sort_order

    def header_is_sortable(self, header):
        return header in SORTABLE_HEADERS and header in self._headers

    def sort_by_header(self, header, order, notify=True):
        """按列标题排序。order: 1升序 / 2降序 / 0恢复抓取原始顺序。"""
        if order and header not in self._headers:
            return False
        if order:
            self._sort_header, self._sort_order = header, order
        else:
            self._sort_header, self._sort_order = None, 0
        self.layoutAboutToBeChanged.emit()
        # 防御：行数/元数据长度不一致时（如自选增删后尚未刷新）不施行置换
        if len(self._orig_meta) != len(self._orig_rows) or \
                len(self._orig_codes) != len(self._orig_rows):
            self._sort_header, self._sort_order = None, 0
        self._perm = self._build_perm(
            self._headers, self._sort_header, self._sort_order)
        self._rows = [self._orig_rows[i] for i in self._perm]
        self._row_meta = [self._orig_meta[i] for i in self._perm]
        self._row_codes = [self._orig_codes[i] for i in self._perm]
        self.layoutChanged.emit()
        if notify:
            self.sort_changed.emit(self._sort_header or "", self._sort_order)
        return True

    def row_code(self, row):
        """取显示行对应的实际代码（自动跟随排序置换，永不错位）。"""
        if 0 <= row < len(self._row_codes):
            return self._row_codes[row]
        return None

    def cycle_sort(self, header):
        """表头点击循环：降序 -> 升序 -> 不排序（与上游一致）。返回 Qt 排序态（None=不排序）。"""
        if header not in SORTABLE_HEADERS:
            return None
        if self._sort_header == header:
            nxt = {2: 1, 1: 0, 0: 2}[self._sort_order]
        else:
            nxt = 2  # 新列首次点击默认降序
        self.sort_by_header(header, nxt)
        return {1: Qt.AscendingOrder, 2: Qt.DescendingOrder}.get(nxt, None)


class KLineDelegate(QStyledItemDelegate):
    """
    当日K线图，基于昨收，今开，最高，最低，实时价
    """

    def __init__(self, parent=None, base_pt=12):
        super().__init__(parent)
        self.up_color = QColor(DEFAULT_UP_COLOR)
        self.down_color = QColor(DEFAULT_DOWN_COLOR)
        self.table_color = QColor(DEFAULT_TABLE_COLOR)
        self.base_pt = max(1, int(base_pt))
        self.scale = 1.0  # 缩放

    def update_scheme(self, table: QColor, up: QColor, down: QColor):
        self.table_color = QColor(table)
        self.up_color = QColor(up)
        self.down_color = QColor(down)

    def set_point_size(self, pt: int):
        self.scale = max(0.5, min(1.5, float(pt) / float(self.base_pt)))

    def paint(self, painter: QPainter, option, index):
        k = index.data(Qt.UserRole)
        if not k or not isinstance(k, tuple) or len(k) != 5:
            super().paint(painter, option, index)
            return

        o, c, h, l, p = k
        if h < l:
            h, l = l, h

        cell = option.rect
        rect = cell.adjusted(2, 2, -2, -2)

        sc = max(0.5, min(1.5, self.scale))
        vpad = max(2, int(rect.height() * (0.12 + 0.06 * (sc - 1))))   # ~12%~18%
        h_eff = max(2, rect.height() - 2 * vpad)
        krect = QRect(rect.left(), rect.top() + vpad, rect.width(), h_eff)

        def y_for(v):
            if h == l == p:
                y = 0.5
            else:
                y = (v - min(l, p)) / (max(h, p) - min(l, p))
            return krect.top() + (1 - y) * krect.height()

        y_o, y_c, y_h, y_l, y_p = (
            y_for(o), y_for(c), y_for(h), y_for(l), y_for(p))

        painter.save()
        painter.setClipRect(cell)
        painter.setRenderHint(QPainter.Antialiasing, True)

        body_w = max(5, min(int(krect.width() * 0.4 * sc), 10))
        x = krect.center().x()

        # 昨收虚线
        dash_col = QColor(self.table_color)
        dash_col.setAlpha(180)
        painter.setPen(QPen(dash_col, 1, Qt.DashLine))
        painter.drawLine(x - body_w, y_p, x + body_w, y_p)

        if c > o:
            kcolor = self.up_color
        elif c < o:
            kcolor = self.down_color
        else:
            kcolor = self.table_color

        top, bot = min(y_o, y_c), max(y_o, y_c)
        body_h = max(2, bot - top)
        body_x = x - body_w // 2

        painter.setPen(QPen(kcolor, 1))
        if c != o:
            # 实体
            painter.drawRect(body_x, top, body_w, body_h)
        else:
            # 一字实体
            painter.drawLine(body_x, y_c, body_x+body_w, y_c)
        if y_h < top:
            # 上影线
            painter.drawLine(x, y_h, x, top)
        if y_l > bot:
            # 下影线
            painter.drawLine(x, bot, x, y_l)
        if c < o:
            # 填充实体（空阳线）
            painter.fillRect(body_x, top, body_w, body_h, QBrush(kcolor))

        painter.restore()
