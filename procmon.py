"""
Process Monitor Manager
双模式: GUI手动监控 + 后台自动监控（托盘图标）
"""
import psutil
import time
import sys
import subprocess
import ctypes
import os
import atexit
import threading
from ctypes import wintypes
from tkinter import *
from tkinter import ttk, filedialog, messagebox

# === Windows API 常量与结构体 ============================================

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIM_SETVERSION = 4
NIF_MESSAGE = 1
NIF_ICON = 2
NIF_TIP = 4
NIF_INFO = 0x10
NIF_SHOWTIP = 0x80
WM_USER = 0x0400
WM_TRAYICON = WM_USER + 100
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_COMMAND = 0x0111

shell32 = ctypes.windll.shell32
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HANDLE),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HANDLE),
    ]


def _load_tray_icon():
    """加载一个标准 Windows 图标作为托盘图标"""
    # IDI_SHIELD = 32518, IDI_APPLICATION = 32512
    hicon = user32.LoadIconW(None, 32518)
    if not hicon:
        hicon = user32.LoadIconW(None, 32512)
    return hicon


# 设置指针类型 API 的 argtypes
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_void_p
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
user32.SetWindowLongPtrW.restype = ctypes.c_void_p
user32.CallWindowProcW.argtypes = [
    ctypes.c_void_p, wintypes.HWND, wintypes.UINT,
    wintypes.WPARAM, wintypes.LPARAM
]
user32.CallWindowProcW.restype = ctypes.c_int64


class TrayIcon:
    """系统托盘图标 - 子类化 tkinter 窗口的 WndProc 来接收消息"""

    def __init__(self, root, callback, tooltip="进程监控管理器"):
        self.root = root
        self.callback = callback
        self.uid = 1001
        self.hicon = _load_tray_icon()
        self._old_proc = None
        self._proc_ptr = None

        # 获取 tkinter 顶层窗口的 HWND
        self.root.update_idletasks()
        hwnd = ctypes.cast(self.root.winfo_id(), wintypes.HWND)
        parent = user32.GetParent(hwnd)
        self.hwnd = parent if parent else hwnd

        # 读取旧的窗口过程指针
        self._old_proc = user32.GetWindowLongPtrW(self.hwnd, -4)
        if not self._old_proc:
            raise RuntimeError("无法获取窗口过程")

        # 创建新的窗口过程
        @ctypes.WINFUNCTYPE(ctypes.c_int64, wintypes.HWND, wintypes.UINT,
                            wintypes.WPARAM, wintypes.LPARAM)
        def wnd_proc(hw, msg, wp, lp):
            if msg == WM_TRAYICON:
                if lp & 0xFFFF == WM_LBUTTONUP:
                    callback("click")
                elif lp & 0xFFFF == WM_RBUTTONUP:
                    callback("right_click")
                return 0
            return user32.CallWindowProcW(self._old_proc, hw, msg, wp, lp)

        self._proc_ptr = wnd_proc
        user32.SetWindowLongPtrW(self.hwnd, -4,
                                  ctypes.cast(self._proc_ptr, ctypes.c_void_p))

        # 创建 Shell_NotifyIcon
        self.nid = NOTIFYICONDATAW()
        self.nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self.nid.hWnd = self.hwnd
        self.nid.uID = self.uid
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self.nid.uCallbackMessage = WM_TRAYICON
        self.nid.hIcon = self.hicon
        self.nid.szTip = tooltip

        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.nid))
        self.nid.uVersion = 0
        shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(self.nid))

    def set_tooltip(self, text):
        self.nid.szTip = text
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.nid))

    def remove(self):
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))
        if self._proc_ptr:
            user32.SetWindowLongPtrW(self.hwnd, -4,
                                      ctypes.cast(self._old_proc, ctypes.c_void_p))
            self._proc_ptr = None
        if self.hicon:
            user32.DestroyIcon(self.hicon)
            self.hicon = None


# === 核心逻辑 ============================================================

