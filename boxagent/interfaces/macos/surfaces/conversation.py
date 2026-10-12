"""Native conversation surfaces, selectable message cards and controls."""

from collections.abc import Sequence
from typing import TypeAlias

import AppKit as AK
import objc

from boxagent.interfaces.macos.surfaces.pet import PetPanel

Frame: TypeAlias = tuple[tuple[float, float], tuple[float, float]]


def is_dark(view: AK.NSView) -> bool:
    return view.effectiveAppearance().bestMatchFromAppearancesWithNames_(
        [AK.NSAppearanceNameAqua, AK.NSAppearanceNameDarkAqua]) == AK.NSAppearanceNameDarkAqua


def accent_color() -> AK.NSColor:
    return AK.NSColor.colorWithSRGBRed_green_blue_alpha_(.29, .42, .35, 1)


class ConversationPanel(PetPanel):
    def canBecomeKeyWindow(self) -> bool:
        return True


class FlippedView(AK.NSView):
    def isFlipped(self) -> bool:
        return True


class RoundedSurface(AK.NSView):
    kind = objc.ivar()
    owner = objc.ivar()

    def drawRect_(self, _rect: object) -> None:
        dark = is_dark(self)
        radius = 18 if self.kind == "panel" else 12
        bounds = AK.NSInsetRect(self.bounds(), .5, .5)
        path = AK.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, radius, radius)
        colors = {
            "panel": (.12, .15, .14, 1) if dark else (.985, .99, .985, 1),
            "input": (.18, .21, .20, 1) if dark else (1, 1, 1, 1),
            "user": (.21, .29, .25, 1) if dark else (.90, .94, .91, 1),
            "assistant": (.17, .20, .19, 1) if dark else (.955, .97, .96, 1),
            "status": (.16, .18, .17, 1) if dark else (.96, .965, .96, 1),
            "approval": (.28, .24, .16, 1) if dark else (.985, .96, .90, 1),
        }
        AK.NSColor.colorWithSRGBRed_green_blue_alpha_(*colors.get(self.kind, colors["input"])).setFill()
        path.fill()
        if self.kind in {"panel", "input", "approval"}:
            AK.NSColor.separatorColor().setStroke()
            path.setLineWidth_(.5)
            path.stroke()

    def viewDidChangeEffectiveAppearance(self) -> None:
        self.setNeedsDisplay_(True)

    def mouseDown_(self, event: object) -> None:
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        if self.kind == "panel" and point.y > self.bounds().size.height - 62:
            self.window().performWindowDragWithEvent_(event)
            if self.owner is not None:
                origin = self.window().frame().origin
                pet_origin = self.owner.petBubbleFrame().origin
                self.owner.bubble_offset = (origin.x - pet_origin.x, origin.y - pet_origin.y)
        else:
            objc.super(RoundedSurface, self).mouseDown_(event)


class SendButton(AK.NSButton):
    def drawRect_(self, rect: object) -> None:
        color = accent_color() if self.isEnabled() else AK.NSColor.quaternaryLabelColor()
        color.setFill()
        AK.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(self.bounds(), 10, 10).fill()
        objc.super(SendButton, self).drawRect_(rect)


class LatestMessageButton(AK.NSButton):
    def drawRect_(self, rect: object) -> None:
        bounds = AK.NSInsetRect(self.bounds(), 1, 1)
        path = AK.NSBezierPath.bezierPathWithOvalInRect_(bounds)
        accent_color().setFill()
        path.fill()
        AK.NSColor.separatorColor().setStroke()
        path.setLineWidth_(.5)
        path.stroke()
        objc.super(LatestMessageButton, self).drawRect_(rect)


class VoiceToggleButton(AK.NSButton):
    """A visible voice action with an explicit on/off switch."""

    voice_state = objc.ivar()

    def drawRect_(self, _rect: object) -> None:
        dark = is_dark(self)
        enabled = self.isEnabled()
        active = self.voice_state in {"ready", "stopping"}
        bounds = AK.NSInsetRect(self.bounds(), .5, .5)
        background = accent_color() if active else AK.NSColor.colorWithSRGBRed_green_blue_alpha_(
            *((.20, .27, .23, 1) if dark else (.91, .95, .92, 1)))
        path = AK.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(bounds, 9, 9)
        background.setFill()
        path.fill()
        AK.NSColor.colorWithSRGBRed_green_blue_alpha_(.48, .62, .53, .8 if enabled else .35).setStroke()
        path.setLineWidth_(1)
        path.stroke()
        foreground = AK.NSColor.whiteColor() if active else (
            AK.NSColor.colorWithSRGBRed_green_blue_alpha_(.72, .85, .77, 1) if dark else accent_color())
        if not enabled:
            foreground = foreground.colorWithAlphaComponent_(.6)
        image = AK.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "mic.fill" if active else "mic", "全双工语音")
        config = AK.NSImageSymbolConfiguration.configurationWithPointSize_weight_(12, AK.NSFontWeightMedium)
        image = image.imageWithSymbolConfiguration_(config)
        image = image.imageWithTintColor_(foreground)
        image.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
            ((11, 8), (14, 14)), AK.NSZeroRect, AK.NSCompositingOperationSourceOver, 1, True, None)
        title = AK.NSAttributedString.alloc().initWithString_attributes_(self.title(), {
            AK.NSFontAttributeName: AK.NSFont.systemFontOfSize_weight_(11, AK.NSFontWeightMedium),
            AK.NSForegroundColorAttributeName: foreground,
        })
        title.drawAtPoint_((31, (self.bounds().size.height - title.size().height) / 2))
        track = AK.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            ((99, 8), (25, 14)), 7, 7)
        foreground.colorWithAlphaComponent_(.25).setFill()
        track.fill()
        foreground.setFill()
        AK.NSBezierPath.bezierPathWithOvalInRect_(((112 if active else 101, 10), (10, 10))).fill()

    def viewDidChangeEffectiveAppearance(self) -> None:
        self.setNeedsDisplay_(True)


