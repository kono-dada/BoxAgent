"""Carbon 注册单一全局快捷键；不安装键盘监听器。"""

import ctypes as C


class Hotkey:
    def __init__(self, callback):
        carbon = C.CDLL("/System/Library/Frameworks/Carbon.framework/Carbon")
        self.carbon = carbon

        class EventType(C.Structure):
            _fields_ = [("eventClass", C.c_uint32), ("eventKind", C.c_uint32)]

        class HotkeyID(C.Structure):
            _fields_ = [("signature", C.c_uint32), ("id", C.c_uint32)]

        handler_type = C.CFUNCTYPE(C.c_int32, C.c_void_p, C.c_void_p, C.c_void_p)
        self.handler = handler_type(lambda *_: (callback(), 0)[1])
        carbon.GetApplicationEventTarget.restype = C.c_void_p
        carbon.InstallEventHandler.argtypes = [C.c_void_p, handler_type, C.c_uint32,
                                               C.POINTER(EventType), C.c_void_p, C.POINTER(C.c_void_p)]
        carbon.RegisterEventHotKey.argtypes = [C.c_uint32, C.c_uint32, HotkeyID,
                                               C.c_void_p, C.c_uint32, C.POINTER(C.c_void_p)]
        carbon.UnregisterEventHotKey.argtypes = [C.c_void_p]
        carbon.RemoveEventHandler.argtypes = [C.c_void_p]
        self.handler_ref, self.key_ref = C.c_void_p(), C.c_void_p()
        target = carbon.GetApplicationEventTarget()
        kind = EventType(int.from_bytes(b"keyb", "big"), 6)
        result = carbon.InstallEventHandler(target, self.handler, 1, C.byref(kind), None, C.byref(self.handler_ref))
        if result == 0:
            result = carbon.RegisterEventHotKey(49, 4096 | 2048,
                HotkeyID(int.from_bytes(b"BXAG", "big"), 1), target, 0, C.byref(self.key_ref))
        self.registered = result == 0

    def close(self):
        if self.key_ref:
            self.carbon.UnregisterEventHotKey(self.key_ref)
        if self.handler_ref:
            self.carbon.RemoveEventHandler(self.handler_ref)