class MonitorCore:
    def __init__(self):
        self.main_pid = None
        self.main_name = ""
        self.running = False

    def start_program(self, cmd, *args):
        proc = subprocess.Popen([cmd, *args], shell=True)
        self.main_pid = proc.pid
        self.main_name = os.path.basename(cmd)
        return proc

    def attach(self, pid):
        self.main_pid = pid
        p = psutil.Process(pid)
        self.main_name = p.name()

    @staticmethod
    def get_window_handle(pid):
        result = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                pid_ptr = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_ptr))
                if pid_ptr.value == pid and user32.GetWindowTextLengthW(hwnd) > 0:
                    result.append(hwnd)
            return True

        user32.EnumWindows(callback, 0)
        return result[0] if result else None

    @staticmethod
    def pid_has_window(pid):
        hwnd = MonitorCore.get_window_handle(pid)
        return hwnd is not None and user32.IsWindow(hwnd)

    @staticmethod
    def is_window_gone(pid):
        hwnd = MonitorCore.get_window_handle(pid)
        if hwnd is None:
            return not psutil.pid_exists(pid)
        return not user32.IsWindow(hwnd)

    @staticmethod
    def get_process_tree(pid):
        items = []
        try:
            p = psutil.Process(pid)
            status = "运行中" if p.is_running() else "已停止"
            items.append((pid, p.name(), status))
            for child in p.children(recursive=True):
                try:
                    cs = "运行中" if child.is_running() else "已停止"
                    items.append((child.pid, child.name(), cs))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    items.append((child.pid, "?", "?"))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            items.append((pid, "?", "?"))
        return items

    @staticmethod
    def has_children(pid):
        try:
            p = psutil.Process(pid)
            return len(p.children()) > 0
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    @staticmethod
    def kill_tree(pid):
        killed = []
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for child in reversed(children):
                try:
                    child.kill()
                    killed.append(f"子进程 {child.pid} ({child.name()})")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            parent.kill()
            killed.append(f"主进程 {pid} ({parent.name()})")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return killed

    def monitor_loop(self, on_status, on_log, on_tree_update):
        self.running = True
        on_status("监控中", "green")
        on_log(f"开始监控: {self.main_name} (PID: {self.main_pid})")

        while self.running:
            if not psutil.pid_exists(self.main_pid):
                on_log("主进程已退出")
                break

            tree = self.get_process_tree(self.main_pid)
            on_tree_update(tree)

            if self.is_window_gone(self.main_pid):
                on_log("窗口已关闭，正在清理子进程...")
                killed = self.kill_tree(self.main_pid)
                for k in killed:
                    on_log(f"  x {k}")
                on_log("清理完成")
                break

            time.sleep(1)

        self.running = False
        on_status("已停止", "gray")


# === 自动监控 ============================================================

