# -*- coding: utf-8 -*-
"""
StockWidget
极简透明盯盘 Widget 浮窗，按指定股票代码实时显示行情表格。

包结构（仿上游仓库分层风格）：
- ``stockwidget.ui``    界面层：浮窗、设置面板等 Qt 组件。
- ``stockwidget.core``  功能函数层：表格数据模型与绘制（K线 Delegate）。
- ``stockwidget.app``   应用装配：创建各组件并连接信号，含托盘与配置读写。

官方仓库地址: https://github.com/sbr0574/StockWidget
"""

# ---- 版本与出处信息（供“关于”页、README 等统一引用）----
__version__ = "1.5.1"

# 原始作者与官方仓库（再分发时必须保留，详见 LICENSE / NOTICE）
UPSTREAM_AUTHOR = "sbr0574"
UPSTREAM_REPO = "https://github.com/sbr0574/StockWidget"
UPSTREAM_REPO_GITEE = "https://gitee.com/sbr0574/StockWidget"
LICENSE_NAME = "Apache License 2.0"
LICENSE_URL = "http://www.apache.org/licenses/LICENSE-2.0"
