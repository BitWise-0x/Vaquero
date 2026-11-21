"""The few AppKit and Foundation calls Qt does not expose, made through the Objective-C runtime with ctypes:
Dock or menu-bar-only presence, the Dock badge, and keeping the Mac awake and out of App Nap during a job.

Every call is a no-op off macOS. The AppKit ones also need the cocoa Qt platform (not offscreen, as in tests)."""

from __future__ import annotations

import ctypes
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from ctypes import c_bool, c_char_p, c_long, c_uint64, c_void_p

log = logging.getLogger("vaquero")

MAC = sys.platform == "darwin"

# NSApplicationActivationPolicy
REGULAR, ACCESSORY = 0, 1
# NSActivityUserInitiated: user-requested work; includes NSActivityIdleSystemSleepDisabled, and keeps App Nap away
ACTIVITY_USER_INITIATED = 0x00FFFFFF | (1 << 20)

if MAC:
    _objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
    ctypes.CDLL("/System/Library/Frameworks/AppKit.framework/AppKit")  # registers NSApplication and friends
    _objc.objc_getClass.restype = c_void_p
    _objc.objc_getClass.argtypes = [c_char_p]
    _objc.sel_registerName.restype = c_void_p
    _objc.sel_registerName.argtypes = [c_char_p]
    _send = ctypes.cast(_objc.objc_msgSend, c_void_p).value
    _objc.objc_autoreleasePoolPush.restype = c_void_p
    _objc.objc_autoreleasePoolPop.argtypes = [c_void_p]


@contextmanager
def _pool() -> Iterator[None]:
    """An autorelease pool: worker threads have none, and the strings and URLs made for a call would leak."""
    token = _objc.objc_autoreleasePoolPush()
    try:
        yield
    finally:
        _objc.objc_autoreleasePoolPop(token)


def _fn(restype: object, *argtypes: object) -> ctypes._CFuncPtr:  # type: ignore[name-defined]
    """objc_msgSend cast to one exact signature: arm64 passes arguments by type, so each shape needs its own."""
    return ctypes.CFUNCTYPE(restype, c_void_p, c_void_p, *argtypes)(_send)


def _cls(name: str) -> int:
    return _objc.objc_getClass(name.encode())


def _sel(name: str) -> int:
    return _objc.sel_registerName(name.encode())


def _nsstring(text: str) -> int:
    return _fn(c_void_p, c_char_p)(_cls("NSString"), _sel("stringWithUTF8String:"), text.encode())


def _app() -> int:
    return _fn(c_void_p)(_cls("NSApplication"), _sel("sharedApplication"))


def set_dock_visible(visible: bool) -> bool:
    """Show the app in the Dock and the app switcher (a regular app) or only in the menu bar (an accessory).
    Returns whether macOS took the change."""
    if not MAC:
        return False
    with _pool():
        ok = bool(_fn(c_bool, c_long)(_app(), _sel("setActivationPolicy:"), REGULAR if visible else ACCESSORY))
    if ok and visible:
        activate()
    return ok


def dock_visible() -> bool:
    return MAC and _fn(c_long)(_app(), _sel("activationPolicy")) == REGULAR


def activate() -> None:
    """Bring the app forward, as a click on its Dock icon would."""
    if not MAC:
        return
    app = _app()
    if _fn(c_bool, c_void_p)(app, _sel("respondsToSelector:"), _sel("activate")):
        _fn(None)(app, _sel("activate"))  # macOS 14+
    else:
        _fn(None, c_bool)(app, _sel("activateIgnoringOtherApps:"), True)


def set_dock_badge(text: str) -> None:
    """The red label on the Dock icon; an empty string removes it."""
    if not MAC:
        return
    with _pool():
        tile = _fn(c_void_p)(_app(), _sel("dockTile"))
        _fn(None, c_void_p)(tile, _sel("setBadgeLabel:"), _nsstring(text) if text else None)