class AutoMonitor:
    EXCLUDE_PIDS = {0, 4}
    EXCLUDE_NAMES = {"svchost.exe", "csrss.exe", "winlogon.exe", "services.exe",
                     "lsass.exe", "wininit.exe", "smss.exe", "system",
                     "system idle process", "registry", "spoolsv.exe",
                     "conhost.exe", "sihost.exe", "taskhostw.exe"}

    def __init__(self, on_log=None, on_tracked=None):
        self.running = False
        self._thread = None
        self._tracked = {}
        self._paused = False
        self._log_callback = on_log
        self._tracked_callback = on_tracked

    def _log(self, msg):
        if self._log_callback:
            self._log_callback(msg)

    def _should_exclude(self, pid, name):
        return pid in self.EXCLUDE_PIDS or name.lower() in self.EXCLUDE_NAMES

    def _get_pid_window_handle(self, pid):
        result = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def callback(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                pid_ptr = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_ptr))
                if pid_ptr.value == pid:
                    result.append(hwnd)
            return True

        user32.EnumWindows(callback, 0)
        return result[0] if result else None

    def _pid_has_window(self, pid):
        hwnd = self._get_pid_window_handle(pid)
        return hwnd is not None and user32.IsWindow(hwnd)

    def _has_children(self, pid):
        try:
            p = psutil.Process(pid)
            return len(p.children()) > 0
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def _kill_tree(self, pid):
        killed = []
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            for child in reversed(children):
                try:
                    child.kill()
                    killed.append(f"{child.pid} ({child.name()})")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            parent.kill()
            killed.append(f"{pid} ({parent.name()})")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return killed

    def loop(self):
        self._log("自动监控已启动")
        while self.running:
            if not self._paused:
                self._tick()
            time.sleep(2)
        self._log("自动监控已停止")

    def _tick(self):
        seen = set()

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                pid = proc.info["pid"]
                name = proc.info["name"]
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

            if self._should_exclude(pid, name):
                continue

            seen.add(pid)
            has_win = self._pid_has_window(pid)
            has_kids = self._has_children(pid)

            if not has_kids:
                continue

            if pid in self._tracked:
                info = self._tracked[pid]
                was_win = info.get("had_window", False)

                if was_win and not has_win and psutil.pid_exists(pid):
                    self._log(f"窗口关闭: {name} (PID: {pid}), 清理子进程...")
                    killed = self._kill_tree(pid)
                    for k in killed:
                        self._log(f"  x {k}")
                    del self._tracked[pid]
                    continue

                info["had_window"] = has_win
                info["children"] = len(list(self._get_children(pid)))
            else:
                if has_win:
                    self._tracked[pid] = {
                        "name": name,
                        "had_window": True,
                        "children": len(list(self._get_children(pid))),
                    }

        for pid in list(self._tracked.keys()):
            if pid not in seen:
                del self._tracked[pid]

        if self._tracked_callback:
            self._tracked_callback(self.get_tracked_list())

    def get_tracked_list(self):
        items = []
        for pid, info in sorted(self._tracked.items(), key=lambda x: x[1]["name"].lower()):
            items.append((pid, info["name"], f'监控中 ({info["children"]})'))
        return items

    @staticmethod
    def _get_children(pid):
        try:
            p = psutil.Process(pid)
            return p.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return []

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self.loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.running = False
        self._tracked.clear()

    def toggle_pause(self):
        self._paused = not self._paused
        return self._paused

    @property
    def paused(self):
        return self._paused

    @property
    def tracked_count(self):
        return len(self._tracked)


# === GUI 界面 ============================================================

