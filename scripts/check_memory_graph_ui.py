"""Locked-screen-safe WKWebView smoke test for the local memory graph."""

import json
import math
import traceback
from pathlib import Path

import AppKit as AK
import objc
from Foundation import NSObject, NSURL
from PyObjCTools import AppHelper
from WebKit import WKSnapshotConfiguration, WKWebView, WKWebViewConfiguration


ROOT = Path(__file__).resolve().parents[1]
GRAPH_ROOT = ROOT / "assets/memory-graph"
OUTPUT = ROOT / ".runtime/pet/ui-check/memory-graph-web.png"

SNAPSHOT = {
    "nodes": [
        {"id": "memory-1", "type": "EVENT", "content": "用户喜欢乌龙茶"},
        {"id": "memory-2", "type": "NARRATIVE", "content": "用户偏好简洁回答"},
        {"id": "memory-3", "type": "ENTITY", "content": "桂花乌龙"},
    ],
    "edges": [
        {
            "id": f"relation-{index}",
            "source": ("memory-1", "memory-1", "memory-2")[index % 3],
            "target": ("memory-2", "memory-3", "memory-3")[index % 3],
            "type": ("SEMANTIC", "CAUSAL", "TEMPORAL")[index % 3],
            "subtype": ("RELATED_TO", "LEADS_TO", "PRECEDES")[index % 3],
        }
        for index in range(10)
    ],
    "selected_id": "memory-1",
}


class GraphMessageHandler(NSObject):
    probe = objc.ivar()

    def userContentController_didReceiveScriptMessage_(self, _controller, message):
        body = message.body()
        kind = body.get("type") if hasattr(body, "get") else None
        if kind == "ready":
            self.probe.page_ready()
        elif kind == "select":
            self.probe.selected_node = str(body.get("nodeId") or "")


