from __future__ import annotations

import base64
import csv
import io
import os
import random
import re
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Optional

from app.hands.hands_iface import Hands
from app.hands.screenshot import capture_png_base64


@dataclass
class BoundingBox:
    left: int
    top: int
    right: int
    bottom: int
    width: int
    height: int
    
    def get_center(self) -> 'Center':
        return Center(
            x=int((self.left + self.right) / 2),
            y=int((self.top + self.bottom) / 2)
        )


@dataclass
class Center:
    x: int
    y: int


@dataclass
class TreeElementNode:
    name: str
    control_type: str
    bounding_box: BoundingBox
    center: Center
    window_name: str
    xpath: str
    value: str
    shortcut: str
    is_focused: bool


class WindowsMCPHands(Hands):
    """
    Windows automation backend implementing Windows-MCP patterns.
    
    Provides rich desktop state with labeled interactive elements,
    window context, and precise coordinate-based interactions.
    """

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("WindowsMCPHands is only available on Windows.")
        
        try:
            import uiautomation as auto
            import pyautogui
            import win32gui
            import win32process
            self._auto = auto
            self._pyautogui = pyautogui
            self._win32gui = win32gui
            self._win32process = win32process
            
            # Configure pyautogui
            pyautogui.FAILSAFE = False
            pyautogui.PAUSE = 0.5
            
        except Exception as e:
            raise RuntimeError(f'Failed to initialize Windows automation: {e}') from e

    async def observe(self, *, include_screenshot: bool, ui_tree_depth: int) -> dict[str, Any]:
        """
        Capture rich desktop state with element labeling.
        """
        print(f"[HANDS DEBUG] observe() called - include_screenshot={include_screenshot}")
        try:
            # Get active window
            active_hwnd = self._win32gui.GetForegroundWindow()
            active_title = self._win32gui.GetWindowText(active_hwnd)
            print(f"[HANDS DEBUG] Active window: {active_title} (hwnd={active_hwnd})")
            
            # Get all windows
            windows = self._get_windows_list()
            print(f"[HANDS DEBUG] Found {len(windows)} windows")
            
            # Get interactive elements from active window
            interactive_elements = []
            try:
                root = self._auto.GetForegroundControl()
                print(f"[HANDS DEBUG] Got foreground control: {root}")
                if root:
                    elements = self._get_interactive_elements(root, active_title or "Active Window")
                    print(f"[HANDS DEBUG] Found {len(elements)} interactive elements")
                    
                    # Label elements
                    for i, elem in enumerate(elements):
                        interactive_elements.append({
                            "label": i,
                            "name": elem.name,
                            "control_type": elem.control_type,
                            "coordinates": [elem.center.x, elem.center.y],
                            "window": elem.window_name,
                            "is_focused": elem.is_focused,
                            "value": elem.value if elem.value else None,
                            "shortcut": elem.shortcut if elem.shortcut else None,
                        })
                    
                    # Debug: print element names
                    if interactive_elements:
                        print(f"[HANDS DEBUG] Elements found:")
                        for e in interactive_elements[:20]:  # Show first 20
                            print(f"  [{e['label']}] {e['name']} ({e['control_type']})")
                        if len(interactive_elements) > 20:
                            print(f"  ... and {len(interactive_elements) - 20} more")
            except Exception as e:
                print(f"[HANDS DEBUG ERROR] Could not get interactive elements: {e}")
                import traceback
                traceback.print_exc()
            
            result = {
                "backend": "windows-mcp",
                "active_window_title": active_title or None,
                "active_window_handle": active_hwnd if active_hwnd else None,
                "windows": windows,
                "interactive_elements": interactive_elements,
                "element_count": len(interactive_elements),
            }
            
            # Handle screenshot
            if include_screenshot:
                print(f"[HANDS DEBUG] Capturing screenshot...")
                screenshot = self._get_annotated_screenshot(interactive_elements)
                result["screenshot_base64_png"] = screenshot
                print(f"[HANDS DEBUG] Screenshot captured: {len(screenshot) if screenshot else 0} chars")
            
            print(f"[HANDS DEBUG] observe() returning {len(interactive_elements)} elements")
            return result
            
        except Exception as e:
            print(f"[HANDS DEBUG ERROR] observe() failed: {e}")
            import traceback
            traceback.print_exc()
            # Fallback to basic observation
            return {
                "backend": "windows-mcp",
                "error": str(e),
                "active_window_title": None,
                "windows": [],
                "interactive_elements": [],
                "element_count": 0,
                "screenshot_base64_png": capture_png_base64() if include_screenshot else None,
            }

    def _get_windows_list(self) -> list[dict]:
        """Get list of open windows."""
        windows = []
        
        def callback(hwnd, _):
            if self._win32gui.IsWindowVisible(hwnd):
                title = self._win32gui.GetWindowText(hwnd)
                if title:
                    is_minimized = self._win32gui.IsIconic(hwnd)
                    windows.append({
                        "name": title,
                        "handle": hwnd,
                        "is_minimized": is_minimized,
                    })
        
        try:
            self._win32gui.EnumWindows(callback, None)
        except:
            pass
        
        return windows

    def _get_interactive_elements(self, root, window_name: str, max_depth: int = 10) -> list[TreeElementNode]:
        """Extract interactive elements from UI tree with deeper traversal."""
        elements = []
        visited = 0
        max_nodes = 1000  # Increased from 500
        
        # Interactive control types - expanded list
        interactive_types = {
            'ButtonControl', 'CheckBoxControl', 'ComboBoxControl', 'EditControl',
            'HyperlinkControl', 'ListControl', 'ListItemControl', 'MenuControl',
            'MenuItemControl', 'RadioButtonControl', 'SliderControl', 'SpinnerControl',
            'TabControl', 'TabItemControl', 'TextControl', 'ToolBarControl',
            'TreeControl', 'TreeItemControl', 'WindowControl', 'PaneControl',
            'DataItemControl', 'CalendarControl', 'CustomControl', 'GroupControl',
            'ImageControl', 'ProgressBarControl', 'ScrollBarControl', 'SeparatorControl',
            'SplitButtonControl', 'StatusBarControl', 'TableControl', 'ThumbControl',
            'TitleBarControl', 'ToolTipControl', 'ToggleSwitchControl', 'SwitchControl'
        }
        
        def walk(control, depth: int):
            nonlocal visited
            if depth <= 0 or visited >= max_nodes:
                return
            
            try:
                visited += 1
                
                # Get control properties
                control_type = getattr(control, 'ControlTypeName', '')
                name = getattr(control, 'Name', '') or ''
                
                # Check if interactive
                if control_type in interactive_types:
                    try:
                        rect = getattr(control, 'BoundingRectangle', None)
                        if rect:
                            bbox = BoundingBox(
                                left=int(getattr(rect, 'left', 0)),
                                top=int(getattr(rect, 'top', 0)),
                                right=int(getattr(rect, 'right', 0)),
                                bottom=int(getattr(rect, 'bottom', 0)),
                                width=int(getattr(rect, 'width', lambda: 0)()),
                                height=int(getattr(rect, 'height', lambda: 0)())
                            )
                            
                            # Skip invalid/invisible elements
                            if bbox.width > 0 and bbox.height > 0:
                                is_offscreen = getattr(control, 'IsOffscreen', False)
                                is_enabled = getattr(control, 'IsEnabled', True)
                                
                                if not is_offscreen and is_enabled:
                                    # Get additional properties
                                    is_focused = getattr(control, 'HasKeyboardFocus', False)
                                    value = ""
                                    shortcut = ""
                                    
                                    # Try to get value from legacy pattern
                                    try:
                                        legacy = control.GetLegacyIAccessiblePattern()
                                        if legacy:
                                            value = getattr(legacy, 'Value', '') or ""
                                    except:
                                        pass
                                    
                                    try:
                                        shortcut = getattr(control, 'AcceleratorKey', '') or ""
                                    except:
                                        pass
                                    
                                    elem = TreeElementNode(
                                        name=name.strip(),
                                        control_type=control_type.replace('Control', '').lower(),
                                        bounding_box=bbox,
                                        center=bbox.get_center(),
                                        window_name=window_name,
                                        xpath="",
                                        value=value.strip(),
                                        shortcut=shortcut,
                                        is_focused=is_focused
                                    )
                                    elements.append(elem)
                    except Exception as e:
                        pass
                
                # Walk children
                try:
                    children = control.GetChildren()
                    for child in children[:100]:  # Increased from 50 to capture more elements
                        walk(child, depth - 1)
                except:
                    pass
                    
            except Exception as e:
                pass
        
        walk(root, max_depth)
        return elements

    def _get_annotated_screenshot(self, elements: list[dict]) -> str | None:
        """Capture screenshot with element annotations."""
        try:
            from PIL import Image, ImageDraw, ImageFont
            
            # Get screenshot
            screenshot = self._pyautogui.screenshot()
            draw = ImageDraw.Draw(screenshot)
            
            # Font for labels
            try:
                font = ImageFont.truetype("arial.ttf", 12)
            except:
                font = ImageFont.load_default()
            
            # Draw annotations for each element
            for elem in elements:
                label = elem["label"]
                x, y = elem["coordinates"]
                
                # Random color for this element
                color = f"#{random.randint(0, 0xFFFFFF):06x}"
                
                # Draw circle at click point
                radius = 8
                draw.ellipse([x - radius, y - radius, x + radius, y + radius], 
                           outline=color, width=2)
                
                # Draw label
                label_text = str(label)
                bbox = draw.textbbox((0, 0), label_text, font=font)
                text_width = bbox[2] - bbox[0]
                text_height = bbox[3] - bbox[1]
                
                # Label background
                label_x = x + radius + 2
                label_y = y - text_height // 2
                draw.rectangle([label_x, label_y, label_x + text_width + 4, label_y + text_height + 4], 
                             fill=color)
                draw.text((label_x + 2, label_y + 2), label_text, fill="white", font=font)
            
            # Convert to base64
            buffered = io.BytesIO()
            screenshot.save(buffered, format="PNG")
            return base64.b64encode(buffered.getvalue()).decode('ascii')
            
        except Exception as e:
            # Fallback to basic screenshot
            return capture_png_base64()

    async def act(self, *, actions: list[dict[str, Any]]) -> dict[str, Any]:
        """
        Execute GUI actions.
        """
        performed: list[dict[str, Any]] = []
        
        for action in actions:
            kind = str(action.get("type") or "").lower()
            
            try:
                if kind == "click":
                    result = self._handle_click(action)
                    performed.append(result)
                
                elif kind == "type":
                    result = self._handle_type(action)
                    performed.append(result)
                
                elif kind == "scroll":
                    result = self._handle_scroll(action)
                    performed.append(result)
                
                elif kind == "shortcut":
                    result = self._handle_shortcut(action)
                    performed.append(result)
                
                elif kind == "hotkey":
                    result = self._handle_hotkey(action)
                    performed.append(result)
                
                elif kind == "wait":
                    ms = int(action.get("ms") or 0)
                    if ms > 0:
                        time.sleep(ms / 1000.0)
                    performed.append({"type": kind, "ok": True, "ms": ms})
                
                elif kind == "launch":
                    result = self._handle_launch(action)
                    performed.append(result)
                
                elif kind == "app":
                    result = self._handle_app(action)
                    performed.append(result)
                
                else:
                    performed.append({"type": kind or "unknown", "ok": False, 
                                    "error": f"Unknown action type: {kind}"})
                    
            except Exception as e:
                performed.append({"type": kind or "unknown", "ok": False, "error": str(e)})
        
        return {
            "success": all(a.get("ok") for a in performed),
            "performed_actions": performed,
            "notes": None,
        }

    def _handle_click(self, action: dict[str, Any]) -> dict[str, Any]:
        """Handle click action."""
        button = action.get("button", "left")
        clicks = int(action.get("clicks", 1))
        
        # Get coordinates
        x, y = None, None
        
        if "element_label" in action:
            # Get current state to resolve label
            label = action["element_label"]
            root = self._auto.GetForegroundControl()
            if root:
                elements = self._get_interactive_elements(root, "")
                if label < len(elements):
                    x, y = elements[label].center.x, elements[label].center.y
                else:
                    return {"type": "click", "ok": False, 
                           "error": f"Element label {label} not found"}
        elif "x" in action and "y" in action:
            x, y = int(action["x"]), int(action["y"])
        elif "name" in action or "query" in action:
            query = action.get("name") or action.get("query")
            if not query:
                return {"type": "click", "ok": False, 
                       "error": "Click by name/query requires a valid name or query"}
            root = self._auto.GetForegroundControl()
            if root:
                elements = self._get_interactive_elements(root, "")
                best_match = None
                best_score = 0
                for elem in elements:
                    from fuzzywuzzy import fuzz
                    score = fuzz.partial_ratio(query.lower(), elem.name.lower())
                    if score > best_score and score > 60:
                        best_score = score
                        best_match = elem
                
                if best_match:
                    x, y = best_match.center.x, best_match.center.y
                else:
                    return {"type": "click", "ok": False, 
                           "error": f"Could not find element matching '{query}'"}
        
        if x is None or y is None:
            return {"type": "click", "ok": False, 
                   "error": "Click requires element_label, x/y coordinates, or name/query"}
        
        # Perform click
        self._pyautogui.click(x, y, button=button, clicks=clicks)
        return {"type": "click", "ok": True, "coordinates": [x, y], 
               "button": button, "clicks": clicks}

    def _handle_type(self, action: dict[str, Any]) -> dict[str, Any]:
        """Handle type action.

        Uses clipboard-paste (Ctrl+V) for reliability on modern web forms with
        autocomplete/autosuggest.  Falls back to pyautogui.typewrite() only when
        the clipboard approach fails.
        """
        text = str(action.get("text", ""))
        clear = action.get("clear", False)
        press_enter = action.get("submit", False) or action.get("press_enter", False)
        caret_position = action.get("caret_position", "idle")
        
        # Get coordinates
        x, y = None, None
        if "element_label" in action:
            label = action["element_label"]
            root = self._auto.GetForegroundControl()
            if root:
                elements = self._get_interactive_elements(root, "")
                if label < len(elements):
                    x, y = elements[label].center.x, elements[label].center.y
                else:
                    return {"type": "type", "ok": False, 
                           "error": f"Element label {label} not found"}
        elif "x" in action and "y" in action:
            x, y = int(action["x"]), int(action["y"])
        elif "name" in action or "query" in action:
            # Resolve by name/query just like click does.
            query = action.get("name") or action.get("query")
            if query:
                root = self._auto.GetForegroundControl()
                if root:
                    elements = self._get_interactive_elements(root, "")
                    best_match = None
                    best_score = 0
                    for elem in elements:
                        from fuzzywuzzy import fuzz
                        score = fuzz.partial_ratio(query.lower(), elem.name.lower())
                        if score > best_score and score > 60:
                            best_score = score
                            best_match = elem
                    if best_match:
                        x, y = best_match.center.x, best_match.center.y
        
        # Click to focus if coordinates provided
        if x is not None and y is not None:
            self._pyautogui.click(x, y)
            time.sleep(0.15)
        
        # Handle caret position
        if caret_position == "start":
            self._pyautogui.press("home")
        elif caret_position == "end":
            self._pyautogui.press("end")
        
        # Clear if requested
        if clear:
            self._pyautogui.hotkey("ctrl", "a")
            self._pyautogui.press("backspace")
            time.sleep(0.1)
        
        # Type text via clipboard-paste for reliability (handles autocomplete, non-ASCII, speed).
        self._clipboard_type(text)
        
        # Small pause so autocomplete UIs have time to react.
        time.sleep(0.15)
        
        # Press enter if requested
        if press_enter:
            self._pyautogui.press("enter")
        
        return {"type": "type", "ok": True, "text": text, "coordinates": [x, y] if x else None,
               "clear": clear, "press_enter": press_enter}

    def _clipboard_type(self, text: str) -> None:
        """Type text by pasting from the clipboard.  Much more reliable than
        character-by-character typewrite on web forms with autocomplete."""
        import ctypes
        try:
            # Save current clipboard.
            self._pyautogui.hotkey("ctrl", "c")  # no-op copy to warm clipboard
            time.sleep(0.05)

            # Put text on the clipboard via win32.
            CF_UNICODETEXT = 13
            kernel32 = ctypes.windll.kernel32
            user32 = ctypes.windll.user32

            user32.OpenClipboard(0)
            user32.EmptyClipboard()
            # Encode to UTF-16LE with null terminator.
            data = text.encode("utf-16le") + b"\x00\x00"
            h_mem = kernel32.GlobalAlloc(0x0042, len(data))  # GMEM_MOVEABLE | GMEM_ZEROINIT
            ptr = kernel32.GlobalLock(h_mem)
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(h_mem)
            user32.SetClipboardData(CF_UNICODETEXT, h_mem)
            user32.CloseClipboard()

            # Paste.
            self._pyautogui.hotkey("ctrl", "v")
            time.sleep(0.1)
        except Exception:
            # Fallback to typewrite (ASCII only, slower).
            self._pyautogui.typewrite(text, interval=0.02)

    def _handle_scroll(self, action: dict[str, Any]) -> dict[str, Any]:
        """Handle scroll action."""
        direction = action.get("direction", "down")
        wheel_times = int(action.get("wheel_times", 1))
        
        # Get coordinates if specified
        x, y = None, None
        if "element_label" in action:
            label = action["element_label"]
            root = self._auto.GetForegroundControl()
            if root:
                elements = self._get_interactive_elements(root, "")
                if label < len(elements):
                    x, y = elements[label].center.x, elements[label].center.y
        elif "x" in action and "y" in action:
            x, y = int(action["x"]), int(action["y"])
        
        # Move to coordinates if provided
        if x is not None and y is not None:
            self._pyautogui.moveTo(x, y)
        
        # Perform scroll
        if direction in ("down", "right"):
            self._pyautogui.scroll(-wheel_times * 3)
        else:
            self._pyautogui.scroll(wheel_times * 3)
        
        return {"type": "scroll", "ok": True, "direction": direction, 
               "wheel_times": wheel_times, "coordinates": [x, y] if x else None}

    def _handle_shortcut(self, action: dict[str, Any]) -> dict[str, Any]:
        """Handle keyboard shortcut."""
        shortcut = action.get("shortcut", "")
        keys = [k.strip() for k in shortcut.split("+")]
        
        # Normalize keys
        normalized = []
        for k in keys:
            k = k.lower()
            if k in ("control", "ctl"):
                k = "ctrl"
            elif k in ("windows", "win"):
                k = "win"
            elif k == "esc":
                k = "escape"
            normalized.append(k)
        
        if len(normalized) > 1:
            self._pyautogui.hotkey(*normalized)
        elif normalized:
            self._pyautogui.press(normalized[0])
        
        return {"type": "shortcut", "ok": True, "shortcut": shortcut}

    def _handle_hotkey(self, action: dict[str, Any]) -> dict[str, Any]:
        """Handle legacy hotkey format."""
        keys = action.get("keys", [])
        value = action.get("value", "")
        
        if keys:
            shortcut = "+".join(keys)
        elif value:
            shortcut = value
        else:
            return {"type": "hotkey", "ok": False, "error": "hotkey requires keys or value"}
        
        return self._handle_shortcut({"shortcut": shortcut})

    def _force_foreground(self, hwnd: int) -> bool:
        """Reliably bring a window to the foreground, bypassing Windows focus-stealing prevention."""
        import ctypes
        try:
            if self._win32gui.IsIconic(hwnd):
                self._win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
            # The Alt-key trick: press and release Alt so Windows thinks we are
            # responding to user input, which allows SetForegroundWindow to succeed.
            ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)        # Alt down
            ctypes.windll.user32.keybd_event(0x12, 0, 0x0002, 0)   # Alt up
            result = ctypes.windll.user32.SetForegroundWindow(hwnd)
            return bool(result)
        except Exception:
            return False

    def _handle_launch(self, action: dict[str, Any]) -> dict[str, Any]:
        """Handle app launch, wait for its window to appear, and reliably focus it."""
        app_name = action.get("app", "")
        cwd = action.get("cwd")  # optional working directory

        # If the app is Windows Terminal, force a NEW window (not a tab) and
        # optionally start in a specific directory.
        effective_cmd = app_name
        if app_name.strip().lower() in ("wt", "wt.exe", "windows terminal"):
            if cwd:
                effective_cmd = f'wt.exe -w new -d "{cwd}"'
            else:
                effective_cmd = "wt.exe -w new"

        # Snapshot window handles before launch
        before_hwnds: set[int] = set()
        try:
            def _collect_before(hwnd, _):
                if self._win32gui.IsWindowVisible(hwnd) and self._win32gui.GetWindowText(hwnd):
                    before_hwnds.add(hwnd)
            self._win32gui.EnumWindows(_collect_before, None)
        except Exception:
            pass

        # Launch the process
        try:
            subprocess.Popen(effective_cmd, shell=True)
        except Exception as e:
            return {"type": "launch", "ok": False, "app": app_name, "error": str(e)}

        # Wait for a new window to appear (up to 5 s)
        new_hwnd = None
        for _ in range(25):  # 25 × 0.2 s = 5 s
            time.sleep(0.2)
            try:
                candidates: list[int] = []
                def _collect_after(hwnd, _):
                    if self._win32gui.IsWindowVisible(hwnd) and self._win32gui.GetWindowText(hwnd):
                        if hwnd not in before_hwnds:
                            candidates.append(hwnd)
                self._win32gui.EnumWindows(_collect_after, None)
                if candidates:
                    new_hwnd = candidates[0]
                    break
            except Exception:
                pass

        # If we didn't detect a brand-new hwnd (e.g. WT reused a process),
        # find the most recently-activated terminal-looking window.
        if new_hwnd is None:
            try:
                terminal_keywords = ("powershell", "cmd", "terminal", "windows terminal", "pwsh")
                all_windows = self._get_windows_list()
                for w in all_windows:
                    if any(kw in w["name"].lower() for kw in terminal_keywords):
                        new_hwnd = w["handle"]
                        break
            except Exception:
                pass

        if new_hwnd:
            self._force_foreground(new_hwnd)
            time.sleep(0.3)
            title = ""
            try:
                title = self._win32gui.GetWindowText(new_hwnd)
            except Exception:
                pass
            return {"type": "launch", "ok": True, "app": app_name,
                    "result": f"Launched and focused: {title}",
                    "focused_window": title}

        return {"type": "launch", "ok": True, "app": app_name,
                "result": f"Launched {app_name} (could not confirm new window)"}

    def _handle_app(self, action: dict[str, Any]) -> dict[str, Any]:
        """Handle app management."""
        mode = action.get("mode", "launch")
        name = action.get("name", "")
        
        if mode == "launch":
            return self._handle_launch({"app": name})
        elif mode == "switch":
            # Try to switch to app by name
            try:
                windows = self._get_windows_list()
                for w in windows:
                    if name.lower() in w["name"].lower():
                        hwnd = w["handle"]
                        if self._win32gui.IsIconic(hwnd):
                            self._win32gui.ShowWindow(hwnd, 9)  # SW_RESTORE
                        self._win32gui.SetForegroundWindow(hwnd)
                        return {"type": "app", "ok": True, "mode": mode, 
                               "name": name, "result": f"Switched to {w['name']}"}
                return {"type": "app", "ok": False, "mode": mode, 
                       "name": name, "error": f"Window '{name}' not found"}
            except Exception as e:
                return {"type": "app", "ok": False, "mode": mode, 
                       "name": name, "error": str(e)}
        else:
            return {"type": "app", "ok": False, "mode": mode, 
                   "name": name, "error": f"Unknown app mode: {mode}"}

    def _launch_from_start_menu(self, app_name: str) -> Optional[str]:
        """Try to launch app from Start Menu using PowerShell."""
        try:
            # Get apps from Start Menu
            command = 'Get-StartApps | Where-Object { $_.Name -like "*' + app_name + '*" } | Select-Object -First 1'
            encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
            result = subprocess.run(
                ['powershell', '-NoProfile', '-EncodedCommand', encoded],
                capture_output=True, text=True, timeout=10
            )
            
            if result.returncode == 0 and result.stdout.strip():
                # Parse output to get app ID
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if ':' in line:
                        parts = line.split(':')
                        if len(parts) >= 2:
                            app_id = parts[-1].strip()
                            if app_id:
                                # Launch the app
                                launch_cmd = f'Start-Process "shell:AppsFolder\\{app_id}"'
                                subprocess.run(
                                    ['powershell', '-NoProfile', '-Command', launch_cmd],
                                    timeout=10
                                )
                                return f"Launched {app_name} from Start Menu"
            
            return None
        except:
            return None
