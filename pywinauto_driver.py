"""Pywinauto driver for FINESIII terminal automation.

Port of v2 drivers/pywinauto_driver.py with two approved fixes:

- keystroke_delay is resolved from the merged application config that
  the executor builds from the timing section (v2 read it from the
  'application' block where it never existed, so the field default was
  always used and the terminal dropped/transposed characters);
- wait_for_screen honors 'strict_screen_detection': when strict, an
  unmatched screen raises ScreenTimeoutError instead of silently
  proceeding. The lenient default preserves the field-proven behavior
  for THIS terminal, whose screen body is not machine-readable (only
  the window title is) — hard-failing on a correct-but-unreadable
  screen previously aborted whole transactions.

Uses pywinauto to interact with the Pines 3 / Fujitsu desktop terminal
emulator on Windows. This is the PRIMARY driver for environments where
FINESIII runs as a native Windows desktop application.

Platform: Windows only
"""

import time
from pathlib import Path

from src.core.exceptions import (
    ApplicationLaunchError, DriverError, ScreenTimeoutError,
)
from src.rpa.drivers.base import BaseDriver


class PywinautoDriver(BaseDriver):
    """Windows desktop terminal automation via pywinauto."""

    def __init__(self):
        self.app = None
        self.window = None
        self.config = {}

    def initialize(self, config: dict) -> None:
        """Initialize with application configuration."""
        self.config = config
        # Lazy import — only needed on Windows
        try:
            import pywinauto
            self._pywinauto = pywinauto
        except ImportError:
            raise DriverError(
                "pywinauto is required for Windows terminal automation. "
                "Install with: pip install pywinauto",
                step="driver_init"
            )

    def launch_application(self) -> None:
        """Launch the FINESIII terminal emulator."""
        from pywinauto import Application

        exe_path = self.config.get("executable_path", "")
        shortcut = self.config.get("desktop_shortcut", "Online Work")
        title_pattern = self.config.get("window_title_pattern", "FINES")
        timeout = self.config.get("launch_timeout", 30)

        try:
            if exe_path and Path(exe_path).exists():
                # Launch by executable path
                self.app = Application(backend="uia").start(
                    exe_path, timeout=timeout
                )
            else:
                # Try to connect to an already-running instance
                try:
                    self.app = Application(backend="uia").connect(
                        title_re=f".*{title_pattern}.*", timeout=10
                    )
                except Exception:
                    # If not running, try launching via shortcut
                    self._launch_via_shortcut(shortcut)
                    time.sleep(5)
                    self.app = Application(backend="uia").connect(
                        title_re=f".*{title_pattern}.*", timeout=timeout
                    )

            # Get the main window
            self.window = self.app.window(title_re=f".*{title_pattern}.*")
            self.window.wait("visible", timeout=timeout)
            self.window.set_focus()

        except Exception as e:
            raise ApplicationLaunchError(
                f"Failed to launch FINESIII terminal: {e}",
                step="launch_application"
            )

    def reattach_to_window(self, title_pattern: str,
                           timeout: float = 15.0) -> bool:
        """Re-attach to the FINESIII login window that opens during
        navigation.

        Matching by title substring ('FINES') is unreliable because other
        windows (e.g. the VS Code window for the 'finesiii_rpa' project)
        contain 'fines' too and would receive keystrokes by mistake.

        The Fujitsu terminal windows have a distinctive window CLASS:
        'F3BJMD101'. Both the menu (基幹業務メニュー) and the login
        (F I N E SⅢ) windows use this class. We therefore match on:
          • window class == 'F3BJMD101'  (the Fujitsu terminal), AND
          • title does NOT contain 基幹業務  (so we skip the menu window).
        """
        from pywinauto import Application
        import win32gui

        terminal_class = self.config.get("terminal_window_class",
                                         "F3BJMD101")
        menu_marker = "基幹業務"

        def find_login_hwnd():
            found = []

            def _cb(hwnd, _):
                if not win32gui.IsWindowVisible(hwnd):
                    return
                try:
                    cls = win32gui.GetClassName(hwnd) or ""
                except Exception:
                    return
                if cls != terminal_class:
                    return  # not a Fujitsu terminal window
                title = win32gui.GetWindowText(hwnd) or ""
                if menu_marker in title:
                    return  # that's the menu window — skip
                found.append(hwnd)

            win32gui.EnumWindows(_cb, None)
            return found[0] if found else None

        start = time.time()
        last_err = None
        while time.time() - start < timeout:
            try:
                hwnd = find_login_hwnd()
                if hwnd:
                    new_app = Application(backend="uia").connect(
                        handle=hwnd, timeout=2)
                    new_win = new_app.window(handle=hwnd)
                    new_win.wait("visible", timeout=3)
                    self.app = new_app
                    self.window = new_win
                    self.window.set_focus()
                    return True
            except Exception as e:
                last_err = e
            time.sleep(1.0)

        raise ApplicationLaunchError(
            f"Could not re-attach to FINESIII login window "
            f"(class '{terminal_class}', non-menu): {last_err}",
            step="reattach_to_window"
        )

    def dismiss_dialog(self, key: str = "enter",
                       title_hint: str = "") -> bool:
        """Acknowledge a modal FINESIII error dialog (エラーメッセージ).

        The dialog is a SEPARATE owned window, not part of the terminal
        surface: reattach_to_window() cannot find it (that matches on
        the Fujitsu terminal class F3BJMD101, which the dialog does not
        use), and press_key() sends to self.window — the terminal —
        which may not be the keyboard focus while a modal is up.

        So: locate the dialog by title and/or ownership, focus it, send
        the acknowledge key, and report whether it was actually found.
        Returns False on ANY problem so the caller falls back to a
        plain key press (today's behaviour) rather than failing — a
        dialog that never gets dismissed must not also lose the
        keystroke that might have dismissed it.
        """
        try:
            import win32gui
        except Exception:
            return False

        own_hwnd = 0
        try:
            own_hwnd = int(self.window.handle) if self.window else 0
        except Exception:
            own_hwnd = 0

        terminal_class = self.config.get("terminal_window_class",
                                         "F3BJMD101")

        def find_dialog():
            found = []

            def _cb(hwnd, _):
                try:
                    if not win32gui.IsWindowVisible(hwnd):
                        return
                    cls = win32gui.GetClassName(hwnd) or ""
                    title = win32gui.GetWindowText(hwnd) or ""
                except Exception:
                    return
                if cls == terminal_class:
                    return              # the terminal itself, not a dialog
                if title_hint and title_hint in title:
                    found.insert(0, hwnd)     # title match wins
                    return
                # Otherwise: a standard dialog owned by our terminal
                try:
                    owner = win32gui.GetWindow(hwnd, 4)   # GW_OWNER
                except Exception:
                    owner = 0
                if cls == "#32770" and own_hwnd and owner == own_hwnd:
                    found.append(hwnd)

            try:
                win32gui.EnumWindows(_cb, None)
            except Exception:
                return None
            return found[0] if found else None

        hwnd = find_dialog()
        if not hwnd:
            return False

        try:
            from pywinauto import Application
            dialog = Application(backend="uia").connect(
                handle=hwnd, timeout=2).window(handle=hwnd)
            dialog.set_focus()
            time.sleep(0.2)
            dialog.type_keys(self._key_sequence(key), pause=0.05)
            time.sleep(0.3)
            return True
        except Exception as e:
            if self.logger:
                self.logger.warning(
                    f"Found error dialog (hwnd {hwnd}) but could not "
                    f"acknowledge it: {e}")
            return False

    def clear_field(self, count: int = 8) -> None:
        """Clear the current input field by OVERTYPING it with spaces.

        On this FINESIII terminal, Backspace is NON-DESTRUCTIVE: it moves
        the cursor without erasing characters (confirmed on the login
        screen). Typing, however, DOES register. So to clear the leftover
        affiliation code before the final '99' that closes the window:
          1. move to the start of the field (HOME),
          2. type `count` spaces, overwriting each character in place,
          3. return to the start of the field (HOME).
        """
        self._ensure_foreground()
        try:
            self.window.type_keys("{HOME}", pause=0.05)
            self.window.type_keys("{SPACE " + str(count) + "}", pause=0.05)
            self.window.type_keys("{HOME}", pause=0.05)
        except Exception:
            # Fallback: discrete key presses (Home, overtype spaces, Home)
            try:
                self.press_key("home")
            except Exception:
                pass
            for _ in range(count):
                try:
                    self.type_text(" ")
                except Exception:
                    break
            try:
                self.press_key("home")
            except Exception:
                pass

    def goto_first_field_and_exit(self, exit_code: str = "99",
                                  field_width: int = 7,
                                  backtab_count: int = 4) -> None:
        """Anchor to the FIRST field via repeated Shift+Tab, clear it,
        submit the exit code.

        During logout, after the plain '99's return to the FINESIII login
        screen, the cursor position is not guaranteed. The exit code must
        be entered in the FIRST field (affiliation / 所属コード).

        Confirmed terminal behaviour:
          - Shift+Tab moves to the previous field and STAYS on the first
            field once reached (no wrap), so pressing it more times than
            there are fields reliably anchors from ANY position.
          - HOME does nothing here; typing registers; Backspace does NOT
            erase.
        """
        self._ensure_foreground()

        def backtab(n):
            for _ in range(max(1, n)):
                try:
                    self.window.type_keys("+{TAB}", pause=0.05)  # Shift+Tab
                except Exception:
                    break
                time.sleep(0.15)

        # 1) Anchor to the first (affiliation) field from any position
        backtab(backtab_count)
        time.sleep(0.3)

        # 2) Fully clear the field by overtyping with spaces (width+margin)
        pad = field_width + 2
        try:
            self.window.type_keys("{SPACE " + str(pad) + "}", pause=0.05)
        except Exception:
            for _ in range(pad):
                try:
                    self.type_text(" ")
                except Exception:
                    break
        time.sleep(0.3)

        # 3) Re-anchor to the first field start (cursor was at field end)
        backtab(backtab_count)
        time.sleep(0.3)

        # 4) Type the exit code and submit
        try:
            self.window.type_keys(exit_code, pause=0.08)
        except Exception:
            self.type_text(exit_code)
        time.sleep(0.2)
        self.press_key("enter")
        time.sleep(0.3)

    def reattach_to_menu_window(self, menu_title: str = "基幹業務",
                                timeout: float = 15.0) -> bool:
        """Re-attach to the Main Menu window after the FINESIII window
        closes during logout, to send the closing '99' -> 'XX'.

        Both windows share the terminal window class (F3BJMD101); they are
        distinguished by TITLE — the menu window's title contains 基幹業務.
        """
        from pywinauto import Application
        import win32gui

        terminal_class = self.config.get("terminal_window_class",
                                         "F3BJMD101")

        def find_menu_hwnd():
            found = []

            def _cb(hwnd, _):
                if not win32gui.IsWindowVisible(hwnd):
                    return
                try:
                    cls = win32gui.GetClassName(hwnd) or ""
                    title = win32gui.GetWindowText(hwnd) or ""
                except Exception:
                    return
                if terminal_class in cls and menu_title in title:
                    found.append(hwnd)

            win32gui.EnumWindows(_cb, None)
            return found[0] if found else None

        start = time.time()
        last_err = None
        while time.time() - start < timeout:
            try:
                hwnd = find_menu_hwnd()
                if hwnd:
                    new_app = Application(backend="uia").connect(
                        handle=hwnd, timeout=2)
                    new_win = new_app.window(handle=hwnd)
                    new_win.wait("visible", timeout=3)
                    self.app = new_app
                    self.window = new_win
                    # Robust foreground-forcing (not just set_focus, which
                    # Windows silently blocks) so the closing '99' -> 'XX'
                    # keystrokes actually land in the menu window.
                    self._ensure_foreground()
                    return True
            except Exception as e:
                last_err = e
            time.sleep(1.0)

        raise ApplicationLaunchError(
            f"Could not re-attach to Main Menu window "
            f"(class '{terminal_class}', title contains '{menu_title}'): "
            f"{last_err}",
            step="reattach_to_menu_window"
        )

    def _launch_via_shortcut(self, shortcut_name: str):
        """Launch application via desktop shortcut.

        Looks for the .lnk in this order:
          1. explicit shortcut_path from config (if set)
          2. user Desktop
          3. public/common Desktop (Fujitsu installers put it here)
        """
        import os

        # 1. Explicit full path from config
        explicit = self.config.get("shortcut_path", "")
        if explicit and Path(explicit).exists():
            self._start_shortcut(explicit)
            return

        # 2 & 3. Search user and public desktops
        candidates = [
            Path(os.environ.get("USERPROFILE", "")) / "Desktop"
            / f"{shortcut_name}.lnk",
            Path(os.environ.get("PUBLIC", "")) / "Desktop"
            / f"{shortcut_name}.lnk",
        ]
        for path in candidates:
            if path.exists():
                self._start_shortcut(str(path))
                return

        searched = "\n  ".join(
            str(p) for p in ([explicit] if explicit else []) + candidates
        )
        raise ApplicationLaunchError(
            f"Desktop shortcut '{shortcut_name}' not found. "
            f"Searched:\n  {searched}",
            step="launch_application"
        )

    def _start_shortcut(self, path: str) -> None:
        """Open the launcher shortcut — as administrator by default.

        The FINESIII terminal must run elevated (the same as right-click >
        Run as administrator). The "runas" verb does that. Windows lets
        only an elevated process send keys to an elevated window, so the
        bot itself must run as administrator too; it then launches the
        terminal without a consent prompt. run_as_admin: false in the
        application block of layouts.yaml restores the plain open.
        """
        import os

        if not self.config.get("run_as_admin", True):
            os.startfile(path)
            return
        try:
            os.startfile(path, "runas")
        except OSError as e:
            raise ApplicationLaunchError(
                f"The FINESIII terminal could not be started as "
                f"administrator ({e}). Start the bot from a window opened "
                f"with 'Run as administrator'.",
                step="launch_application"
            )

    def _ensure_foreground(self) -> None:
        """Force the connected terminal window to the foreground.

        Windows blocks SetForegroundWindow from background processes, so a
        plain set_focus() silently fails and keystrokes go to whatever
        window is actually in front. The AttachThreadInput trick
        temporarily links our input thread to the target window's thread,
        which grants permission to bring it forward.
        """
        try:
            import win32gui
            import win32process
            import win32con
            import win32api
        except Exception:
            # win32 not available — fall back to pywinauto focus only
            try:
                self.window.set_focus()
            except Exception:
                pass
            return

        try:
            hwnd = self.window.handle
        except Exception:
            hwnd = None
        if not hwnd:
            try:
                self.window.set_focus()
            except Exception:
                pass
            return

        try:
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        except Exception:
            pass

        this_tid = win32api.GetCurrentThreadId()
        try:
            target_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            target_tid = 0
        try:
            fg = win32gui.GetForegroundWindow()
            fg_tid, _ = (win32process.GetWindowThreadProcessId(fg)
                         if fg else (0, 0))
        except Exception:
            fg_tid = 0

        attached = []
        for tid in {target_tid, fg_tid}:
            if tid and tid != this_tid:
                try:
                    win32process.AttachThreadInput(this_tid, tid, True)
                    attached.append(tid)
                except Exception:
                    pass
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            win32gui.SetActiveWindow(hwnd)
        except Exception:
            pass
        for tid in attached:
            try:
                win32process.AttachThreadInput(this_tid, tid, False)
            except Exception:
                pass

        try:
            self.window.set_focus()
        except Exception:
            pass

    def type_text(self, text: str, delay: float = None) -> None:
        """Type text into the focused field, one character at a time.

        This legacy 32-bit terminal drops/transposes characters when
        keystrokes arrive too fast (e.g. '9903325' was received as
        '9033325'). Sending each character individually with a deliberate
        pause makes entry reliable. The per-character delay comes from
        timing.keystroke_delay, which the executor merges into this
        driver's config (approved fix: v2 looked it up in the
        'application' block where it never existed).
        """
        if not self.window:
            raise DriverError("No window connected", step="type_text")

        if delay is None:
            delay = self.config.get("keystroke_delay", 0.15)

        try:
            self._ensure_foreground()
            for ch in text:
                # Escape pywinauto special characters per-character
                safe = ch.replace("{", "{{").replace("}", "}}")
                safe = safe.replace("+", "{+}").replace("^", "{^}")
                safe = safe.replace("%", "{%}").replace("~", "{~}")
                safe = safe.replace("(", "{(}").replace(")", "{)}")
                self.window.type_keys(safe, pause=0, with_spaces=True)
                time.sleep(delay)
        except Exception as e:
            raise DriverError(f"Failed to type text: {e}", step="type_text")

    # Shared by press_key() and dismiss_dialog() — the dialog is a
    # different window, but the key encoding is the same.
    _KEY_MAP = {
        "enter": "{ENTER}",
        "tab": "{TAB}",
        "escape": "{ESC}",
        "backspace": "{BACKSPACE}",
        "delete": "{DELETE}",
        "home": "{HOME}",
        "end": "{END}",
        "up": "{UP}",
        "down": "{DOWN}",
        "left": "{LEFT}",
        "right": "{RIGHT}",
        "f1": "{F1}", "f2": "{F2}", "f3": "{F3}", "f4": "{F4}",
        "f5": "{F5}", "f6": "{F6}", "f7": "{F7}", "f8": "{F8}",
        "f9": "{F9}", "f10": "{F10}", "f11": "{F11}", "f12": "{F12}",
    }

    @classmethod
    def _key_sequence(cls, key: str) -> str:
        """Map a platform key name onto a pywinauto type_keys token."""
        return cls._KEY_MAP.get((key or "").lower(), f"{{{key}}}")

    def press_key(self, key: str) -> None:
        """Press a keyboard key."""
        if not self.window:
            raise DriverError("No window connected", step="press_key")

        try:
            self._ensure_foreground()
            self.window.type_keys(self._key_sequence(key))
        except Exception as e:
            raise DriverError(f"Failed to press key '{key}': {e}",
                              step="press_key")

    def send_field_value(self, field_id: str, value: str) -> None:
        """Enter a value at the current cursor position. The caller is
        responsible for cursor positioning (via the Enter sequence)."""
        self.type_text(value)

    def get_screen_text(self) -> str:
        """Read all readable text from the terminal window.

        This green-screen terminal (KANRIP / FGG1010, 32-bit) renders its
        body text graphically — UIA returns no child text. The window
        TITLE, however, is reliably readable via the win32 backend and
        changes per screen, so we always include it. Title-based
        detection is the dependable signal here.
        """
        if not self.window:
            raise DriverError("No window connected", step="get_screen_text")

        try:
            try:
                self.window.set_focus()
            except Exception:
                pass

            texts = []

            # Method 1: UIA window text (often empty on this terminal)
            try:
                t = self.window.window_text()
                if t:
                    texts.append(t)
            except Exception:
                pass

            # Method 2: UIA child elements (empty on this terminal, kept
            # for other screens / future compatibility)
            try:
                for child in self.window.descendants():
                    try:
                        t = child.window_text()
                        if t and t.strip():
                            texts.append(t)
                    except Exception:
                        continue
            except Exception:
                pass

            # Method 3: win32 window TITLE — the reliably-readable signal.
            # Titles may render with spaces between letters
            # (e.g. 'F I N E S Ⅲ'), so also try a space-tolerant pattern.
            try:
                from pywinauto import Application
                tp = self.config.get("window_title_pattern", "")
                raw_patterns = [p for p in [tp, "FINES", "基幹業務"] if p]
                patterns = []
                for p in raw_patterns:
                    patterns.append(p)
                    patterns.append(r"\s*".join(list(p)))  # space-tolerant
                seen = set()
                for pat in patterns:
                    if pat in seen:
                        continue
                    seen.add(pat)
                    try:
                        w32 = Application(backend="win32").connect(
                            title_re=f".*{pat}.*", timeout=1
                        )
                        win = w32.window(title_re=f".*{pat}.*")
                        title = win.window_text()
                        if title and title.strip():
                            texts.append(title)
                            # also append a despaced copy so identifier
                            # substrings like 'FINES' match
                            texts.append(
                                title.replace(" ", "").replace("　", ""))
                    except Exception:
                        continue
            except Exception:
                pass

            return "\n".join(texts)

        except Exception as e:
            raise DriverError(
                f"Failed to read screen text: {e}", step="get_screen_text"
            )

    def wait_for_screen(self, identifiers: list[str],
                        timeout: float = 15.0) -> bool:
        """Wait until the screen is recognized.

        This terminal exposes only the window TITLE, not body text, so a
        positive match on ANY identifier succeeds early. When nothing
        matches within the poll window, behavior is config-driven
        (approved fix — v2 always proceeded, silently disabling
        verification with no way to turn it on):

          strict_screen_detection: true  → raise ScreenTimeoutError
          strict_screen_detection: false → timed wait, then proceed
            (default: field-proven behavior for THIS terminal, whose
            correct screens are frequently unreadable; hard-failing
            there aborted whole transactions and forced logout)
        """
        strict = self.config.get("strict_screen_detection", False)
        start = time.time()
        poll_deadline = min(timeout, 5.0) if not strict else timeout

        while time.time() - start < poll_deadline:
            try:
                text = self.get_screen_text()
                if any(ident in text for ident in identifiers):
                    return True
            except Exception:
                pass
            time.sleep(0.5)

        if strict:
            raise ScreenTimeoutError(
                f"Screen with identifiers {identifiers} not detected "
                f"within {timeout}s (strict_screen_detection on)",
                step="wait_for_screen"
            )

        # Lenient: could not positively match via title — fall back to a
        # timed wait so the screen has time to settle, then proceed
        # (navigation is deterministic on this terminal).
        remaining = timeout - (time.time() - start)
        if remaining > 0:
            time.sleep(remaining)

        return True

    def screen_contains(self, text: str) -> bool:
        """Check if current screen contains specific text."""
        try:
            screen_text = self.get_screen_text()
            return text in screen_text
        except Exception:
            return False

    def take_screenshot(self, filepath: str) -> None:
        """Capture screenshot of the terminal window."""
        if not self.window:
            raise DriverError("No window connected", step="take_screenshot")

        try:
            image = self.window.capture_as_image()
            image.save(filepath)
        except Exception:
            # Fallback to full-screen capture
            try:
                import pyautogui
                pyautogui.screenshot(filepath)
            except Exception as e:
                raise DriverError(
                    f"Failed to capture screenshot: {e}",
                    step="take_screenshot"
                )

    def close_application(self) -> None:
        """Gracefully close the terminal emulator."""
        if self.window:
            try:
                # Try graceful exit first
                self.window.set_focus()
                self.type_text("99")
                self.press_key("enter")
                time.sleep(2)
            except Exception:
                pass

            try:
                if self.window.exists():
                    self.window.close()
            except Exception:
                pass

        self.app = None
        self.window = None
