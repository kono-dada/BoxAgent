"""原生应用菜单、状态栏菜单和桌宠大小滑块。"""

import json

import AppKit as AK
from boxagent.interfaces.macos.pets.size import PET_MIN_SCALE, PET_MAX_SCALE


def pet_size_controls(owner):
    """在菜单里直接拖动滑块，连续发送大小变化。"""
    view = AK.NSView.alloc().initWithFrame_(((0, 0), (280, 112)))

    def label(text, frame):
        field = AK.NSTextField.labelWithString_(text)
        field.setFrame_(frame)
        field.setFont_(AK.NSFont.systemFontOfSize_(12))
        view.addSubview_(field)
        return field

    label("拖动调整大小", ((16, 84), (170, 18)))
    owner.pet_size_label = label(f"{round(owner.pet_scale * 100)}%", ((202, 84), (62, 18)))
    owner.pet_size_label.setAlignment_(AK.NSTextAlignmentRight)
    slider = AK.NSSlider.alloc().initWithFrame_(((16, 48), (248, 28)))
    slider.setMinValue_(PET_MIN_SCALE * 100)
    slider.setMaxValue_(PET_MAX_SCALE * 100)
    slider.setDoubleValue_(owner.pet_scale * 100)
    slider.setContinuous_(True)
    slider.setTarget_(owner)
    slider.setAction_("setPetSize:")
    slider.setAccessibilityLabel_("桌宠大小百分比")
    owner.pet_size_slider = slider
    view.addSubview_(slider)
    label(f"{round(PET_MIN_SCALE * 100)}%", ((16, 28), (52, 18)))
    maximum = label(f"{round(PET_MAX_SCALE * 100)}%", ((212, 28), (52, 18)))
    maximum.setAlignment_(AK.NSTextAlignmentRight)
    reset = AK.NSButton.alloc().initWithFrame_(((91, 4), (98, 24)))
    reset.setTitle_("恢复默认")
    reset.setBezelStyle_(AK.NSBezelStyleRounded)
    reset.setTarget_(owner)
    reset.setAction_("resetPetSize:")
    view.addSubview_(reset)
    return view


def install_menus(owner):
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
    context_item = None
    for title, selector in [
        ("显示／收起对话", "toggleBubble:"),
        ("新建会话…", "createSession:"),
        ("开启／关闭麦克风  ⌃⌥空格", "toggleMic:"),
        ("编辑角色设定…", "editSoul:"),
        ("记忆看板…", "showMemoryDashboard:"),
        ("Skill 管理…", "showSkillManager:"),
        ("打开当前会话最近运行…", "openLatestRun:"),
        ("打开当前会话运行记录…", "openRunRecords:"),
        ("打开诊断日志…", "openDiagnosticLogs:"),
        ("开启屏幕总结", "toggleContext:"),
        ("停止后台任务", "cancelTask:"),
        ("退出 BoxAgent", "quit:"),
    ]:
        item = AK.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, selector, "")
        item.setTarget_(owner)
        menu.addItem_(item)
        if selector == "toggleContext:":
            context_item = item
    local_menu = AK.NSMenu.alloc().initWithTitle_("3D 形象")
    for manifest_file in (owner.pet_catalog.vrm_manifests() if owner.pet_catalog else []):
        try:
            manifest = json.loads(manifest_file.read_text())
            if manifest.get("type") != "vrm":
                continue
            item = AK.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                str(manifest.get("displayName") or manifest_file.parent.name), "useLocalVrm:", "")
            item.setRepresentedObject_(str(manifest_file.parent))
            item.setTarget_(owner)
            local_menu.addItem_(item)
        except (OSError, ValueError, TypeError):
            continue
    if local_menu.numberOfItems():
        item = AK.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("3D 形象", None, "")
        item.setSubmenu_(local_menu)
        menu.insertItem_atIndex_(item, 4)
    size_menu = AK.NSMenu.alloc().initWithTitle_("桌宠大小")
    size_menu.setAutoenablesItems_(False)
    controls = AK.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("调整大小", None, "")
    controls.setView_(pet_size_controls(owner))
    controls.setEnabled_(True)
    size_menu.addItem_(controls)
    size_item = AK.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
        "桌宠大小", None, "")
    size_item.setSubmenu_(size_menu)
    menu.insertItem_atIndex_(size_item, 4)
    status_item = AK.NSStatusBar.systemStatusBar().statusItemWithLength_(
        AK.NSVariableStatusItemLength)
    status_item.button().setTitle_("◉")
    status_item.button().setToolTip_(f"{owner.agent_name} · ⌃⌥空格切换麦克风")
    status_item.setMenu_(menu)
    return menu, context_item, status_item
