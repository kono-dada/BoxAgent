"""原生形象商店；网络与切图在工作线程，AppKit 更新只在主线程。"""

from concurrent.futures import ThreadPoolExecutor
import io
import logging

import AppKit as AK
import objc
from Foundation import NSData, NSObject, NSURL
from PyObjCTools import AppHelper

from ..appearance.codex_pets import CodexPetsAppearance
from ..config import DEFAULT_PET
from ..desktop import button, label
from .surface import StoreSurface


class PetStoreWindow(NSObject):
    @objc.python_method
    def configure(self, desktop, catalog):
        self.desktop, self.catalog = desktop, catalog
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="BoxAgent-pets")
        self.generation = 0
        self.closed = False
        self.selecting = False
        self.loading = False
        self.selection_message = ""
        self.list_future = None
        self.total = 0
        self.page = 1
        self.items = []
        self.window = AK.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (760, 700)), AK.NSWindowStyleMaskTitled | AK.NSWindowStyleMaskClosable,
            AK.NSBackingStoreBuffered, False)
        self.window.setTitle_("形象商店 · BoxAgent")
        self.window.setReleasedWhenClosed_(False)
        self.window.center()
        root = StoreSurface.alloc().initWithFrame_(((0, 0), (760, 700)))
        self.window.setContentView_(root)
        root.addSubview_(label("选你喜欢的样子", ((24, 650), (440, 30)), 23))
        root.addSubview_(label("来自 Codex Pets 社区 · 下载后可离线使用", ((24, 625), (530, 20)), 12,
                              AK.NSColor.secondaryLabelColor()))
        self.search = AK.NSSearchField.alloc().initWithFrame_(((24, 579), (310, 30)))
        self.search.setPlaceholderString_("搜索名字、动物或角色")
        self.search.setTarget_(self)
        self.search.setAction_("search:")
        self.search.setSendsWholeSearchString_(True)
        root.addSubview_(self.search)
        self.search_button = button("搜索", ((342, 579), (64, 30)), self, "search:")
        root.addSubview_(self.search_button)
        self.filter = AK.NSPopUpButton.alloc().initWithFrame_pullsDown_(((420, 579), (140, 30)), False)
        self.filter.addItemsWithTitles_(["全部形象", "已下载"])
        self.filter.setTarget_(self)
        self.filter.setAction_("search:")
        root.addSubview_(self.filter)
        self.builtin = button("换回小鸭", ((590, 579), (140, 30)), self, "builtin:")
        root.addSubview_(self.builtin)
        self.status = label("正在加载形象…", ((24, 533), (710, 38)), 12, AK.NSColor.secondaryLabelColor())
        self.status.setMaximumNumberOfLines_(2)
        root.addSubview_(self.status)
        self.grid = AK.NSView.alloc().initWithFrame_(((24, 90), (712, 438)))
        root.addSubview_(self.grid)
        self.previous = button("上一页", ((210, 47), (90, 30)), self, "previous:")
        self.next = button("下一页", ((460, 47), (90, 30)), self, "next:")
        self.indicator = label("", ((306, 53), (148, 20)), 13)
        self.indicator.setAlignment_(AK.NSTextAlignmentCenter)
        for view in (self.previous, self.next, self.indicator):
            root.addSubview_(view)
        root.addSubview_(label("点击分享者名称，前往来源页查看作品信息与使用说明。", ((24, 16), (700, 18)), 11,
                              AK.NSColor.secondaryLabelColor()))
        return self

    @objc.python_method
    def show(self):
        self.window.makeKeyAndOrderFront_(None)
        AK.NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        if not self.items:
            self.load()

    @objc.python_method
    def dispatch(self, work, callback):
        future = self.pool.submit(work)
        def finished(done):
            try:
                value, error = done.result(), None
            except Exception as exc:
                if not done.cancelled():
                    logging.getLogger(__name__).exception("形象商店后台请求失败")
                value, error = None, str(exc)
            AppHelper.callAfter(self.deliver, callback, value, error)
        future.add_done_callback(finished)
        return future

    @objc.python_method
    def update_controls(self):
        for control in (self.search, self.search_button, self.filter, self.builtin):
            control.setEnabled_(not self.selecting)
        self.previous.setEnabled_(not self.selecting and not self.loading and self.page > 1)
        self.next.setEnabled_(not self.selecting and not self.loading and self.page * 12 < self.total)

    @objc.python_method
    def deliver(self, callback, value, error):
        if not self.closed:
            callback(value, error)

    @objc.python_method
    def load(self):
        if self.selecting:
            return
        self.generation += 1
        if self.list_future is not None:
            self.list_future.cancel()
        self.loading = True
        self.selection_message = ""
        generation = self.generation
        search, page, installed = self.search.stringValue(), self.page, self.filter.indexOfSelectedItem() == 1
        self.update_controls()
        self.status.setStringValue_("正在加载已下载的形象…" if installed else "正在加载形象…")
        def loaded(result, error):
            if generation != self.generation:
                return
            self.loading = False
            if error:
                self.status.setStringValue_(self.selection_message or "暂时无法加载形象。请再试一次，或切换到「已下载」。")
                self.update_controls()
                return
            self.items, self.page = result["pets"], result["page"]
            self.total = result["total"]
            self.draw()
            empty = ("还没有下载形象。到「全部形象」挑一个吧。" if installed and not search.strip()
                     else "没有找到匹配的形象，试试其他关键词。")
            self.status.setStringValue_(self.selection_message or result["error"] or (
                f"共 {self.total} 个形象" if self.total else empty))
            self.update_controls()
            self.indicator.setStringValue_(f"{self.page} / {max(1, (self.total + 11) // 12)}")
        self.list_future = self.dispatch(lambda: self.catalog.list(search, page, installed), loaded)

    @objc.python_method
    def draw(self):
        for view in list(self.grid.subviews()):
            view.removeFromSuperview()
        self.choose_buttons = []
        selected = self.desktop.appearance.directory.resolve()
        for index, pet in enumerate(self.items):
            x, y = (index % 4) * 180, 292 - (index // 4) * 146
            card = AK.NSView.alloc().initWithFrame_(((x, y), (172, 140)))
            picture = pet.get("picture")
            if picture is not None:
                data = io.BytesIO()
                picture.save(data, format="PNG")
                raw = data.getvalue()
                image = AK.NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(raw, len(raw)))
                view = AK.NSImageView.alloc().initWithFrame_(((36, 62), (100, 78)))
                view.setImageScaling_(AK.NSImageScaleProportionallyUpOrDown)
                view.setImage_(image)
                card.addSubview_(view)
            else:
                card.addSubview_(label("暂无预览", ((40, 84), (100, 20)), 12, AK.NSColor.secondaryLabelColor()))
            name = label(pet["displayName"], ((6, 43), (160, 19)), 12)
            name.setAlignment_(AK.NSTextAlignmentCenter)
            name.setMaximumNumberOfLines_(1)
            name.setLineBreakMode_(AK.NSLineBreakByTruncatingTail)
            name.setToolTip_(pet["displayName"] + "\n" + pet["description"])
            card.addSubview_(name)
            source = button(f'分享者：{pet["ownerName"]} ↗',
                            ((4, 23), (164, 20)), self, "source:")
            source.setBordered_(False)
            source.setFont_(AK.NSFont.systemFontOfSize_(10))
            source.cell().setLineBreakMode_(AK.NSLineBreakByTruncatingTail)
            source.setToolTip_(f'分享者：{pet["ownerName"]} · 查看来源')
            source.setTag_(index)
            card.addSubview_(source)
            current = selected == (self.catalog.root / pet["id"])
            choose = button("正在使用" if current else "使用形象" if pet.get("cached") else "下载并使用",
                            ((28, 0), (116, 25)), self, "choose:")
            choose.setTag_(index)
            choose.setEnabled_(not current and not self.selecting)
            card.addSubview_(choose)
            self.choose_buttons.append(choose)
            self.grid.addSubview_(card)

    def search_(self, _sender):
        self.page = 1
        self.load()

    def previous_(self, _sender):
        self.page = max(1, self.page - 1)
        self.load()

    def next_(self, _sender):
        self.page += 1
        self.load()

    def source_(self, sender):
        AK.NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(self.items[sender.tag()]["sourceUrl"]))

    def choose_(self, sender):
        pet = self.items[sender.tag()]
        self.select(pet["id"], pet["displayName"])

    def builtin_(self, _sender):
        self.select(None, "小鸭")

    @objc.python_method
    def select(self, identity, name):
        if self.selecting:
            return
        self.selecting = True
        self.update_controls()
        self.draw()
        self.selection_message = f"正在准备「{name}」… 完成后会自动切换。"
        self.status.setStringValue_(self.selection_message)
        def prepare():
            directory = self.catalog.prepare(identity) if identity else DEFAULT_PET
            return directory, CodexPetsAppearance.prepare(directory)
        def ready(result, error):
            try:
                if error:
                    raise ValueError(error)
                directory, prepared = result
                appearance = CodexPetsAppearance(directory, prepared)
                self.desktop.replaceAppearance(appearance, self.catalog)
                for pet in self.items:
                    if pet["id"] == identity:
                        pet["cached"] = True
                self.selection_message = f"已换上「{name}」，下次打开仍会使用这个形象。"
            except Exception:
                logging.getLogger(__name__).exception("形象切换未完成，保留原形象")
                self.selection_message = "暂时无法切换形象，仍在使用原来的形象。请重试，或选择其他形象。"
            finally:
                self.status.setStringValue_(self.selection_message)
                self.selecting = False
                self.update_controls()
                self.draw()
        self.dispatch(prepare, ready)

    @objc.python_method
    def shutdown(self):
        self.closed = True
        self.generation += 1
        self.window.orderOut_(None)
        self.pool.shutdown(wait=False, cancel_futures=True)