class Activity:
    """Tells macOS a user-started job is running: no idle sleep, no App Nap, until end().

    The NSProcessInfo activity API is what caffeinate and Electron's powerSaveBlocker use underneath."""

    def __init__(self, reason: str) -> None:
        self._token: int | None = None
        if not MAC:
            return
        with _pool():
            info = _fn(c_void_p)(_cls("NSProcessInfo"), _sel("processInfo"))
            token = _fn(c_void_p, c_uint64, c_void_p)(
                info, _sel("beginActivityWithOptions:reason:"), ACTIVITY_USER_INITIATED, _nsstring(reason)
            )
            if token:
                self._token = _fn(c_void_p)(token, _sel("retain"))  # outlives the pool it was returned into

    def end(self) -> None:
        if self._token is None:
            return
        info = _fn(c_void_p)(_cls("NSProcessInfo"), _sel("processInfo"))
        _fn(None, c_void_p)(info, _sel("endActivity:"), self._token)
        _fn(None)(self._token, _sel("release"))
        self._token = None

    @property
    def active(self) -> bool:
        return self._token is not None


def _utf8(nsstring: int | None) -> str:
    if not nsstring:
        return ""
    raw = _fn(c_char_p)(nsstring, _sel("UTF8String"))
    return raw.decode() if raw else ""


def trash(path: str) -> str | None:
    """Move path to the Trash as the Finder does, so it can be put back; returns where it went, None when the volume
    has no Trash (some network shares) or the move failed."""
    if not MAC:
        return None
    with _pool():
        url = _fn(c_void_p, c_void_p)(_cls("NSURL"), _sel("fileURLWithPath:"), _nsstring(path))
        manager = _fn(c_void_p)(_cls("NSFileManager"), _sel("defaultManager"))
        result, error = c_void_p(), c_void_p()
        ok = _fn(c_bool, c_void_p, ctypes.POINTER(c_void_p), ctypes.POINTER(c_void_p))(
            manager, _sel("trashItemAtURL:resultingItemURL:error:"), url, ctypes.byref(result), ctypes.byref(error)
        )
        if not ok:
            reason = _utf8(_fn(c_void_p)(error.value, _sel("localizedDescription"))) if error.value else "unknown"
            log.warning("could not move %s to the Trash: %s", path, reason, extra={"link": Path(path), "link_text": path})
            return None
        return _utf8(_fn(c_void_p)(result.value, _sel("path"))) if result.value else None


# SMAppServiceStatus
LOGIN_OFF, LOGIN_ON, LOGIN_NEEDS_APPROVAL, LOGIN_NOT_FOUND = 0, 1, 2, 3


def _login_service() -> int | None:
    if not MAC:
        return None
    ctypes.CDLL("/System/Library/Frameworks/ServiceManagement.framework/ServiceManagement")
    cls = _cls("SMAppService")
    return _fn(c_void_p)(cls, _sel("mainAppService")) if cls else None


def login_item_status() -> int:
    """Whether the app opens at login (SMAppService, macOS 13+): LOGIN_OFF, LOGIN_ON, LOGIN_NEEDS_APPROVAL (the
    user must allow it in System Settings > General > Login Items) or LOGIN_NOT_FOUND."""
    with _pool():
        service = _login_service()
        return _fn(c_long)(service, _sel("status")) if service else LOGIN_NOT_FOUND


def set_login_item(on: bool) -> str:
    """Register or unregister the app as a login item; returns "" or the reason it failed."""
    with _pool():
        service = _login_service()
        if not service:
            return "login items are not available"
        error = c_void_p()
        selector = "registerAndReturnError:" if on else "unregisterAndReturnError:"
        if _fn(c_bool, ctypes.POINTER(c_void_p))(service, _sel(selector), ctypes.byref(error)):
            return ""
        return _utf8(_fn(c_void_p)(error.value, _sel("localizedDescription"))) if error.value else "unknown error"


def open_login_items_settings() -> None:
    if MAC:
        with _pool():
            _login_service()
            _fn(None)(_cls("SMAppService"), _sel("openSystemSettingsLoginItems"))
