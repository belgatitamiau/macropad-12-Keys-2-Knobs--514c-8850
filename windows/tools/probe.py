"""Read-only identity probe for the 514c:8850 macropad.

Uses only the HID stack (hid.dll + setupapi.dll) via ctypes - no USBDK,
no libusb, no Zadig, no driver replacement. Overlapped I/O with explicit
timeouts so we never reproduce the vendor app's blocking-read hang.

SAFETY: this script only ever sends the identity query `03 FB FB FB`.
It never sends 0xFC (hardware variant) or 0xEF (bootloader).
"""

import ctypes
from ctypes import wintypes as w

hid = ctypes.WinDLL("hid.dll")
setupapi = ctypes.WinDLL("setupapi.dll")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010

TARGET_VID = 0x514C
TARGET_PID = 0x8850


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


GUID_DEVINTERFACE_HID = GUID(
    0x4D1E55B2, 0xF16F, 0x11CF,
    (ctypes.c_ubyte * 8)(0x88, 0xCB, 0x00, 0x11, 0x11, 0x00, 0x00, 0x30),
)


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", w.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", w.ULONG),
        ("VendorID", w.USHORT),
        ("ProductID", w.USHORT),
        ("VersionNumber", w.USHORT),
    ]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [
        ("Usage", w.USHORT),
        ("UsagePage", w.USHORT),
        ("InputReportByteLength", w.USHORT),
        ("OutputReportByteLength", w.USHORT),
        ("FeatureReportByteLength", w.USHORT),
        ("Reserved", w.USHORT * 17),
        ("NumberLinkCollectionNodes", w.USHORT),
        ("NumberInputButtonCaps", w.USHORT),
        ("NumberInputValueCaps", w.USHORT),
        ("NumberInputDataIndices", w.USHORT),
        ("NumberOutputButtonCaps", w.USHORT),
        ("NumberOutputValueCaps", w.USHORT),
        ("NumberOutputDataIndices", w.USHORT),
        ("NumberFeatureButtonCaps", w.USHORT),
        ("NumberFeatureValueCaps", w.USHORT),
        ("NumberFeatureDataIndices", w.USHORT),
    ]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", w.DWORD),
        ("OffsetHigh", w.DWORD),
        ("hEvent", ctypes.c_void_p),
    ]


setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID), w.LPCWSTR, w.HWND, w.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(GUID), w.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD), ctypes.c_void_p]
setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

hid.HidD_GetAttributes.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetPreparsedData.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]

kernel32.CreateFileW.restype = ctypes.c_void_p
kernel32.CreateFileW.argtypes = [
    w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD, w.DWORD,
    ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.CreateEventW.restype = ctypes.c_void_p
kernel32.CreateEventW.argtypes = [
    ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, w.DWORD]
kernel32.WriteFile.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD),
    ctypes.POINTER(OVERLAPPED)]
kernel32.ReadFile.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD),
    ctypes.POINTER(OVERLAPPED)]
kernel32.GetOverlappedResult.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(OVERLAPPED), ctypes.POINTER(w.DWORD),
    w.BOOL]
kernel32.CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.POINTER(OVERLAPPED)]


def enumerate_hid_paths():
    paths = []
    hdev = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(GUID_DEVINTERFACE_HID), None, None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if hdev == INVALID_HANDLE_VALUE or not hdev:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        idx = 0
        while True:
            did = SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(did)
            if not setupapi.SetupDiEnumDeviceInterfaces(
                    hdev, None, ctypes.byref(GUID_DEVINTERFACE_HID), idx,
                    ctypes.byref(did)):
                break
            req = w.DWORD(0)
            setupapi.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(did), None, 0, ctypes.byref(req), None)
            buf = ctypes.create_string_buffer(req.value)
            cb = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            ctypes.memmove(buf, ctypes.byref(w.DWORD(cb)), 4)
            if setupapi.SetupDiGetDeviceInterfaceDetailW(
                    hdev, ctypes.byref(did), buf, req.value,
                    ctypes.byref(req), None):
                paths.append(ctypes.wstring_at(ctypes.addressof(buf) + 4))
            idx += 1
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(hdev)
    return paths


def open_path(path):
    return kernel32.CreateFileW(
        path, GENERIC_READ | GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING,
        FILE_FLAG_OVERLAPPED, None)


def get_info(handle):
    attrs = HIDD_ATTRIBUTES()
    attrs.Size = ctypes.sizeof(attrs)
    if not hid.HidD_GetAttributes(handle, ctypes.byref(attrs)):
        return None, None
    preparsed = ctypes.c_void_p()
    caps = None
    if hid.HidD_GetPreparsedData(handle, ctypes.byref(preparsed)) and preparsed:
        caps = HIDP_CAPS()
        # HidP_GetCaps returns HIDP_STATUS_SUCCESS (0x00110000), not 0.
        if hid.HidP_GetCaps(preparsed, ctypes.byref(caps)) != 0x00110000:
            caps = None
        hid.HidD_FreePreparsedData(preparsed)
    return attrs, caps


