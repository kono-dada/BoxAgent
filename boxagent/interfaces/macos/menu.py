"""Build the application and status-bar menus for the native host."""

import AppKit as AK


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
        ("形象商店…", "showPetStore:"),
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
    status_item = AK.NSStatusBar.systemStatusBar().statusItemWithLength_(
        AK.NSVariableStatusItemLength)
    status_item.button().setTitle_("◉")
    status_item.button().setToolTip_(f"{owner.agent_name} · ⌃⌥空格切换麦克风")
    status_item.setMenu_(menu)
    return menu, context_item, status_item
