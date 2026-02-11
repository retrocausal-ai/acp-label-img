try:
    from PyQt5.QtGui import *
    from PyQt5.QtCore import *
    from PyQt5.QtWidgets import *
except ImportError:
    from PyQt4.QtGui import *
    from PyQt4.QtCore import *


class ToolBar(QToolBar):

    def __init__(self, title):
        super(ToolBar, self).__init__(title)
        layout = self.layout()
        m = (2, 2, 2, 2)  # Minimal margins for compact layout
        layout.setSpacing(2)  # Minimal spacing between buttons
        layout.setContentsMargins(*m)
        self.setContentsMargins(*m)
        self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)

    def addAction(self, action):
        if isinstance(action, QWidgetAction):
            return super(ToolBar, self).addAction(action)
        btn = ToolButton()
        btn.setDefaultAction(action)
        btn.setToolButtonStyle(self.toolButtonStyle())
        self.addWidget(btn)


class ToolButton(QToolButton):
    """ToolBar companion class which ensures all buttons have the same size."""
    minSize = (45, 45)  # Reduced from (60, 60) for more compact layout

    def __init__(self):
        super(ToolButton, self).__init__()
        # Set compact padding for buttons
        self.setStyleSheet("QToolButton { padding: 2px; }")

    def minimumSizeHint(self):
        ms = super(ToolButton, self).minimumSizeHint()
        w1, h1 = ms.width(), ms.height()
        w2, h2 = self.minSize
        ToolButton.minSize = max(w1, w2), max(h1, h2)
        return QSize(*ToolButton.minSize)