def sync_transfer(handle, is_write, payload, timeout_ms=2000):
    ev = kernel32.CreateEventW(None, True, False, None)
    ov = OVERLAPPED()
    ov.hEvent = ev
    buf = ctypes.create_string_buffer(bytes(payload), len(payload))
    done = w.DWORD(0)
    fn = kernel32.WriteFile if is_write else kernel32.ReadFile
    ok = fn(handle, buf, len(payload), ctypes.byref(done), ctypes.byref(ov))
    err = ctypes.get_last_error()
    if not ok and err != ERROR_IO_PENDING:
        kernel32.CloseHandle(ev)
        return b"", err
    if not ok:
        rc = kernel32.WaitForSingleObject(ev, timeout_ms)
        if rc != WAIT_OBJECT_0:
            kernel32.CancelIoEx(handle, ctypes.byref(ov))
            kernel32.CloseHandle(ev)
            return b"", ("timeout")
    got = w.DWORD(0)
    if not kernel32.GetOverlappedResult(handle, ctypes.byref(ov),
                                        ctypes.byref(got), True):
        kernel32.CloseHandle(ev)
        return b"", ctypes.get_last_error()
    kernel32.CloseHandle(ev)
    return buf.raw[:got.value], 0


def hexs(b):
    return " ".join(f"{x:02x}" for x in b)


def main():
    print("=== enumerate HID interfaces ===")
    target = None
    for path in enumerate_hid_paths():
        h = open_path(path)
        if not h or h == INVALID_HANDLE_VALUE:
            continue
        try:
            attrs, caps = get_info(h)
            if attrs is None:
                continue
            vid, pid = attrs.VendorID, attrs.ProductID
            if vid == TARGET_VID and pid == TARGET_PID:
                up = caps.UsagePage if caps else None
                us = caps.Usage if caps else None
                print(f"  ** MATCH {vid:04x}:{pid:04x}  usagePage={up} usage={us} "
                      f"in={caps.InputReportByteLength if caps else '?'} "
                      f"out={caps.OutputReportByteLength if caps else '?'}")
                print(f"     path: {path}")
                if caps and caps.UsagePage == 0xFF00 and caps.Usage == 0x0001:
                    target = (path, caps)
        finally:
            kernel32.CloseHandle(h)

    if not target:
        print("  No 514c:8850 vendor collection (usage page 0xFF00/usage 0x0001) found.")
        return 1

    path, caps = target
    out_len = caps.OutputReportByteLength
    in_len = caps.InputReportByteLength
    print(f"\n=== identity probe on MI_00 (out={out_len}, in={in_len}) ===")

    h = open_path(path)
    if not h or h == INVALID_HANDLE_VALUE:
        print("  open failed:", ctypes.get_last_error())
        return 1
    try:
        msg = bytes([0x03, 0xFB, 0xFB, 0xFB]) + b"\x00" * (out_len - 4)
        print(f"  -> {hexs(msg[:8])} ... ({len(msg)} bytes)")
        _, err = sync_transfer(h, True, msg)
        if err:
            print("  write error:", err)
            return 1
        keys = knobs = None
        for attempt in range(3):
            data, err = sync_transfer(h, False, bytes(in_len))
            if err:
                print(f"  read attempt {attempt + 1}: {err}")
                continue
            if data:
                print(f"  <- {hexs(data)}")
                if len(data) >= 4 and data[1] == 0xFB:
                    keys, knobs = data[2], data[3]
                    break
        if keys is None:
            print("  no valid identity reply")
            return 2
        print(f"\n  *** IDENTITY CONFIRMED: keys={keys} knobs={knobs} ***")

        # --- dialect probe: 0xFA is a READ on ch57x-1, absent on ch57x-3 ---
        print("\n=== dialect probe (send 0xFA read; silent on ch57x-3) ===")
        fa = bytes([0x03, 0xFA, keys, knobs, 0x01]) + b"\x00" * (out_len - 5)
        print(f"  -> {hexs(fa[:8])} ... ({len(fa)} bytes)")
        _, err = sync_transfer(h, True, fa)
        if err:
            print("  write error:", err)
        replies = []
        for _ in range(3):
            data, err = sync_transfer(h, False, bytes(in_len), timeout_ms=1200)
            if err:
                break
            if data:
                replies.append(data)
        if replies:
            for r in replies[:4]:
                print(f"  <- {hexs(r)}")
            print("\n  *** DIALECT = ch57x-1 (marker 0xFE) - device answers 0xFA read-back ***")
        else:
            print("  (silencio)")
            print("\n  *** DIALECT = ch57x-3 (marker 0xFD) - no read-back, matches spokino ***")
        return 0
    finally:
        kernel32.CloseHandle(h)


if __name__ == "__main__":
    raise SystemExit(main())
