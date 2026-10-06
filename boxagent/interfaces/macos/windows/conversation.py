"""Conversation panel primitives and controls."""

import AppKit as AK
import objc

from boxagent.interfaces.macos.windows.pet import PetPanel


class ConversationPanel(PetPanel):
    def canBecomeKeyWindow(self):
        return True


class FlippedView(AK.NSView):
    def isFlipped(self):
        return True


class RoundedSurface(AK.NSView):
    kind = objc.ivar()

    def drawRect_(self, _rect):
        dark = self.effectiveAppearance().bestMatchFromAppearancesWithNames_(
            [AK.NSAppearanceNameAqua, AK.NSAppearanceNameDarkAqua]) == AK.NSAppearanceNameDarkAqua
        radius = 16 if self.kind == "panel" else 10
        path = AK.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            self.bounds(), radius, radius)
        if self.kind == "panel":
            color = (AK.NSColor.colorWithSRGBRed_green_blue_alpha_(.13, .14, .16, 1)
                     if dark else AK.NSColor.colorWithSRGBRed_green_blue_alpha_(.97, .97, .985, 1))
        else:
            color = AK.NSColor.textBackgroundColor()
        color.setFill()
        path.fill()
        if self.kind != "panel":
            AK.NSColor.separatorColor().setStroke()
            path.setLineWidth_(.5)
            path.stroke()

    def viewDidChangeEffectiveAppearance(self):
        self.setNeedsDisplay_(True)


class SendButton(AK.NSButton):
    def drawRect_(self, rect):
        color = (AK.NSColor.colorWithSRGBRed_green_blue_alpha_(.29, .34, .78, 1)
                 if self.isEnabled() else AK.NSColor.quaternaryLabelColor())
        color.setFill()
        AK.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), 8, 8).fill()
        objc.super(SendButton, self).drawRect_(rect)


def label(text, frame, size=13, color=None):
    view = AK.NSTextField.labelWithString_(text)
    view.setFrame_(frame)
    view.setFont_(AK.NSFont.systemFontOfSize_(size))
    view.setTextColor_(color or AK.NSColor.labelColor())
    view.setLineBreakMode_(AK.NSLineBreakByWordWrapping)
    view.setMaximumNumberOfLines_(0)
    return view


def button(title, frame, target, action):
    view = AK.NSButton.buttonWithTitle_target_action_(title, target, action)
    view.setFrame_(frame)
    view.setBezelStyle_(AK.NSBezelStyleRounded)
    return view


def symbol_button(name, description, frame, target, action, button_class=AK.NSButton):
    view = button_class.buttonWithTitle_target_action_("", target, action)
    view.setFrame_(frame)
    view.setBordered_(False)
    view.setImage_(AK.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
        name, description))
    view.setImagePosition_(AK.NSImageOnly)
    view.setToolTip_(description)
    view.setAccessibilityLabel_(description)
    return view
