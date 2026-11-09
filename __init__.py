import os
import traceback

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

GREEN_TOTAL_DURATION = 1.0
GREEN_FADE_IN = 0.30
GREEN_FADE_OUT = 0.30

ANIMATION_FPS = 24

WINDOW_TITLE_HINT = "ComfyUI"
WINDOW_CLASS_HINT = "Chrome_WidgetWin_1"
ALLOW_FOREGROUND_FALLBACK = True

_IS_WINDOWS = os.name == "nt"


def _write_import_error(err_text):
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.join(base_dir, "import_error.log")

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(err_text)

        print(f"[ComfyUI-Indicator] import error log written to: {log_path}")
    except Exception:
        pass


try:
    import threading
    import time

    if _IS_WINDOWS:
        import ctypes
        from ctypes import (
            Structure,
            POINTER,
            byref,
            cast,
            c_long,
            c_ulong,
            c_ushort,
            c_ubyte,
            c_void_p,
            c_bool,
            c_ulonglong,
            WINFUNCTYPE,
            create_unicode_buffer,
        )

        DWORD = c_ulong
        HWND = c_void_p
        ULONGLONG = c_ulonglong
        LPARAM = c_long
        HRESULT = c_long

        CLSCTX_ALL = 23

        TBPF_NOPROGRESS = 0x0
        TBPF_NORMAL = 0x2

        COINIT_APARTMENTTHREADED = 0x0

        class GUID(Structure):
            _fields_ = [
                ("Data1", c_ulong),
                ("Data2", c_ushort),
                ("Data3", c_ushort),
                ("Data4", c_ubyte * 8),
            ]

        CLSID_TaskbarList = GUID(
            0x56FDF344,
            0xFD6D,
            0x11D0,
            (c_ubyte * 8)(0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90),
        )

        IID_ITaskbarList3 = GUID(
            0xEA1AFB91,
            0x9E28,
            0x4B86,
            (c_ubyte * 8)(0x90, 0xE9, 0x9E, 0x9F, 0x8A, 0x5E, 0xEF, 0xAF),
        )

        _active_stop = None
        _active_thread = None
        _animation_lock = threading.Lock()

        def _get_console_hwnd():
            try:
                return ctypes.windll.kernel32.GetConsoleWindow() or 0
            except Exception:
                return 0

        def _get_foreground_hwnd():
            try:
                return ctypes.windll.user32.GetForegroundWindow() or 0
            except Exception:
                return 0

        def _get_class_name(hwnd):
            try:
                buffer = create_unicode_buffer(256)
                ctypes.windll.user32.GetClassNameW(hwnd, buffer, 256)
                return buffer.value
            except Exception:
                return ""

        def _find_comfy_window():
            candidates = []

            title_hint = (WINDOW_TITLE_HINT or "").strip().lower()
            class_hint = (WINDOW_CLASS_HINT or "").strip().lower()

            def enum_callback(hwnd, lparam):
                try:
                    hwnd_value = hwnd.value if hasattr(hwnd, "value") else int(hwnd)

                    if not hwnd_value:
                        return True

                    if not ctypes.windll.user32.IsWindowVisible(hwnd_value):
                        return True

                    text_len = ctypes.windll.user32.GetWindowTextLengthW(hwnd_value)
                    if text_len <= 0:
                        return True

                    buffer = create_unicode_buffer(text_len + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd_value, buffer, text_len + 1)

                    title = buffer.value.strip().lower()
                    if not title:
                        return True

                    class_name = _get_class_name(hwnd_value).lower()

                    score = 0

                    if title_hint and title_hint in title:
                        score += 100

                    if title_hint and title == title_hint:
                        score += 50

                    if class_hint and class_name == class_hint:
                        score += 40

                    if "comfy" in title:
                        score += 5

                    if score > 0:
                        candidates.append((score, hwnd_value))

                except Exception:
                    pass

                return True

            callback = WINFUNCTYPE(c_bool, HWND, LPARAM)(enum_callback)

            try:
                ctypes.windll.user32.EnumWindows(callback, 0)
            except Exception:
                return 0

            if not candidates:
                return 0

            candidates.sort(key=lambda item: item[0], reverse=True)
            return candidates[0][1]

        def _get_target_hwnd():
            hwnd = _find_comfy_window()
            if hwnd:
                return int(hwnd)

            hwnd = _get_console_hwnd()
            if hwnd:
                return int(hwnd)

            if ALLOW_FOREGROUND_FALLBACK:
                hwnd = _get_foreground_hwnd()
                if hwnd:
                    return int(hwnd)

            return 0

        def _init_com():
            ole32 = ctypes.windll.ole32

            try:
                ole32.CoInitializeEx.argtypes = [c_void_p, DWORD]
                ole32.CoInitializeEx.restype = HRESULT
            except Exception:
                pass

            try:
                hr = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
                return hr >= 0
            except Exception:
                return False

        def _uninit_com(needs_uninit):
            if not needs_uninit:
                return

            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass

        def _create_taskbar_list3():
            ole32 = ctypes.windll.ole32

            try:
                ole32.CoCreateInstance.argtypes = [
                    POINTER(GUID),
                    c_void_p,
                    DWORD,
                    POINTER(GUID),
                    POINTER(c_void_p),
                ]
                ole32.CoCreateInstance.restype = HRESULT
            except Exception:
                pass

            obj = c_void_p()

            hr = ole32.CoCreateInstance(
                byref(CLSID_TaskbarList),
                None,
                CLSCTX_ALL,
                byref(IID_ITaskbarList3),
                byref(obj),
            )

            if hr < 0 or not obj.value:
                return None

            return obj

        def _release_com_obj(obj):
            try:
                ptr = cast(obj, POINTER(c_void_p))
                vtable_ptr = ptr[0]
                vtable = cast(vtable_ptr, POINTER(c_void_p))

                release = cast(vtable[2], WINFUNCTYPE(c_ulong, c_void_p))
                release(obj)
            except Exception:
                pass

        def _get_taskbar_interface(obj):
            ptr = cast(obj, POINTER(c_void_p))
            vtable_ptr = ptr[0]
            vtable = cast(vtable_ptr, POINTER(c_void_p))

            hr_init = cast(
                vtable[3],
                WINFUNCTYPE(HRESULT, c_void_p),
            )

            set_progress_value = cast(
                vtable[9],
                WINFUNCTYPE(HRESULT, c_void_p, HWND, ULONGLONG, ULONGLONG),
            )

            set_progress_state = cast(
                vtable[10],
                WINFUNCTYPE(HRESULT, c_void_p, HWND, DWORD),
            )

            release = cast(
                vtable[2],
                WINFUNCTYPE(c_ulong, c_void_p),
            )

            return hr_init, set_progress_value, set_progress_state, release

        def _wait_event(stop_event, seconds):
            if seconds <= 0:
                return True

            end_time = time.monotonic() + seconds

            while not stop_event.is_set():
                remaining = end_time - time.monotonic()
                if remaining <= 0:
                    return True

                time.sleep(min(0.01, remaining))

            return False

        def _fade_values(
            set_value_func,
            obj,
            hwnd_arg,
            start_value,
            end_value,
            duration,
            stop_event,
        ):
            if stop_event.is_set():
                return False

            if duration <= 0:
                hr = set_value_func(obj, hwnd_arg, ULONGLONG(end_value), ULONGLONG(100))
                return hr >= 0 and not stop_event.is_set()

            steps = max(2, int(duration * ANIMATION_FPS))
            step_time = duration / steps

            for i in range(steps + 1):
                if stop_event.is_set():
                    return False

                t = i / steps
                value = int(round(start_value + (end_value - start_value) * t))

                hr = set_value_func(obj, hwnd_arg, ULONGLONG(value), ULONGLONG(100))
                if hr < 0:
                    return False

                if i != steps:
                    if not _wait_event(stop_event, step_time):
                        return False

            return True

        def _animate_green(hwnd, total_duration, stop_event):
            fade_in = max(0.0, float(GREEN_FADE_IN))
            fade_out = max(0.0, float(GREEN_FADE_OUT))

            total_duration = max(0.0, float(total_duration))

            if total_duration <= 0:
                return

            if fade_in + fade_out > total_duration:
                if fade_in + fade_out > 0:
                    scale = total_duration / (fade_in + fade_out)
                    fade_in *= scale
                    fade_out *= scale

                hold = 0.0
            else:
                hold = total_duration - fade_in - fade_out

            needs_uninit = _init_com()
            obj = None
            release_func = None

            try:
                obj = _create_taskbar_list3()
                if not obj:
                    return

                hr_init, set_value_func, set_state_func, release_func = _get_taskbar_interface(obj)

                if hr_init(obj) < 0:
                    return

                hwnd_arg = HWND(hwnd)

                hr = set_state_func(obj, hwnd_arg, DWORD(TBPF_NORMAL))
                if hr < 0:
                    return

                if not _fade_values(
                    set_value_func,
                    obj,
                    hwnd_arg,
                    0,
                    100,
                    fade_in,
                    stop_event,
                ):
                    return

                if not _wait_event(stop_event, hold):
                    return

                if not _fade_values(
                    set_value_func,
                    obj,
                    hwnd_arg,
                    100,
                    0,
                    fade_out,
                    stop_event,
                ):
                    return

                set_state_func(obj, hwnd_arg, DWORD(TBPF_NOPROGRESS))
                set_value_func(obj, hwnd_arg, ULONGLONG(0), ULONGLONG(100))

            except Exception:
                pass

            finally:
                if release_func is not None:
                    try:
                        release_func(obj)
                    except Exception:
                        pass
                elif obj:
                    _release_com_obj(obj)

                _uninit_com(needs_uninit)

        def show_green(duration=1.0):
            global _active_stop
            global _active_thread

            hwnd = _get_target_hwnd()
            if not hwnd:
                return False

            with _animation_lock:
                if _active_thread is not None and _active_thread.is_alive():
                    if _active_stop is not None:
                        _active_stop.set()

                    try:
                        _active_thread.join(timeout=0.05)
                    except Exception:
                        pass

                stop_event = threading.Event()

                thread = threading.Thread(
                    target=_animate_green,
                    args=(hwnd, float(duration), stop_event),
                    daemon=True,
                )

                _active_stop = stop_event
                _active_thread = thread

                thread.start()

            return True

    else:

        def show_green(duration=1.0):
            return False

    if _IS_WINDOWS:
        _EXECUTION_FAILED = False

        def _is_finished_executing(data):
            """
            Проверяет, является ли событие 'executing' признаком завершения.
            """
            if data is None:
                return True

            if isinstance(data, dict):
                if "node" not in data:
                    return False
                return data.get("node") is None

            return False

        def _handle_server_event(event, data):
            global _EXECUTION_FAILED

            if event == "execution_start":
                _EXECUTION_FAILED = False

            elif event == "execution_interrupted":
                _EXECUTION_FAILED = True

            elif event == "execution_error":
                _EXECUTION_FAILED = True

            elif event == "executing":
                if _is_finished_executing(data) and not _EXECUTION_FAILED:
                    show_green(GREEN_TOTAL_DURATION)

        try:
            from server import PromptServer

            if hasattr(PromptServer, "send_sync"):
                _original_send_sync = PromptServer.send_sync

                def _patched_send_sync(self, event, data, *args, **kwargs):
                    try:
                        _handle_server_event(event, data)
                    except Exception:
                        pass

                    return _original_send_sync(self, event, data, *args, **kwargs)

                PromptServer.send_sync = _patched_send_sync
                print("[ComfyUI-Indicator] server hook installed")

            else:
                print("[ComfyUI-Indicator] PromptServer.send_sync not found")

        except Exception as e:
            print(f"[ComfyUI-Indicator] server hook error: {e}")
            print(traceback.format_exc())

    print("[ComfyUI-Indicator] module loaded")

except Exception:
    err = traceback.format_exc()

    print("[ComfyUI-Indicator] IMPORT ERROR")
    print(err)

    _write_import_error(err)

    def show_green(duration=1.0):
        return False