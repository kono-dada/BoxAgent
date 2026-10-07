"""商店背景随系统外观变化，也确保原生视图离屏验收包含背景。"""

import AppKit as AK


class StoreSurface(AK.NSView):
    def drawRect_(self, rect):
        AK.NSColor.windowBackgroundColor().setFill()
        AK.NSRectFill(rect)
