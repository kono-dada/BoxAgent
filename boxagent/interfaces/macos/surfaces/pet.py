"""原生角色窗口与拖动事件；移动速度用于形象的悬挂反馈。"""

import AppKit as AK
import objc
import time
from boxagent.interfaces.macos.pets.drag_motion import DragMotion


class PetPanel(AK.NSPanel):
    def canBecomeKeyWindow(self):
        return False

    def canBecomeMainWindow(self):
        return False


class DragView(AK.NSView):
    owner = objc.ivar()

    def hitTest_(self, point):
        return self if AK.NSPointInRect(point, self.frame()) else None

    def mouseDown_(self, event):
        self.down = AK.NSEvent.mouseLocation()
        self.origin = self.window().frame().origin
        self.dragged = False
        self.sendInteraction("drag-start")
        self.motion = DragMotion.begin((self.down.x, self.down.y), time.monotonic())

    def mouseDragged_(self, event):
        point = AK.NSEvent.mouseLocation()
        sample = self.motion.move((point.x, point.y), time.monotonic())
        if sample:
            kind, (dx, dy), velocity = sample
            self.dragged = True
            self.sendInteraction(kind, velocity)
            self.window().setFrameOrigin_((self.origin.x + dx, self.origin.y + dy))
            self.owner.positionBubble()

    def mouseUp_(self, event):
        self.sendInteraction("drag-end")
        if not self.dragged:
            self.sendInteraction("tap")
        if self.dragged:
            # 释放后让角色留在当前显示器的可见范围内。
            frame = self.window().frame()
            screen = (self.window().screen() or AK.NSScreen.mainScreen()).visibleFrame()
            x = max(screen.origin.x, min(frame.origin.x, screen.origin.x + screen.size.width - frame.size.width))
            y = max(screen.origin.y, min(frame.origin.y, screen.origin.y + screen.size.height - frame.size.height))
            self.window().setFrameOrigin_((x, y))
            self.owner.positionBubble()
        if not self.dragged:
            self.owner.toggleBubble_(None)
        self.owner.savePosition()

    @objc.python_method
    def sendInteraction(self, kind, velocity=(0, 0)):
        appearance = self.owner.appearance
        if hasattr(appearance, "interact"):
            appearance.interact(kind, velocity)

    def rightMouseDown_(self, event):
        AK.NSMenu.popUpContextMenu_withEvent_forView_(self.owner.menu, event, self)
