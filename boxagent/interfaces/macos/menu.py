"""Build the application and status-bar menus for the native host."""

from typing import Protocol

import AppKit as AK


class MenuOwner(Protocol):
    agent_name: str


def install_menus(owner: MenuOwner) -> tuple[AK.NSMenu, AK.NSStatusItem]:
    main_menu = AK.NSMenu.alloc().initWithTitle_("BoxAgent")
    edit_item = AK.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("编辑", None, "")
    edit_menu = AK.NSMenu.alloc().initWithTitle_("编辑")
    for title, action, key in [
        ("撤销", "undo:", "z"), ("剪切", "cut:", "x"),
        ("复制", "copy:", "c"), ("粘贴", "paste:", "v"),
        ("全选", "selectAll:", "a"),
    ]:
        edit_menu.addItem_(
            AK.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key))
    edit_item.setSubmenu_(edit_menu)
    main_menu.addItem_(edit_item)
    AK.NSApplication.sharedApplication().setMainMenu_(main_menu)

    menu = AK.NSMenu.alloc().initWithTitle_("BoxAgent")
    for title, action, symbol, key in [
        ("伙伴控制台…", "openPanel:", "sidebar.left", ""),
        ("新建会话…", "createSession:", "square.and.pencil", "n"),
        ("退出 BoxAgent", "quit:", "power", "q"),
    ]:
        if action == "quit:":
            menu.addItem_(AK.NSMenuItem.separatorItem())
        item = AK.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
        item.setTarget_(owner)
        image = AK.NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, title)
        image.setSize_((16, 16))
        item.setImage_(image)
        menu.addItem_(item)
    status_item = AK.NSStatusBar.systemStatusBar().statusItemWithLength_(
        AK.NSVariableStatusItemLength)
    status_item.button().setImage_(
        AK.NSImage.imageWithSystemSymbolName_accessibilityDescription_("sparkles", "伙伴控制台"))
    status_item.button().setToolTip_(f"{owner.agent_name} · ⌃⌥空格切换麦克风")
    status_item.setMenu_(menu)
    return menu, status_item
