import sys
import os
import json
import keyboard
import winreg

from PySide6.QtCore import Qt, QPoint, QSize
from PySide6.QtGui import QAction, QIcon, QPixmap, QPainter, QColor, QPen, QBrush
from PySide6.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QStyle
from stockwidget.ui.widget_panel import FloatLabel
from stockwidget.ui.settings_panel import SettingsDialog

# ----- 程序与资源 -----
APP_NAME = "StockWidget"
APP_ICON_FILE = os.path.join("resources", "icons", "StockWidget.ico")


def resource_path(rel_path):
    # PyInstaller 打包后资源解包到 sys._MEIPASS；源码运行时以项目根目录为基准
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, rel_path)


# ----- 配置存档 -----
# 1. 智能判断当前是 .exe 环境还是 .py 开发环境
if getattr(sys, 'frozen', False):
    # 如果是 PyInstaller 打包后的 .exe 运行，获取 exe 所在的目录
    CONFIG_DIR = os.path.dirname(sys.executable)
else:
    # 如果是直接通过 Python 运行脚本，获取项目根目录（stockwidget 包的上一级）
    CONFIG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 2. 配置文件直接与 exe 放在同一个文件夹下
CONFIG_FILE = os.path.join(CONFIG_DIR, "SW_config.json")


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict):
    if not os.path.exists(CONFIG_DIR):
        os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_FILE)


