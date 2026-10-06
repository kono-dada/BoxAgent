"""Pet window and drag interaction primitives."""

import AppKit as AK
import objc


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

    def mouseDragged_(self, event):
        point = AK.NSEvent.mouseLocation()
        dx, dy = point.x - self.down.x, point.y - self.down.y
        if abs(dx) + abs(dy) > 4:
            self.dragged = True
            self.window().setFrameOrigin_((self.origin.x + dx, self.origin.y + dy))
            self.owner.positionBubble()

    def mouseUp_(self, event):
        if not self.dragged:
            self.owner.toggleBubble_(None)
        self.owner.savePosition()

    def rightMouseDown_(self, event):
        AK.NSMenu.popUpContextMenu_withEvent_forView_(self.owner.menu, event, self)