class ProcessMonitorGUI:
    def __init__(self):
        self.root = Tk()
        self.root.title("进程监控管理器")
        self.root.geometry("750x560")
        self.root.resizable(False, False)

        self.core = MonitorCore()
        self.auto_monitor = AutoMonitor(on_log=self._log, on_tracked=self._on_tracked_update)
        self.monitor_thread = None
        self._tray = None
        self._in_auto_mode = False
        self._setup_ui()

        # 默认进入自动模式（隐藏手动面板）
        self.frame_manual.pack_forget()
        self.root.after(100, self._enter_auto_mode)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _setup_ui(self):
        main = Frame(self.root, padx=12, pady=12)
        main.pack(fill=BOTH, expand=True)

        # === 模式切换 ===
        frame_mode = Frame(main)
        frame_mode.pack(fill=X, pady=(0, 8))

        Label(frame_mode, text="监控模式:", font=("", 10, "bold")).pack(side=LEFT, padx=(0, 10))
        self.mode_var = StringVar(value="auto")
        Radiobutton(frame_mode, text="手动（选择单个程序）",
                    variable=self.mode_var, value="manual",
                    command=self._switch_mode).pack(side=LEFT, padx=(0, 10))
        Radiobutton(frame_mode, text="自动（监控所有进程）",
                    variable=self.mode_var, value="auto",
                    command=self._switch_mode).pack(side=LEFT)

        # === 手动模式：程序选择 ===
        self.frame_manual = LabelFrame(main, text="目标程序", padx=8, pady=8)

        self.path_var = StringVar()
        entry = ttk.Entry(self.frame_manual, textvariable=self.path_var)
        entry.pack(side=LEFT, fill=X, expand=True, padx=(0, 8))
        ttk.Button(self.frame_manual, text="浏览", width=8, command=self._browse).pack(side=LEFT, padx=(0, 4))
        self.btn_start = ttk.Button(self.frame_manual, text="启动并监控", command=self._start_monitor)
        self.btn_start.pack(side=LEFT)
        self.frame_manual.pack(fill=X, pady=(0, 8))

        # === 状态栏 ===
        frame_status = Frame(main)
        frame_status.pack(fill=X, pady=(0, 8))

        self.status_label = Label(frame_status, text="就绪", fg="gray", font=("", 10, "bold"))
        self.status_label.pack(side=LEFT)
        self.btn_stop = ttk.Button(frame_status, text="停止", command=self._stop_monitor, state=DISABLED)
        self.btn_stop.pack(side=RIGHT)

        # === 进程树 ===
        frame_tree = LabelFrame(main, text="进程列表", padx=4, pady=4)
        frame_tree.pack(fill=BOTH, expand=True, pady=(0, 8))

        columns = ("pid", "name", "status")
        self.tree = ttk.Treeview(frame_tree, columns=columns, show="headings", height=6)
        self.tree.heading("pid", text="PID")
        self.tree.heading("name", text="进程名")
        self.tree.heading("status", text="状态")
        self.tree.column("pid", width=80, anchor=CENTER)
        self.tree.column("name", width=300)
        self.tree.column("status", width=120, anchor=CENTER)

        scroll_tree = Scrollbar(frame_tree, orient=VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll_tree.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        scroll_tree.pack(side=RIGHT, fill=Y)

        # === 日志 ===
        frame_log = LabelFrame(main, text="运行日志", padx=4, pady=4)
        frame_log.pack(fill=BOTH, expand=True)

        self.log_text = Text(frame_log, height=8, wrap=WORD, state=DISABLED, font=("Consolas", 9))
        scroll_log = Scrollbar(frame_log, orient=VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll_log.set)
        self.log_text.pack(side=LEFT, fill=BOTH, expand=True)
        scroll_log.pack(side=RIGHT, fill=Y)

    def _switch_mode(self):
        if self.mode_var.get() == "auto":
            self.frame_manual.pack_forget()
            self._enter_auto_mode()
        else:
            self.frame_manual.pack(fill=X, pady=(0, 8))
            self._exit_auto_mode()

    def _create_tray(self):
        if self._tray:
            return

        def tray_callback(action):
            if action == "click":
                self.root.after(0, self._restore_from_tray)
            elif action == "right_click":
                self.root.after(0, self._show_tray_menu)

        self._tray = TrayIcon(self.root, tray_callback)

    def _remove_tray(self):
        if self._tray:
            self._tray.remove()
            self._tray = None

    def _show_tray_menu(self):
        menu = Menu(self.root, tearoff=False)
        menu.add_command(label="显示窗口", command=self._restore_from_tray)
        if self.auto_monitor.paused:
            menu.add_command(label="继续监控", command=self._tray_toggle_pause)
        else:
            menu.add_command(label="暂停监控", command=self._tray_toggle_pause)
        menu.add_separator()
        menu.add_command(label="退出", command=self._quit_all)

        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        menu.post(self.root.winfo_pointerx(), self.root.winfo_pointery())

    def _tray_toggle_pause(self):
        paused = self.auto_monitor.toggle_pause()
        self._log("自动监控已暂停" if paused else "自动监控已恢复")

    def _enter_auto_mode(self):
        if self._in_auto_mode:
            return
        self._in_auto_mode = True
        self.auto_monitor.start()
        self._set_status("自动监控中", "green")
        self._log("--- 进入自动模式 ---")
        self.btn_stop.configure(state=NORMAL, text="停止")
        self._create_tray()
        self._log("程序已最小化到系统托盘，双击托盘图标恢复窗口")

    def _exit_auto_mode(self):
        if not self._in_auto_mode:
            return
        self._in_auto_mode = False
        self.auto_monitor.stop()
        self._set_status("就绪", "gray")
        self._log("--- 退出自动模式 ---")
        self.btn_stop.configure(state=DISABLED, text="停止")
        self._update_tree([])
        self._remove_tray()

    def _restore_from_tray(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _browse(self):
        path = filedialog.askopenfilename(
            title="选择程序",
            filetypes=[("可执行文件", "*.exe"), ("快捷方式", "*.lnk"), ("所有文件", "*.*")]
        )
        if path:
            self.path_var.set(path)

    def _log(self, msg):
        self.root.after(0, self._append_log, msg)

    def _append_log(self, msg):
        self.log_text.configure(state=NORMAL)
        self.log_text.insert(END, f"[{time.strftime('%H:%M:%S')}] {msg}\n")
        self.log_text.see(END)
        self.log_text.configure(state=DISABLED)

    def _set_status(self, text, color="black"):
        self.status_label.configure(text=text, fg=color)

    def _update_tree(self, items):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for pid, name, status in items:
            self.tree.insert("", END, values=(pid, name, status))

    def _on_tree_update(self, items):
        self.root.after(0, self._update_tree, items)

    def _on_log(self, msg):
        self.root.after(0, self._append_log, msg)

    def _on_status(self, text, color):
        self.root.after(0, self._set_status, text, color)

    def _on_tracked_update(self, items):
        if self._in_auto_mode:
            self.root.after(0, self._update_tree, items)

    def _start_monitor(self):
        path = self.path_var.get().strip()
        if not path:
            messagebox.showwarning("提示", "请选择要监控的程序")
            return
        if not os.path.isfile(path):
            messagebox.showerror("错误", "文件不存在")
            return

        self.btn_start.configure(state=DISABLED)
        self.btn_stop.configure(state=NORMAL)

        self.core.start_program(path)
        self._update_tree(self.core.get_process_tree(self.core.main_pid))
        self._append_log(f"已启动: {path} (PID: {self.core.main_pid})")

        self.monitor_thread = threading.Thread(
            target=self.core.monitor_loop,
            args=(self._on_status, self._on_log, self._on_tree_update),
            daemon=True
        )
        self.monitor_thread.start()

    def _stop_monitor(self):
        if self._in_auto_mode:
            self.mode_var.set("manual")
            self._switch_mode()
            return

        if self.core.running and self.core.main_pid:
            self.core.running = False
            self._append_log("用户手动停止监控")
            killed = self.core.kill_tree(self.core.main_pid)
            for k in killed:
                self._append_log(f"  x {k}")

        self.btn_start.configure(state=NORMAL)
        self.btn_stop.configure(state=DISABLED)
        self._set_status("已停止", "gray")

    def _on_close(self):
        if self._in_auto_mode:
            self.root.withdraw()
            self._log("窗口已隐藏，双击托盘图标恢复")
        else:
            self._quit_all()

    def _quit_all(self):
        self._remove_tray()
        if self.auto_monitor.running:
            self.auto_monitor.stop()
        if self.core.running and self.core.main_pid:
            self.core.running = False
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# === 入口 ================================================================

def main():
    mutex_name = "Local\\ProcessMonitorManager"
    mutex = kernel32.CreateMutexW(None, False, mutex_name)
    if kernel32.GetLastError() == 183:
        # 已有一个实例在运行，尝试激活它的窗口
        hwnd = user32.FindWindowW(None, "进程监控管理器")
        if hwnd:
            user32.ShowWindow(hwnd, 1)   # SW_SHOWNORMAL
            user32.SetForegroundWindow(hwnd)
        return 1

    app = ProcessMonitorGUI()
    app.run()
    kernel32.ReleaseMutex(mutex)
    return 0


if __name__ == "__main__":
    sys.exit(main())