class App(QApplication):
    def __init__(self, argv):
        super().__init__(argv)
        self.setApplicationName(APP_NAME)
        self.setApplicationDisplayName(APP_NAME)
        self.setQuitOnLastWindowClosed(False)
        icon_path = resource_path(APP_ICON_FILE)
        # load saved icon choice from config
        cfg = load_config()
        icon_choice = cfg.get('app_icon')
        self._app_icon_choice = icon_choice

        def _resolve_icon(choice):
            # choice can be None, 'default', 'std:NAME' or a file path
            if not choice or choice == 'default':
                p = resource_path(APP_ICON_FILE)
                if os.path.exists(p):
                    return QIcon(p)
                return self.style().standardIcon(QStyle.SP_ComputerIcon)
            if isinstance(choice, str) and choice.startswith('std:'):
                key = choice.split(':', 1)[1]
                mapping = {
                    'computer': QStyle.SP_ComputerIcon,
                    'network': QStyle.SP_DriveNetIcon,
                    'folder': QStyle.SP_DirIcon,
                    'file': QStyle.SP_FileIcon,
                    'trash': QStyle.SP_TrashIcon,
                    'desktop': QStyle.SP_DesktopIcon,
                }
                sp = mapping.get(key, QStyle.SP_ComputerIcon)
                return self.style().standardIcon(sp)
            # assume it's a file path
            try:
                if os.path.exists(choice):
                    return QIcon(choice)
            except Exception:
                pass
            return self.style().standardIcon(QStyle.SP_ComputerIcon)

        app_icon = _resolve_icon(icon_choice)
        self.setWindowIcon(app_icon)
        # 保存基础图标以便总盈亏指示灯叠加
        self._base_app_icon = app_icon
        # 总盈亏状态：(total_pnl, has_pnl)
        self._pnl_status = (0.0, False)
        # 托盘 ToolTip 指标文本（制表符分隔）
        self._tooltip_text = ""

        cfg = load_config()
        self.win = FloatLabel(cfg)
        # Apply start-on-boot setting from config
        try:
            self.set_start_on_boot(bool(cfg.get("start_on_boot", False)))
        except Exception:
            pass
        self.win.set_on_change(self.save_now)
        self.win.set_open_settings_callback(self.open_settings)
        self.win.set_notifier_callback(self.notify_user)
        self.win.set_pnl_callback(self.update_tray_pnl_status)
        self.win.set_tooltip_callback(self.update_tray_tooltip)

        self.tray = QSystemTrayIcon(app_icon, self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        menu.addAction(QAction("显示/隐藏 浮窗", self, triggered=self.toggle_win))
        menu.addAction(QAction("设置…", self, triggered=self.open_settings))
        menu.addSeparator()
        menu.addAction(QAction("退出", self, triggered=self.quit_app))
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.show()

        self.settings_dlg = None
        self.win.show()
        self.win.raise_()
        self.win.activateWindow()
        self.win.setFocus(Qt.ActiveWindowFocusReason)
        self.save_now()

    def on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.toggle_win()

    def toggle_win(self):
        if self.win.isVisible():
            self.win.hide()
        else:
            self.win.show()
            self.win.raise_()
            self.win.activateWindow()
            self.win.setFocus(Qt.ActiveWindowFocusReason)
        self.save_now()

    def open_settings(self):
        if self.settings_dlg and self.settings_dlg.isVisible():
            self.settings_dlg.raise_()
            self.settings_dlg.activateWindow()
            return
        self.settings_dlg = SettingsDialog(self.win, None, app=self)
        # 将设置窗口放在屏幕正中
        screen = QApplication.primaryScreen().availableGeometry()
        self.settings_dlg.adjustSize()
        cx = screen.left() + (screen.width() - self.settings_dlg.width()) // 2
        cy = screen.top() + (screen.height() - self.settings_dlg.height()) // 2
        self.settings_dlg.move(QPoint(cx, cy))
        self.settings_dlg.show()
        self.settings_dlg.raise_()
        self.settings_dlg.activateWindow()

    def quit_app(self):
        self.tray.hide()
        self.save_now()
        keyboard.unhook_all_hotkeys()
        sys.exit(0)

    def save_now(self):
        cfg = self.win.current_config()
        # persist selected app icon
        try:
            cfg['app_icon'] = getattr(self, '_app_icon_choice', None)
        except Exception:
            pass
        save_config(cfg)

    def set_app_icon(self, choice):
        """Set application and tray icon. `choice` can be None/'default', 'std:KEY' or a file path."""
        self._app_icon_choice = choice
        # resolve to QIcon

        def _resolve_icon(choice):
            if not choice or choice == 'default':
                p = resource_path(APP_ICON_FILE)
                if os.path.exists(p):
                    return QIcon(p)
                return self.style().standardIcon(QStyle.SP_ComputerIcon)
            if isinstance(choice, str) and choice.startswith('std:'):
                key = choice.split(':', 1)[1]
                mapping = {
                    'computer': QStyle.SP_ComputerIcon,
                    'network': QStyle.SP_DriveNetIcon,
                    'folder': QStyle.SP_DirIcon,
                    'file': QStyle.SP_FileIcon,
                    'trash': QStyle.SP_TrashIcon,
                    'desktop': QStyle.SP_DesktopIcon,
                }
                sp = mapping.get(key, QStyle.SP_ComputerIcon)
                return self.style().standardIcon(sp)
            try:
                if os.path.exists(choice):
                    return QIcon(choice)
            except Exception:
                pass
            return self.style().standardIcon(QStyle.SP_ComputerIcon)

        icon = _resolve_icon(choice)
        # 记录基础图标，后续还需叠加总盈亏指示灯
        self._base_app_icon = icon
        try:
            self.setWindowIcon(icon)
        except Exception:
            pass
        try:
            if hasattr(self, 'tray') and self.tray is not None:
                self._apply_tray_icon_with_pnl()
        except Exception:
            pass

    def notify_user(self, title: str, msg: str):
        """通过系统托盘显示通知（用于封单预警等场景）。"""
        try:
            if hasattr(self, 'tray') and self.tray is not None:
                self.tray.showMessage(str(title or ""), str(msg or ""),
                                      QSystemTrayIcon.Information, 5000)
        except Exception:
            pass

    def update_tray_pnl_status(self, total_pnl: float, has_pnl: bool):
        """接收总盈亏变化，刷新托盘图标的红/绿灯泡指示。"""
        try:
            self._pnl_status = (float(total_pnl), bool(has_pnl))
        except Exception:
            self._pnl_status = (0.0, False)
        try:
            self._apply_tray_icon_with_pnl()
        except Exception:
            pass

    def update_tray_tooltip(self, text: str):
        """接收制表符格式的指标文本，刷新托盘 ToolTip。"""
        try:
            self._tooltip_text = str(text or "")
        except Exception:
            self._tooltip_text = ""
        try:
            self._apply_tray_icon_with_pnl()
        except Exception:
            pass

    def _build_tray_tooltip(self) -> str:
        """拼接托盘 ToolTip：总盈亏行（可选）+ 制表符格式的指标数据。"""
        parts = []
        total_pnl, has_pnl = getattr(self, '_pnl_status', (0.0, False))
        if has_pnl:
            sign = "+" if total_pnl > 0 else ("-" if total_pnl < 0 else "")
            parts.append(f"总盈亏：{sign}{abs(total_pnl):,.2f}")
        text = getattr(self, '_tooltip_text', "") or ""
        if text:
            parts.append(text)
        return "\n".join(parts)

    def _apply_tray_icon_with_pnl(self):
        """根据当前总盈亏状态在基础图标右下角叠加红/绿灯泡。"""
        if not hasattr(self, 'tray') or self.tray is None:
            return
        base_icon = getattr(self, '_base_app_icon', None)
        if base_icon is None:
            return
        total_pnl, has_pnl = getattr(self, '_pnl_status', (0.0, False))
        size = 64
        pm = base_icon.pixmap(QSize(size, size))
        if pm.isNull():
            self.tray.setIcon(base_icon)
            self.tray.setToolTip(self._build_tray_tooltip())
            return
        if not has_pnl:
            # 未配置成本 — 不叠加灯泡
            self.tray.setIcon(base_icon)
            self.tray.setToolTip(self._build_tray_tooltip())
            return
        # 选色：沿用浮窗的涨/跌色（A股惯例：正红负绿）
        try:
            up_color = QColor(self.win.up_color)
            down_color = QColor(self.win.down_color)
        except Exception:
            up_color = QColor(220, 60, 60)
            down_color = QColor(60, 180, 90)
        if total_pnl > 0:
            color = up_color
        elif total_pnl < 0:
            color = down_color
        else:
            color = QColor(160, 160, 160)
        # 绘制叠加小灯泡
        result = QPixmap(pm)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.Antialiasing, True)
        d = int(size * 0.55)
        x = size - d - 2
        y = size - d - 2
        # 白色描边便于在深色图标上识别
        painter.setPen(QPen(QColor(255, 255, 255), 2))
        painter.setBrush(QBrush(color))
        painter.drawEllipse(x, y, d, d)
        painter.end()
        self.tray.setIcon(QIcon(result))
        # 同步更新提示文本
        try:
            self.tray.setToolTip(self._build_tray_tooltip())
        except Exception:
            self.tray.setToolTip(APP_NAME)

    def set_start_on_boot(self, enabled: bool):
        """Enable or disable Windows startup by writing/removing Run key in HKCU."""
        try:
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            name = APP_NAME
            if enabled:
                if getattr(sys, 'frozen', False):
                    cmd = f'"{sys.executable}"'
                else:
                    cmd = f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                        winreg.DeleteValue(key, name)
                except OSError:
                    # value not present
                    pass
        except Exception:
            pass