class MemoryGraphProbe(NSObject):
    web_view = objc.ivar()
    window = objc.ivar()
    handler = objc.ivar()
    selected_node = objc.ivar()
    started = objc.ivar()
    finished = objc.ivar()
    result = objc.ivar()

    @objc.python_method
    def configure(self):
        self.started = False
        self.finished = False
        self.selected_node = ""
        self.result = {}
        configuration = WKWebViewConfiguration.alloc().init()
        self.handler = GraphMessageHandler.alloc().init()
        self.handler.probe = self
        configuration.userContentController().addScriptMessageHandler_name_(
            self.handler, "boxagent")
        self.web_view = WKWebView.alloc().initWithFrame_configuration_(
            ((0, 0), (700, 600)), configuration)
        self.window = AK.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0, 0), (700, 600)), AK.NSWindowStyleMaskBorderless,
            AK.NSBackingStoreBuffered, False)
        self.window.setContentView_(self.web_view)
        # An ordered window lets WebKit composite Canvas while remaining independent
        # from Accessibility, mouse input, the foreground app, and screen capture.
        self.window.orderFront_(None)
        self.web_view.loadFileURL_allowingReadAccessToURL_(
            NSURL.fileURLWithPath_(str(GRAPH_ROOT / "index.html")),
            NSURL.fileURLWithPath_isDirectory_(str(GRAPH_ROOT), True))
        AppHelper.callLater(8, self.timeout)
        return self

    @objc.python_method
    def page_ready(self):
        if self.started:
            return
        self.started = True
        payload = json.dumps(SNAPSHOT, ensure_ascii=False, separators=(",", ":"))
        self.web_view.evaluateJavaScript_completionHandler_(
            f"window.boxagentGraph.setSnapshot({payload})", None)
        AppHelper.callLater(1.5, self.verify)

    @objc.python_method
    def verify(self):
        script = """
        (() => {
          const before = window.boxagentGraph.debug();
          const canvas = document.querySelector('canvas');
          const pixels = canvas.getContext('2d').getImageData(
            0, 0, canvas.width, canvas.height).data;
          let coloredPixels = 0;
          for (let index = 0; index < pixels.length; index += 4) {
            if (pixels[index + 3] &&
                (pixels[index] || pixels[index + 1] || pixels[index + 2])) {
              coloredPixels += 1;
            }
          }
          document.querySelector('[data-kind="CAUSAL"]').click();
          const after = window.boxagentGraph.debug();
          window.webkit.messageHandlers.boxagent.postMessage({
            type: 'select', nodeId: 'memory-2'
          });
          return JSON.stringify({
            before, after, coloredPixels,
            canvas: {
              width: canvas.width, height: canvas.height,
              clientWidth: canvas.clientWidth, clientHeight: canvas.clientHeight,
            },
          });
        })()
        """
        self.web_view.evaluateJavaScript_completionHandler_(
            script, lambda value, error: self.receive_debug(value, error))

    @objc.python_method
    def receive_debug(self, value, error):
        if error:
            self.fail(f"JavaScript verification failed: {error}")
            return
        try:
            report = json.loads(value)
        except Exception:
            self.fail(traceback.format_exc())
            return
        AppHelper.callLater(.2, lambda: self.validate(report))

    @objc.python_method
    def validate(self, report):
        try:
            before = report["before"]
            after = report["after"]
            positions = list(before["positions"].values())
            distances = [
                math.hypot(left["x"] - right["x"], left["y"] - right["y"])
                for index, left in enumerate(positions)
                for right in positions[index + 1:]
            ]
            checks = {
                "local_asset_loaded": before["nodeCount"] == 3,
                "all_edges_loaded": before["edgeCount"] == 10,
                "canvas_has_graph_pixels": report["coloredPixels"] > 5_000,
                "layout_positions_are_finite": all(
                    math.isfinite(axis)
                    for position in positions for axis in position.values()),
                "parallel_edges_do_not_collapse_nodes": min(distances) > 80,
                "causal_filter_hides_three_edges": (
                    len(before["visibleEdgeIds"]) == 10
                    and len(after["visibleEdgeIds"]) == 7),
                "webkit_native_bridge_selects_exact_node": (
                    self.selected_node == "memory-2"),
                "canvas_matches_viewport": (
                    report["canvas"]["clientWidth"] == 700
                    and report["canvas"]["clientHeight"] == 600),
            }
            failed = [name for name, passed in checks.items() if not passed]
            if failed:
                self.fail("failed checks: " + ", ".join(failed), report=report)
                return
            self.web_view.takeSnapshotWithConfiguration_completionHandler_(
                WKSnapshotConfiguration.alloc().init(),
                lambda image, error: self.save(image, error, checks, report))
        except Exception:
            self.fail(traceback.format_exc(), report=report)

    @objc.python_method
    def save(self, image, error, checks, report):
        if error or image is None:
            self.fail(f"WKWebView snapshot failed: {error}")
            return
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        bitmap = AK.NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
        data = bitmap.representationUsingType_properties_(AK.NSBitmapImageFileTypePNG, {})
        if not data.writeToFile_atomically_(str(OUTPUT), True):
            self.fail(f"failed to save {OUTPUT}")
            return
        self.finish({
            "status": "passed",
            "checks": checks,
            "node_count": report["before"]["nodeCount"],
            "edge_count": report["before"]["edgeCount"],
            "visible_edges_after_filter": len(report["after"]["visibleEdgeIds"]),
            "colored_pixels": report["coloredPixels"],
            "screenshot": str(OUTPUT),
        })

    @objc.python_method
    def timeout(self):
        if not self.finished:
            self.fail("WKWebView did not finish within 8 seconds")

    @objc.python_method
    def fail(self, message, *, report=None):
        self.finish({"status": "failed", "error": message, "report": report or {}})

    @objc.python_method
    def finish(self, result):
        if self.finished:
            return
        self.finished = True
        self.result = result
        print(json.dumps(result, ensure_ascii=False), flush=True)
        self.window.orderOut_(None)
        self.web_view.configuration().userContentController().removeScriptMessageHandlerForName_(
            "boxagent")
        AppHelper.stopEventLoop()


def main():
    app = AK.NSApplication.sharedApplication()
    app.setActivationPolicy_(AK.NSApplicationActivationPolicyAccessory)
    probe = MemoryGraphProbe.alloc().init().configure()
    AppHelper.runEventLoop()
    if probe.result.get("status") != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