class TranscriptView(FlippedView):
    """Measure real text layout and retain full selectable text in every card."""

    @objc.python_method
    def render(self, sections: Sequence[tuple[str, str]], width: float) -> float:
        previous = getattr(self, "cards", [])
        cards = []
        if not sections:
            for child in list(self.subviews()):
                child.removeFromSuperview()
            self.cards = []
            self.addSubview_(label("我在这里，和我说说吧。", ((0, 0), (width, 24)),
                                   13, AK.NSColor.secondaryLabelColor()))
            self.setFrameSize_((width, 24))
            return 24
        top = 0.0
        for index, (role, text) in enumerate(sections):
            if index < len(previous) and previous[index][:3] == (role, text, width):
                cached = previous[index]
                cached[3].setFrameOrigin_((cached[3].frame().origin.x, top))
                cards.append(cached)
                top += cached[4] + 10
                continue
            user = role == "你"
            status = role in {"状态", "任务", "提示"} or role.startswith("桌面观察")
            card_width = width - 34 if user else width
            text_width = card_width - 28
            body = AK.NSTextView.alloc().initWithFrame_(((14, 12), (text_width, 24)))
            body.setEditable_(False)
            body.setSelectable_(True)
            body.setDrawsBackground_(False)
            body.setTextContainerInset_((0, 0))
            body.textContainer().setLineFragmentPadding_(0)
            body.setHorizontallyResizable_(False)
            body.textContainer().setContainerSize_((text_width, 1000000))
            paragraph = AK.NSMutableParagraphStyle.alloc().init()
            paragraph.setLineSpacing_(3)
            body.textStorage().setAttributedString_(AK.NSAttributedString.alloc().initWithString_attributes_(text, {
                AK.NSFontAttributeName: AK.NSFont.systemFontOfSize_(12 if status else 13),
                AK.NSForegroundColorAttributeName: AK.NSColor.secondaryLabelColor() if status else AK.NSColor.labelColor(),
                AK.NSParagraphStyleAttributeName: paragraph,
            }))
            manager = body.layoutManager()
            manager.ensureLayoutForTextContainer_(body.textContainer())
            text_height = max(18, manager.usedRectForTextContainer_(body.textContainer()).size.height + 2)
            body.setFrameSize_((text_width, text_height))
            height = text_height + 44
            card = RoundedSurface.alloc().initWithFrame_(((width - card_width, top), (card_width, height)))
            card.kind = "user" if user else "approval" if role == "需要授权" else "status" if status else "assistant"
            heading = label(role, ((14, height - 27), (text_width, 16)), 10,
                            AK.NSColor.secondaryLabelColor())
            card.addSubview_(heading)
            card.addSubview_(body)
            self.addSubview_(card)
            cards.append((role, text, width, card, height))
            top += height + 10
        for child in list(self.subviews()):
            if all(child != row[3] for row in cards):
                child.removeFromSuperview()
        self.cards = cards
        height = max(24, top - 10)
        self.setFrameSize_((width, height))
        return height


def label(text: str, frame: Frame, size: float = 13,
          color: AK.NSColor | None = None) -> AK.NSTextField:
    view = AK.NSTextField.labelWithString_(text)
    view.setFrame_(frame)
    view.setFont_(AK.NSFont.systemFontOfSize_(size))
    view.setTextColor_(color or AK.NSColor.labelColor())
    view.setLineBreakMode_(AK.NSLineBreakByWordWrapping)
    view.setMaximumNumberOfLines_(0)
    return view


def button(title: str, frame: Frame, target: object, action: str) -> AK.NSButton:
    view = AK.NSButton.buttonWithTitle_target_action_(title, target, action)
    view.setFrame_(frame)
    view.setBezelStyle_(AK.NSBezelStyleRounded)
    return view


def symbol_button(name: str, description: str, frame: Frame, target: object,
                  action: str, button_class: type[AK.NSButton] = AK.NSButton) -> AK.NSButton:
    view = button_class.buttonWithTitle_target_action_("", target, action)
    view.setFrame_(frame)
    view.setBordered_(False)
    view.setImage_(AK.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, description))
    view.setImagePosition_(AK.NSImageOnly)
    view.setToolTip_(description)
    view.setAccessibilityLabel_(description)
    return view
