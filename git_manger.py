#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
git_manger.py  ——  Git 上传辅助工具（GUI 版）
单文件版本：所有规则都在本文件顶部的【用户可修改配置区】里，
改完保存即可，无需任何外部配置文件。

功能：
  - 可视化 Git 常用操作（init / add / commit / remote / push / pull / status / branch 等）
  - 强制过滤隐私文件（代码层，永远不会被 git add 添加）
  - 分支管理、日志输出、进度显示、悬浮提示
  - 基于系统原生 Git，零第三方依赖，零外部配置文件
"""

import os
import sys
import io
import subprocess
import threading
import logging
import datetime
import traceback
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog, colorchooser

# ===========================================================================
#  ▼▼▼  用户可修改的配置区（改完保存即可，无需再改其他代码）  ▼▼▼
# ===========================================================================

# ① 强制忽略的文件名（代码层过滤，永远不会被 git add 添加）
#    在此列表里的文件，不管 .gitignore 怎么写，都绝对不会上传
FORCE_IGNORE_FILES = [
    "git_manger.py",             # 本工具自身
    "AGENTS.md",                 # 项目代理规则说明
]

# ② 强制忽略的文件后缀（小写匹配，如 .env 文件、.paml 项目配置等隐私文件）
FORCE_extra_suffixes = [
    ".env",       # 环境变量 / 密钥
    ".paml",      # 自定义项目配置 / 隐私参数
]

# ③ 强制忽略的目录名（匹配任意层级的同名目录，递归跳过整棵子树）
#    例：["chat_history", "tmp_uploads", "dist", "build"]
FORCE_IGNORE_DIRS = [
    "chat_history",       # AI 对话历史（隐私）
    "tmp_uploads",       # 临时上传目录
    "dist",              # 前端构建产物
    "build",             # 编译产物
    ".idea", ".vscode",  # IDE 配置
]

# ④ 自动忽略的隐藏文件（点文件，如 .netrc / .gitcredentials 等）
#    True = 所有 "." 开头文件都忽略（.gitignore / .gitattributes 除外）
AUTO_IGNORE_DOTFILES = True

# ⑤ 自动忽略的常见构建 / 依赖目录名（会递归跳过）
SKIP_DIR_NAMES = {
    ".git", ".svn", ".hg", "__pycache__",
    ".venv", "venv", "node_modules",
}

# ⑥ 可额外忽略的后缀（可在此直接添加，或在 GUI 设置里临时添加）
EXTRA_extra_suffixes = [".bak", ".tmp", ".log", ".sqlite3", ".db", ".pyc"]

# ⑦ 可额外忽略的文件名（可在此直接添加，或在 GUI 设置里临时添加）
EXTRA_extra_names = ["credentials.json", "id_rsa", "id_ed25519", ".netrc"]

# ⑧ GUI 外观 / 功能默认值
DEFAULT_BRANCH = "main"
DEFAULT_COMMIT_MSG = "chore: auto commit"
DEFAULT_THEME = 1          # 1~7 皮肤；0 表示暗黑
DEFAULT_LANGUAGE = "zh"     # zh / en
BTN_COLOR1 = "#4B9956"
BTN_COLOR2 = "#2E7D32"

# ===========================================================================
#  ▲▲▲  用户可修改的配置区  END  ▲▲▲
# ===========================================================================


# ---------------------------------------------------------------------------
# 基础路径与目录适配
# ---------------------------------------------------------------------------


def is_windows() -> bool:
    return os.name == "nt"


def _safe_get_real_executable() -> str:
    """获取可执行文件或脚本真实路径。"""
    try:
        if getattr(sys, "frozen", False):
            return sys.executable
        # Nuitka 打包：sys.argv[0] 以 .exe 结尾
        if is_windows() and sys.argv and sys.argv[0].lower().endswith(".exe"):
            return os.path.abspath(sys.argv[0])
        return os.path.abspath(__file__)
    except Exception:
        return os.path.abspath(sys.argv[0])


def get_program_dir() -> str:
    """三重判断获取程序所在目录（PyInstaller / Nuitka / 脚本）。"""
    try:
        exe = _safe_get_real_executable()
        return os.path.dirname(os.path.realpath(exe))
    except Exception:
        try:
            return os.path.dirname(os.path.realpath(__file__))
        except Exception:
            return os.path.abspath(os.getcwd())


PROGRAM_DIR = get_program_dir()

# ---------------------------------------------------------------------------
# 日志（控制台 + GUI 回调）
# ---------------------------------------------------------------------------

_LOGGER = logging.getLogger("git_manger")
_LOGGER.setLevel(logging.INFO)
if not _LOGGER.handlers:
    _hdlr = logging.StreamHandler(sys.stdout)
    _hdlr.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s",
                                         datefmt="%H:%M:%S"))
    _LOGGER.addHandler(_hdlr)


def ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# Git 命令封装
# ---------------------------------------------------------------------------


class GitError(RuntimeError):
    pass


def run_git(args, cwd=None, check=True):
    """以 subprocess 调用系统 Git。返回 (returncode, stdout, stderr)。"""
    try:
        proc = subprocess.run(
            ["git"] + list(args),
            cwd=cwd or PROGRAM_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except FileNotFoundError:
        raise GitError("未检测到系统 Git，请先安装并配置环境变量。")
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def git_available() -> bool:
    try:
        rc, _, _ = run_git(["--version"])
        return rc == 0
    except Exception:
        return False


def is_git_repo(path=None) -> bool:
    try:
        rc, _, _ = run_git(["rev-parse", "--is-inside-work-tree"], cwd=path or PROGRAM_DIR)
        return rc == 0
    except Exception:
        return False


def list_tracked_files(path=None):
    rc, out, _ = run_git(["ls-files"], cwd=path or PROGRAM_DIR)
    return out.splitlines() if rc == 0 else []


def list_untracked_files(path=None):
    rc, out, _ = run_git(["ls-files", "--others", "--exclude-standard"],
                         cwd=path or PROGRAM_DIR)
    return out.splitlines() if rc == 0 else []


# ---------------------------------------------------------------------------
# 文件过滤规则（使用顶部【用户可修改配置区】的常量）
# ---------------------------------------------------------------------------

def _is_ignored(rel_path, extra_suffixes=None, extra_names=None):
    """判断相对路径是否应被忽略。rel_path 用正斜杠。"""
    if not rel_path:
        return True
    name = os.path.basename(rel_path)

    if name in FORCE_IGNORE_FILES:
        return True
    if extra_names and name in set(extra_names):
        return True

    low = name.lower()
    for suf in FORCE_extra_suffixes:
        if low.endswith(suf):
            return True
    if extra_suffixes:
        for suf in extra_suffixes:
            if low.endswith(suf.lower()):
                return True

    if AUTO_IGNORE_DOTFILES and low.startswith(".") and name not in (".gitignore", ".gitattributes"):
        return True
    return False


def scan_project_files(path=None, extra_suffixes=None, extra_names=None,
                       extra_dirs=None):
    """递归扫描项目文件，返回 [(绝对路径, 相对路径), ...] 过滤后列表。"""
    root = os.path.abspath(path or PROGRAM_DIR)
    results = []
    merged_suffixes = list(FORCE_extra_suffixes) + list(EXTRA_extra_suffixes)
    merged_names = list(FORCE_IGNORE_FILES) + list(EXTRA_extra_names)
    merged_dirs = set(SKIP_DIR_NAMES) | set(FORCE_IGNORE_DIRS) | set(extra_dirs or [])
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in merged_dirs]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                rel = os.path.relpath(full, root)
            except Exception:
                rel = fn
            rel_fwd = rel.replace(os.sep, "/")
            if _is_ignored(rel_fwd, extra_suffixes=merged_suffixes,
                           extra_names=merged_names):
                continue
            results.append((full, rel_fwd))
    return results


# ---------------------------------------------------------------------------
# 运行时配置（全部来自代码顶部常量，不再读写外部文件）
# ---------------------------------------------------------------------------

def make_runtime_cfg() -> dict:
    """用代码顶部的常量生成运行时配置 dict。"""
    return {
        "extra_suffixes": list(EXTRA_extra_suffixes),
        "extra_names": list(EXTRA_extra_names),
        "extra_dirs": list(FORCE_IGNORE_DIRS),
        "remote_url": "",
        "default_branch": DEFAULT_BRANCH,
        "last_commit_msg": DEFAULT_COMMIT_MSG,
        "language": DEFAULT_LANGUAGE,
        "theme": DEFAULT_THEME,
        "git_user_name": "",
        "git_user_email": "",
    }


# ---------------------------------------------------------------------------
# GUI 主程序
# ---------------------------------------------------------------------------

THEMES = [
    {"top_bg": "#BDC3DB", "bg": "#F9F9FB", "label_fg": "#FFFFFF"},
    {"top_bg": "#E6E6E6", "bg": "#E0FACF", "label_fg": "#FFFFFF"},
    {"top_bg": "#E4E4E4", "bg": "#EEDEE2", "label_fg": "#FFFFFF"},
    {"top_bg": "#E4E4E4", "bg": "#DEEFE5", "label_fg": "#FFFFFF"},
    {"top_bg": "#EBEBEB", "bg": "#E3EFFF", "label_fg": "#FFFFFF"},
    {"top_bg": "#ECDCE5", "bg": "#FFEAEA", "label_fg": "#FFFFFF"},
    {"top_bg": "#E0E0E0", "bg": "#E0FACF", "label_fg": "#FFFFFF"},
]
DARK_THEME = {"top_bg": "#1E1E1E", "bg": "#2B2B2B", "label_fg": "#FFFFFF"}

BTN_COLOR1 = "#4B9956"
BTN_COLOR2 = "#2E7D32"
TITLE_BG = "#F47524"
TITLE_FG = "#FFFAF0"


class ToolTip:
    """简单的 Tkinter Tooltip，500ms 延迟，3s 自动隐藏。"""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        self.after_show = None
        self._delay_ms = 500
        self._hide_ms = 3000
        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)
        widget.bind("<Button>", self._on_leave)

    def _on_enter(self, _event):
        self._schedule_show()

    def _on_leave(self, _event=None):
        if self.after_show:
            self.widget.after_cancel(self.after_show)
            self.after_show = None
        self._hide()

    def _schedule_show(self):
        if self.after_show:
            return
        self.after_show = self.widget.after(self._delay_ms, self._show)

    def _show(self):
        self.after_show = None
        if self.tip_window or not self.text:
            return
        try:
            x, y, cx, cy = self.widget.bbox("insert") if self.widget.winfo_class() == "Entry" else (0, 0, 0, 0)
        except Exception:
            x = y = cx = cy = 0
        x = self.widget.winfo_rootx() + cx + 20
        y = self.widget.winfo_rooty() + cy + self.widget.winfo_height() + 5
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry("+%d+%d" % (x, y))
        label = tk.Label(tw, text=self.text, justify="left",
                         background="#FFFFE0", foreground="#333333",
                         relief="solid", borderwidth=1,
                         font=("Microsoft YaHei", 9))
        label.pack(ipadx=4, ipady=2)
        tw.after(self._hide_ms, self._hide)

    def _hide(self):
        if self.tip_window:
            try:
                self.tip_window.destroy()
            except Exception:
                pass
            self.tip_window = None


class DialogWithPaste(tk.Toplevel):
    """带粘贴按钮的输入对话框基类。"""

    def __init__(self, master, title, prompt="", initial="", width=60):
        super().__init__(master)
        self.title(title)
        self.configure(bg="#E8E8E8")
        self.resizable(False, False)
        self.result = None
        self._build(prompt, initial, width)
        self.transient(master)
        self.grab_set()
        self._center(master)

    def _center(self, master):
        try:
            self.update_idletasks()
            mw = master.winfo_width()
            mh = master.winfo_height()
            self.update_idletasks()
            w = self.winfo_width(); h = self.winfo_height()
            x = master.winfo_rootx() + max(0, (mw - w) // 2)
            y = master.winfo_rooty() + max(0, (mh - h) // 2)
            self.geometry("+%d+%d" % (x, y))
        except Exception:
            pass

    def _build(self, prompt, initial, width):
        frm = tk.Frame(self, bg="#E8E8E8")
        frm.pack(padx=12, pady=10, fill="both", expand=True)
        if prompt:
            tk.Label(frm, text=prompt, bg="#E8E8E8", fg="#333333",
                     font=("Microsoft YaHei", 10)).pack(anchor="w")
        self.var = tk.StringVar(value=initial)
        self.entry = tk.Entry(frm, textvariable=self.var, width=width,
                              bg="#FFFFFF", fg="#4682B4",
                              insertbackground="#4682B4", relief="solid", bd=1)
        self.entry.pack(fill="x", pady=(4, 6))
        self.entry.focus_set()

        btns = tk.Frame(frm, bg="#E8E8E8")
        btns.pack(fill="x")
        paste_btn = tk.Button(btns, text="📋 粘贴", bg=BTN_COLOR1, fg="white",
                              activebackground=BTN_COLOR1,
                              activeforeground="white", relief="flat",
                              font=("Microsoft YaHei", 10, "bold"),
                              padx=10, pady=4, command=self._paste)
        paste_btn.pack(side="left", padx=(0, 6))
        ToolTip(paste_btn, "将剪贴板内容粘贴到输入框")

        ok_btn = tk.Button(btns, text="✅ 确定", bg=BTN_COLOR2, fg="white",
                           activebackground=BTN_COLOR2,
                           activeforeground="white", relief="flat",
                           font=("Microsoft YaHei", 10, "bold"),
                           padx=10, pady=4, command=self._ok)
        ok_btn.pack(side="left", padx=6)
        cancel_btn = tk.Button(btns, text="❌ 取消", bg=BTN_COLOR2, fg="white",
                               activebackground=BTN_COLOR2,
                               activeforeground="white", relief="flat",
                               font=("Microsoft YaHei", 10, "bold"),
                               padx=10, pady=4, command=self._cancel)
        cancel_btn.pack(side="left", padx=6)

        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", lambda e: self._cancel())

    def _paste(self):
        try:
            clip = self.clipboard_get()
        except Exception:
            return
        cur = self.var.get()
        self.entry.insert("insert", clip)
        self.var.set(self.entry.get())

    def _ok(self):
        self.result = self.var.get()
        self.destroy()

    def _cancel(self):
        self.result = None
        self.destroy()


class FloatingToast(tk.Toplevel):
    """悬浮提示窗，保留1秒后自动关闭。"""

    def __init__(self, master, text, color="#228B22"):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        tk.Label(self, text=text, bg="#FFFFE0", fg=color, padx=16, pady=8,
                 relief="solid", bd=1, font=("Microsoft YaHei", 11, "bold")
                 ).pack()
        self.update_idletasks()
        w = self.winfo_width(); h = self.winfo_height()
        mx = master.winfo_rootx() + master.winfo_width() // 2 - w // 2
        my = master.winfo_rooty() + master.winfo_height() // 2 - h // 2
        self.geometry("+%d+%d" % (mx, my))
        self.after(1000, self.destroy)




# ---- GitHub push helpers (GH007 noreply auto-fix) ----
def _gh_compliant_email(raw: str = "") -> str:
    """Return a noreply@users.noreply.github.com email.

    Using a GitHub-provided noreply email avoids GitHub's GH007
    privacy-protection rejects when the local commit author email
    is a private inbox address (e.g. 163.com, qq.com, gmail.com).
    """
    # If the user has already set a noreply address, don't force a new one.
    rc, out, _ = run_git(["config", "--global", "--get", "user.email"])
    if rc == 0 and "users.noreply.github.com" in out.strip():
        return out.strip()
    name = (raw or "").strip() or "github-user"
    name = re.sub(r'[^\w\u4e00-\u9fff.-]', '', name)
    if not name:
        name = "github-user"
    return "{}@users.noreply.github.com".format(name.lower())

def _read_remote_origin_url() -> str:
    """从 git config 读取 remote.origin.url，失败返回空字符串。"""
    try:
        rc, out, _ = run_git(['config', '--get', 'remote.origin.url'])
        if rc == 0:
            return out.strip()
    except Exception:
        pass
    return ""

def _ensure_github_identity(remote_url: str) -> None:
    """If origin is GitHub, ensure user.name/user.email are push-compliant.

    - Forces user.email to <name>@users.noreply.github.com so GH007 won't fire.
    - Uses current user.name if set; otherwise defaults to "github-user".
    """
    if not remote_url or "github.com" not in remote_url.lower():
        return
    rc_name, out_name, _ = run_git(["config", "--get", "user.name"])
    if rc_name != 0 or not out_name.strip():
        run_git(["config", "--global", "user.name", "github-user"])
    rc_email, out_email, _ = run_git(["config", "--global", "--get", "user.email"])
    need_noreply = False
    if rc_email != 0:
        need_noreply = True
    else:
        cur = out_email.strip().lower()
        if ("users.noreply.github.com" not in cur
                and ("@163.com" in cur or "@qq.com" in cur
                     or "@gmail.com" in cur or "@outlook.com" in cur
                     or "@yahoo." in cur or "@hotmail.com" in cur
                     or "noreply" not in cur)):
            need_noreply = True
    if need_noreply:
        rc_n, out_n, _ = run_git(["config", "--global", "--get", "user.name"])
        nm = out_n.strip() if rc_n == 0 and out_n.strip() else ""
        run_git(["config", "--global", "user.email", _gh_compliant_email(nm)])


def _working_tree_is_dirty() -> bool:
    """Return True if there are staged or unstaged changes."""
    try:
        rc, out, _ = run_git(['status', '--porcelain'])
        return rc == 0 and bool(out.strip())
    except Exception:
        return False

def _collect_changed_files() -> list:
    """List of added/modified/deleted/untracked files (porcelain pairs)."""
    try:
        rc, out, _ = run_git(['status', '--porcelain'])
        if rc != 0: return []
        rows = [l for l in out.splitlines() if l.strip()]
        # porcelain: first 2 chars = status, rest = file path (may have arrow ->)
        paths = []
        for l in rows:
            if len(l) < 3: continue
            pth = l[2:].strip()
            if pth.startswith('"') and pth.endswith('"'):
                try:
                    pth = bytes(pth[1:-1], 'utf-8').decode('unicode_escape')
                except Exception:
                    pass
            paths.append(pth.replace('\\', '/'))
        return paths
    except Exception:
        return []


def _build_gitignore_text() -> str:
    """Build .gitignore content from FORCE_IGNORE_* rules.

    Includes the tool's own files so repo never commits private
    files the helper itself may drop.
    """
    lines = [
        '# Auto-generated by git_manger.py - DO NOT EDIT MANUALLY',
        '#### Tool private (never commit) ####',
        'git_manger.py',
        'AGENTS.md',
        'config.yaml',
        '#### Suffix ignore ####',
        '*.env',
        '*.paml',
        '*.pyc',
        '*.pyo',
        '*.log',
        '*.tmp',
        '*.bak',
        '*.sqlite',
        '*.db',
        '*.sqlite3',
        '*.ini',
        '*.token',
        '*.key',
        '*.pem',
        '*.crt',
        '*.cer',
        '*.pfx',
        '*.p12',
        '#### Editor / OS ####',
        '.vscode/',
        '.idea/',
        '.DS_Store',
        'Thumbs.db',
        '#### Python cache ####',
        '__pycache__/',
        '*.egg-info/',
        '#### Node / JS ####',
        'node_modules/',
        '.npm/',
        '#### Build / dist ####',
        'tmp_uploads/',
        'dist/',
        'build/',
        '*.spec/',
        '#### Chat & logs (project private) ####',
        'chat_history/',
    ]
    return '\n'.join(lines) + '\n'

def _ensure_gitignore(project_dir: str) -> None:
    """Create/update .gitignore at project root with our rules.

    If .gitignore starts with our auto-gen marker -> overwrite (rules propagate).
    If .gitignore exists but is user-written -> append our block at bottom.
    """
    gip = os.path.join(project_dir, '.gitignore')
    want = _build_gitignore_text()
    try:
        cur = ''
        if os.path.exists(gip):
            with open(gip, 'r', encoding='utf-8', errors='ignore') as _f:
                cur = _f.read()
        if cur.lstrip().startswith('# Auto-generated by git_manger.py'):
            if cur.strip() != want.strip():
                with open(gip, 'w', encoding='utf-8') as _f:
                    _f.write(want)
            return
        block_lines = [
            '',
            '#### Added by git_manger.py ####',
            _build_gitignore_text().strip(),
            '#### End git_manger block ####',
            '',
        ]
        block = '\n'.join(block_lines)
        if '#### Added by git_manger.py ####' not in cur:
            with open(gip, 'w', encoding='utf-8') as _f:
                _f.write(cur.rstrip() + '\n' + block)
    except Exception:
        pass

def _path_matches_force_ignore(rel_path: str) -> bool:
    """Check if a repo-relative path should be filtered.

    Mirrors FORCE_IGNORE_* + FORCE_IGNORE_DIRS logic.
    """
    rp = rel_path.replace('\\', '/').strip('/')
    base = os.path.basename(rp)
    # tool private files
    if base in {'git_manger.py', 'AGENTS.md', 'config.yaml'}:
        return True
    # suffix
    low = rp.lower()
    for sf in ['.env', '.paml', '.pyc', '.pyo', '.log', '.tmp', '.bak',
               '.sqlite', '.db', '.sqlite3', '.ini', '.token', '.key',
               '.pem', '.crt', '.cer', '.pfx', '.p12']:
        if low.endswith(sf):
            return True
    # dirs
    for d in ('chat_history/', '.git/', '.svn/', '.hg/', 'node_modules/',
              'tmp_uploads/', 'dist/', 'build/', '.idea/', '.vscode/',
              '__pycache__/', '.cache/', '.npm/'):
        if rp.startswith(d) or ('/' + d.rstrip('/') + '/') in ('/' + rp.replace('\\', '/')):
            return True
    return False

def _prune_ignored_from_index() -> list:
    """Run `git rm --cached` on tracked files that match filter.

    Returns list of paths removed from index.
    """
    try:
        rc, out, _ = run_git(['ls-files', '-z'])
        if rc != 0 or not out:
            return []
        tracked = [p for p in out.split('\x00') if p]
    except Exception:
        return []
    to_remove = [p for p in tracked if _path_matches_force_ignore(p)]
    if not to_remove:
        return []
    # Execute git rm --cached for each (keeps local files, just stops tracking)
    try:
        # git rm --cached supports multiple paths
        run_git(['rm', '--cached', '--ignore-unmatch', '-r', '--'] + to_remove)
    except Exception:
        pass
    return to_remove

class GitManagerApp(tk.Tk):
    """Git 上传辅助工具主窗口。"""

    def __init__(self):
        super().__init__()
        self.cfg = make_runtime_cfg()
        self.theme_index = (self.cfg.get("theme", DEFAULT_THEME) or DEFAULT_THEME) - 1
        if self.theme_index < 0 or self.theme_index >= len(THEMES):
            self.theme_index = 0
        self.is_dark = False
        self._apply_theme()

        self.title("🔧 Git 上传辅助工具   v1.0")
        self.configure(bg=self._theme["bg"])
        try:
            icon_path = os.path.join(PROGRAM_DIR, "icon.ico")
            if os.path.exists(icon_path):
                self.iconbitmap(icon_path)
        except Exception:
            pass

        self.geometry("960x640")
        self.minsize(820, 580)

        self._status_vars = {
            "git": tk.BooleanVar(value=git_available()),
            "repo": tk.BooleanVar(value=is_git_repo()),
        }
        self._build_ui()
        self._refresh_status()
        self._log("ℹ️ 程序已启动，工作目录：{}".format(PROGRAM_DIR), "INFO")

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -------- 主题 --------
    def _apply_theme(self):
        if self.is_dark:
            self._theme = DARK_THEME
        else:
            self._theme = THEMES[self.theme_index]

    def _set_theme(self, idx=None, dark=None):
        if idx is not None:
            self.theme_index = max(0, min(len(THEMES) - 1, idx))
            self.is_dark = False
        if dark is not None:
            self.is_dark = bool(dark)
        self._apply_theme()
        self.cfg["theme"] = self.theme_index + 1
        self.destroy_widgets()
        self._build_ui()
        self._refresh_status()

    def destroy_widgets(self):
        for w in self.winfo_children():
            w.destroy()

    # -------- UI 构建 --------
    def _build_ui(self):
        th = self._theme
        # 顶部标题条
        top = tk.Frame(self, bg=TITLE_BG, height=40)
        top.pack(fill="x", side="top")
        top.pack_propagate(False)
        tk.Label(top, text="🔧  Git 上传辅助工具",
                 bg=TITLE_BG, fg=TITLE_FG,
                 font=("Microsoft YaHei", 14, "bold")).pack(side="left", padx=14)

        # 状态小圆球 + 说明
        self._status_cvs = {}
        for key, label in (("git", "Git"), ("repo", "Repo")):
            cv = tk.Canvas(top, width=16, height=16, bg=TITLE_BG, highlightthickness=0)
            cv.pack(side="left", padx=(6, 2))
            cv.create_oval(2, 2, 14, 14, fill="#999999", outline="")
            self._status_cvs[key] = cv
            tk.Label(top, text=label, bg=TITLE_BG, fg=TITLE_FG,
                     font=("Microsoft YaHei", 9)).pack(side="left")

        tk.Label(top, text="   📁 项目目录：{}".format(PROGRAM_DIR),
                 bg=TITLE_BG, fg=TITLE_FG,
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=10)

        # 顶部右按钮
        tk.Button(top, text="🎨 皮肤", bg=BTN_COLOR1, fg="white",
                  relief="flat", activebackground=BTN_COLOR1, activeforeground="white",
                  font=("Microsoft YaHei", 9), padx=8, pady=2,
                  command=self._cycle_theme).pack(side="right", padx=4)
        tk.Button(top, text="🌙 暗黑", bg=BTN_COLOR1, fg="white",
                  relief="flat", activebackground=BTN_COLOR1, activeforeground="white",
                  font=("Microsoft YaHei", 9), padx=8, pady=2,
                  command=self._toggle_dark).pack(side="right", padx=4)
        tk.Button(top, text="🌐 EN", bg=BTN_COLOR1, fg="white",
                  relief="flat", activebackground=BTN_COLOR1, activeforeground="white",
                  font=("Microsoft YaHei", 9), padx=8, pady=2,
                  command=self._toggle_lang).pack(side="right", padx=4)
        tk.Button(top, text="❓ 帮助", bg=BTN_COLOR2, fg="white",
                  relief="flat", activebackground=BTN_COLOR2, activeforeground="white",
                  font=("Microsoft YaHei", 9), padx=8, pady=2,
                  command=self._help_me).pack(side="right", padx=4)
        tk.Button(top, text="ℹ️ 关于", bg=BTN_COLOR2, fg="white",
                  relief="flat", activebackground=BTN_COLOR2, activeforeground="white",
                  font=("Microsoft YaHei", 9), padx=8, pady=2,
                  command=self._about_me).pack(side="right", padx=4)

        # 主体：左侧按钮 + 右侧日志
        body = tk.Frame(self, bg=th["bg"])
        body.pack(fill="both", expand=True, padx=8, pady=8)

        left = tk.Frame(body, bg=th["bg"])
        left.pack(side="left", fill="y", padx=(0, 8))

        tk.Label(left, text="📦 Git 操作", bg=th["bg"], fg="#87CEFA",
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(0, 4))

        # 第一行：颜色 1
        row1 = tk.Frame(left, bg=th["bg"]); row1.pack(fill="x", pady=2)
        for emoji, txt, cmd in [
            ("🚀", "初始化仓库", self._cmd_init),
            ("📦", "扫描并添加", self._cmd_add),
            ("✍️", "代码提交", self._cmd_commit),
        ]:
            b = self._mk_btn(row1, "{} {}".format(emoji, txt), BTN_COLOR1, cmd, width=14)
            b.pack(side="left", padx=3)
            ToolTip(b, txt + "（使用系统 Git）")

        row2 = tk.Frame(left, bg=th["bg"]); row2.pack(fill="x", pady=2)
        for emoji, txt, cmd in [
            ("🔗", "远程仓库", self._cmd_remote),
            ("🌐", "GitHub创建", self._cmd_github_create),
            ("⬆️", "推送到远程", self._cmd_push),
            ("⬇️", "拉取远程", self._cmd_pull),
        ]:
            b = self._mk_btn(row2, "{} {}".format(emoji, txt), BTN_COLOR2, cmd, width=12)
            b.pack(side="left", padx=3)
            ToolTip(b, txt)

        tk.Label(left, text="🌿 分支管理", bg=th["bg"], fg="#87CEFA",
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(10, 4))

        row3 = tk.Frame(left, bg=th["bg"]); row3.pack(fill="x", pady=2)
        for emoji, txt, cmd in [
            ("📋", "分支列表", self._cmd_branch_list),
            ("➕", "创建分支", self._cmd_branch_create),
            ("🔀", "切换分支", self._cmd_branch_switch),
            ("➕", "合并分支", self._cmd_branch_merge),
        ]:
            b = self._mk_btn(row3, "{} {}".format(emoji, txt), BTN_COLOR1, cmd, width=12)
            b.pack(side="left", padx=3)
            ToolTip(b, txt)

        tk.Label(left, text="🧹 其他", bg=th["bg"], fg="#87CEFA",
                 font=("Microsoft YaHei", 11, "bold")).pack(anchor="w", pady=(10, 4))

        row4 = tk.Frame(left, bg=th["bg"]); row4.pack(fill="x", pady=2)
        for emoji, txt, cmd in [
            ("📊", "查看状态", self._cmd_status),
            ("📜", "提交历史", self._cmd_log),
            ("🔑", "Git 设置", self._cmd_settings),
        ]:
            b = self._mk_btn(row4, "{} {}".format(emoji, txt), BTN_COLOR1, cmd, width=12)
            b.pack(side="left", padx=3)
            ToolTip(b, txt)

        row5 = tk.Frame(left, bg=th["bg"]); row5.pack(fill="x", pady=2)
        for emoji, txt, cmd in [
            ("🔓", "查看忽略", self._cmd_show_ignore),
            ("📁", "打开目录", self._cmd_open_dir),
            ("🔄", "刷新状态", self._refresh_status),
        ]:
            b = self._mk_btn(row5, "{} {}".format(emoji, txt), BTN_COLOR2, cmd, width=12)
            b.pack(side="left", padx=3)
            ToolTip(b, txt)

        # 文件列表区
        filebox_frame = tk.LabelFrame(left, text="📁 项目文件（过滤后）",
                                      bg=th["bg"], fg="#9ACD32",
                                      font=("Microsoft YaHei", 10, "bold"),
                                      bd=1, relief="solid")
        filebox_frame.pack(fill="both", expand=True, pady=(10, 4))
        self.filebox = tk.Listbox(filebox_frame, bg="#FFFFFF", fg="#4169E1",
                                  selectbackground="#ADD8E6",
                                  selectforeground="#101010",
                                  font=("Consolas", 9), activestyle="none",
                                  relief="flat", bd=0)
        sb = ttk.Scrollbar(filebox_frame, orient="vertical", command=self.filebox.yview)
        self.filebox.configure(yscrollcommand=sb.set)
        self.filebox.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        sb.pack(side="right", fill="y")

        # 右侧日志区：标题 + 按钮在同一行（标题在左，按钮在右）
        right = tk.Frame(body, bg=th["bg"])
        right.pack(side="left", fill="both", expand=True)

        log_frame = tk.Frame(right, bg=th["bg"], bd=1, relief="solid")
        log_frame.pack(fill="both", expand=True)

        log_head = tk.Frame(log_frame, bg=th["bg"])
        log_head.pack(fill="x", side="top")
        tk.Label(log_head, text="🖥️ 操作日志", bg=th["bg"], fg="#9ACD32",
                 font=("Microsoft YaHei", 10, "bold")).pack(side="left", padx=6, pady=4)
        for txt, cmd in [("🧹 清空", self._clear_log), ("📋 复制", self._copy_log)]:
            bclr = BTN_COLOR1 if txt == "🧹 清空" else BTN_COLOR2
            tk.Button(log_head, text=txt, bg=bclr, fg="white", relief="flat",
                      activebackground=bclr, activeforeground="white",
                      font=("Microsoft YaHei", 9, "bold"),
                      padx=10, pady=2, command=cmd).pack(side="right", padx=3, pady=3)

        self.log_text = tk.Text(log_frame, bg="#FFFFFF", fg="#008080",
                                font=("Consolas", 9), relief="flat", bd=0,
                                wrap="word", state="disabled")
        log_sb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_sb.set)
        self.log_text.pack(side="left", fill="both", expand=True, padx=2, pady=2)
        log_sb.pack(side="right", fill="y")

        # 左下角进度条
        self.progress = ttk.Progressbar(self, mode="determinate", length=200)
        self.progress.pack(fill="x", side="bottom")
        self.progress.configure(value=0, maximum=100)

        self.status_label = tk.Label(self, text="✅ 就绪", bg=TITLE_BG, fg=TITLE_FG,
                                     anchor="w", font=("Microsoft YaHei", 9))
        self.status_label.pack(fill="x", side="bottom")

        # 首次刷新文件列表
        self._refresh_filebox()

    def _mk_btn(self, parent, text, bg_color, command, width=None):
        b = tk.Button(parent, text=text, bg=bg_color, fg="white",
                      activebackground=bg_color, activeforeground="white",
                      relief="flat", bd=0, cursor="hand2",
                      font=("Microsoft YaHei", 10, "bold"),
                      padx=12, pady=4, command=command)
        if width:
            b.configure(width=width)
        return b

    # -------- 日志 --------
    def _log(self, msg, level="INFO"):
        color_map = {
            "SUCCESS": "#2E7D32",
            "ERROR": "#D32F2F",
            "WARN": "#F57C00",
            "INFO": "#1976D2",
            "DATA": "#512DA8",
        }
        color = color_map.get(level, "#222222")
        text = "[{}] {}\n".format(ts(), msg)
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", text, level)
            self.log_text.tag_config("SUCCESS", foreground=color_map["SUCCESS"])
            self.log_text.tag_config("ERROR", foreground=color_map["ERROR"])
            self.log_text.tag_config("WARN", foreground=color_map["WARN"])
            self.log_text.tag_config("INFO", foreground=color_map["INFO"])
            self.log_text.tag_config("DATA", foreground=color_map["DATA"])
            last_tag = level if level in color_map else "INFO"
            self.log_text.tag_add(last_tag, "end-{}l".format(len(text) + 1) + "-1c", "end-1c")
            # 正确做法：给整段新文本打 tag
            start = "end-{}l".format(len(self.log_text.get("1.0", "end-1c").splitlines()) + 1) if False else "end-2l"
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception:
            print(text)
        self.status_label.configure(text="ℹ️ " + msg[:80])

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _copy_log(self):
        try:
            self.clipboard_clear()
            txt = self.log_text.get("1.0", "end-1c")
            self.clipboard_append(txt)
            FloatingToast(self, "✅ 已复制日志")
        except Exception as e:
            FloatingToast(self, "❌ 复制失败")

    # -------- 状态 --------
    def _refresh_status(self):
        ok = git_available()
        self._status_vars["git"].set(ok)
        self._status_vars["repo"].set(is_git_repo())
        for key, var in self._status_vars.items():
            cv = self._status_cvs.get(key)
            if cv is None:
                continue
            cv.delete("all")
            color = "#4B9956" if var.get() else "#888888"
            cv.create_oval(2, 2, 14, 14, fill=color, outline="")
        self._refresh_filebox()

    def _refresh_filebox(self):
        self.filebox.delete(0, "end")
        files = scan_project_files(PROGRAM_DIR,
                                   extra_suffixes=self.cfg.get("extra_suffixes"),
                                   extra_names=self.cfg.get("extra_names"), extra_dirs=self.cfg.get("extra_dirs"))
        for full, rel in files:
            self.filebox.insert("end", rel)
        self.status_label.configure(
            text="📁 共 {} 个文件（已过滤隐私文件）".format(len(files)))

    # -------- 命令执行包装 --------
    def _run_async(self, func, *args, **kwargs):
        self.progress.configure(value=10, maximum=100)
        t = threading.Thread(target=self._safe_run, args=(func,) + args, kwargs=kwargs, daemon=True)
        t.start()

    def _safe_run(self, func, *args, **kwargs):
        try:
            func(*args, **kwargs)
        except GitError as e:
            self._log("❌ {}".format(e), "ERROR")
        except Exception as e:
            self._log("❌ 执行异常：{}".format(e), "ERROR")
            _LOGGER.error(traceback.format_exc())
        finally:
            self.progress.configure(value=100)
            self.after(400, lambda: self.progress.configure(value=0))
            self.after(200, self._refresh_status)

    def _exec_git(self, args, cwd=None, label="git", check=True):
        self.progress.configure(value=30)
        self._log("$ git {}".format(" ".join(args)), "INFO")
        rc, out, err = run_git(args, cwd=cwd or PROGRAM_DIR)
        self.progress.configure(value=80)
        if out:
            for line in out.splitlines()[:200]:
                self._log(line, "INFO")
            if len(out.splitlines()) > 200:
                self._log("...（输出已截断）", "INFO")
        if err:
            for line in err.splitlines()[:50]:
                self._log(line, "WARN" if rc != 0 else "INFO")
        if rc == 0:
            self._log("✅ {} 执行成功".format(label), "SUCCESS")
            self.progress.configure(value=100)
            return True
        if check:
            self._log("❌ {} 执行失败 (exit={})".format(label, rc), "ERROR")
        self.progress.configure(value=100)
        return False

    # -------- 具体操作 --------
    def _cmd_init(self):
        if is_git_repo():
            if not messagebox.askyesno("确认", "当前目录已是 Git 仓库，是否继续初始化？"):
                return
        self._run_async(lambda: self._exec_git(["init", "-b",
                        self.cfg.get("default_branch", "main")], label="init"))

    def _cmd_add(self):
        files = scan_project_files(PROGRAM_DIR,
                                   extra_suffixes=self.cfg.get("extra_suffixes"),
                                   extra_names=self.cfg.get("extra_names"), extra_dirs=self.cfg.get("extra_dirs"))
        if not files:
            self._log("⚠️ 没有可添加的文件（已全部过滤）", "WARN")
            return

        # 先重置再批量加
        def _do():
            self._exec_git(["reset"], label="reset")
            # 使用 -- 分隔，避免以 - 开头的文件名被误解析
            paths = [p.replace("\\", "/") for _, p in files]
            # Windows 下 subprocess 自动处理转义，使用 "--" 防止路径被识别为选项
            args = ["add", "--"] + paths
            self._exec_git(args, label="add ({}/{} 个)".format(len(paths), len(files)))

        self._run_async(_do)

    def _cmd_commit(self):
        if not is_git_repo():
            FloatingToast(self, "⚠️ 请先初始化仓库", "#D32F2F"); return
        dlg = DialogWithPaste(self, "代码提交", "请输入提交备注：",
                              initial=self.cfg.get("last_commit_msg", DEFAULT_COMMIT_MSG))
        self.wait_window(dlg)
        msg = (dlg.result or "").strip()
        if not msg:
            return
        self.cfg["last_commit_msg"] = msg
        self._run_async(lambda: self._exec_git(["commit", "-m", msg], label="commit"))

    def _cmd_remote(self):
        dlg = DialogWithPaste(self, "远程仓库", "输入远程仓库 URL（留空查看/删除）：",
                              initial=self.cfg.get("remote_url", ""))
        self.wait_window(dlg)
        url = (dlg.result or "").strip()
        if not url:
            self._run_async(lambda: self._exec_git(["remote", "-v"], label="remote -v"))
            return
        self.cfg["remote_url"] = url

        def _do():
            run_git(["remote", "remove", "origin"])  # 忽略失败
            self._exec_git(["remote", "add", "origin", url], label="remote add origin")
            self._exec_git(["remote", "-v"], label="remote -v")

        self._run_async(_do)


    def _github_cli_available(self) -> bool:
        try:
            p_ = subprocess.run(['gh', '--version'],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               shell=False)
            return p_.returncode == 0
        except Exception:
            return False

    def _curl_available(self) -> bool:
        try:
            p_ = subprocess.run(['curl', '--version'],
                               stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL,
                               shell=False)
            return p_.returncode == 0
        except Exception:
            return False

    def _github_auth_status(self) -> str:
        if not self._github_cli_available():
            return 'no_gh'
        try:
            p_ = subprocess.run(
                ['gh', 'auth', 'status'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                shell=False, text=True,
                encoding='utf-8', errors='replace')
            return 'logged_in' if p_.returncode == 0 else 'not_logged_in'
        except Exception:
            return 'no_gh'

    def _run_curl_create(self, repo_name, owner, visibility, desc, token):
        import json as _json
        if not self._curl_available():
            self._log('curl missing', 'ERROR'); return
        if not token:
            self._log('PAT empty', 'ERROR'); return
        if owner:
            api_url = 'https://api.github.com/orgs/{}/repos'.format(
                owner.rstrip('/'))
        else:
            api_url = 'https://api.github.com/user/repos'
        body = {'name': repo_name, 'visibility': visibility}
        if desc:
            body['description'] = desc
        args = [
            'curl', '-sS', '-X', 'POST', api_url,
            '-H', 'Authorization: Bearer {}'.format(token),
            '-H', 'Accept: application/vnd.github+json',
            '-H', 'X-GitHub-Api-Version: 2022-11-28',
            '-d', _json.dumps(body),
        ]
        self._log('$ curl POST {} (token hidden)'.format(api_url), 'INFO')
        try:
            p_ = subprocess.run(
                args, cwd=PROGRAM_DIR,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                shell=False, text=True,
                encoding='utf-8', errors='replace')
        except Exception as ex:
            self._log('curl fail: {}'.format(ex), 'ERROR'); return
        out = p_.stdout or ''; err = p_.stderr or ''
        if err:
            for line in err.splitlines()[:50]:
                self._log(line, 'WARN')
        try:
            data = _json.loads(out)
        except Exception:
            self._log('response parse failed', 'WARN'); return
        if p_.returncode == 0 and 'html_url' in data:
            url_clone = data.get('html_url', '')
            self.cfg['remote_url'] = url_clone
            self._log(
                'ok GitHub repo created (curl): {}'.format(url_clone), 'SUCCESS')
            self._run_async(
                lambda: self._apply_origin_and_push(url_clone))
        else:
            msg = data.get('message', (out or '')[:200])
            self._log('GitHub reject: {}'.format(msg), 'ERROR')

    def _find_gh_exe(self) -> str:
        import shutil as _sh
        path = _sh.which("gh")
        if path: return path
        import glob as _glob
        candidates = [
            r"C:\Program Files\GitHub CLI\gh.exe",
            r"C:\Program Files (x86)\GitHub CLI\gh.exe",
        ]
        for cand in candidates:
            if os.path.exists(cand):
                return cand
        la = os.environ.get("LOCALAPPDATA", "")
        if la:
            pat = os.path.join(
                la, "Microsoft", "WinGet", "Packages",
                "GitHub.cli_*", "gh.exe")
            hits = _glob.glob(pat)
            if hits:
                return hits[0]
        return "gh"

    def _gh_login_status(self):
        if not self._github_cli_available():
            FloatingToast(self, 'gh CLI missing, install first', '#D32F2F'); return
        gh_exe = self._find_gh_exe()
        self._log('启动 gh 设备码登录（请稍等 8 秒收集验证码）...', 'INFO')
        import subprocess as _sp
        import threading as _th
        import webbrowser as _wb
        import time as _tm
        import re as _re
        pr = _sp.Popen(
            [gh_exe, 'auth', 'login', '--web',
             '--hostname', 'github.com',
             '--git-protocol', 'https', '--skip-ssh-key'],
            stdout=_sp.PIPE, stderr=_sp.STDOUT,
            bufsize=1, text=True,
            encoding='utf-8', errors='replace',
            shell=False)
        lines = []
        _th.Thread(
            target=lambda: [lines.append(l) for l in pr.stdout],
            daemon=True).start()
        # gh needs time to talk to GitHub and print the code. Give 8 seconds.
        _tm.sleep(8)
        code = ''; url = ''
        raw = ''.join(lines)
        m_code = _re.search(
            r'(?:First copy your one-time code:|one-time code:)[\s]*([A-Z0-9-]+)',
            raw, _re.IGNORECASE)
        if m_code: code = m_code.group(1).strip()
        m_url = _re.search(
            r'(https://github\.com/login/device[\S]*)', raw)
        if m_url: url = m_url.group(1).strip()
        if not url: url = 'https://github.com/login/device'
        _wb.open(url)
        self._log('浏览器已打开: {}'.format(url), 'INFO')
        if code: self._log('请输入一次性验证码: {}'.format(code), 'SUCCESS')
        else: self._log('⚠️ 未能解析验证码，请手动访问 {} 登录'.format(url), 'WARN')
        FloatingToast(
            self,
            '打开 github.com/login/device 输入代码 {} 授权'.format(code or '...'),
            '#2E7D32')
        # Let gh keep running in background; poll auth status until success.
        def _poll_loop():
            for i in range(180):  # max ~6 minutes
                _tm.sleep(2)
                if pr.poll() is not None:
                    break
                state = self._github_auth_status()
                if state == 'logged_in':
                    self._log('✅ gh 已登录！', 'SUCCESS')
                    FloatingToast(self, 'gh 登录成功', '#2E7D32')
                    return
            _tm.sleep(3)
            state = self._github_auth_status()
            if state == 'logged_in':
                self._log('✅ gh 已登录！', 'SUCCESS')
            else:
                self._log('⚠️ gh 仍未登录，请完成浏览器授权后再点一次', 'WARN')
                try:
                    if pr.poll() is None: pr.kill()
                except Exception: pass
        self._run_async(_poll_loop)

    def _cmd_github_create(self):
        if not is_git_repo():
            FloatingToast(self, 'init repo first', '#D32F2F'); return
        gh_state = self._github_auth_status()
        has_curl = self._curl_available()
        top = tk.Toplevel(self)
        top.title('GitHub Create Repo')
        top.configure(bg='#E8E8E8')
        top.geometry('580x520')
        top.transient(self); top.grab_set()
        frm = tk.Frame(top, bg='#E8E8E8')
        frm.pack(fill='both', expand=True, padx=14, pady=10)
        tk.Label(
            frm, text='GitHub Create Repo',
            bg='#E8E8E8', fg='#F47524',
            font=('Microsoft YaHei', 12, 'bold')
        ).grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 4))
        if gh_state == 'logged_in':
            st = '[ok] gh logged'; sf = '#2E7D32'
        elif gh_state == 'not_logged_in':
            st = '[warn] gh not logged; click gh-login or paste PAT'; sf = '#D84315'
        else:
            st = '[info] gh CLI missing; paste PAT'; sf = '#555555'
        tk.Label(
            frm, text=st, bg='#E8E8E8', fg=sf,
            font=('Microsoft YaHei', 10, 'bold')
        ).grid(row=1, column=0, columnspan=2, sticky='w', pady=(0, 6))
        def _mk_label(r, t):
            tk.Label(
                frm, text=t, bg='#E8E8E8', fg='#87CEFA',
                font=('Microsoft YaHei', 10, 'bold')
            ).grid(row=r, column=0, columnspan=2, sticky='w')
        def _mk_entry(r, d=''):
            e = tk.Entry(
                frm, bg='#FFFFFF', fg='#4682B4', width=52,
                insertbackground='#4682B4')
            if d: e.insert(0, d)
            e.grid(row=r+1, column=0, columnspan=2, sticky='we', pady=(2, 6))
            return e
        _mk_label(2, 'Owner (blank=self)'); e_owner = _mk_entry(2)
        _mk_label(4, 'Repo name')
        e_repo = _mk_entry(4,
            os.path.basename(os.path.normpath(PROGRAM_DIR)))
        tk.Label(
            frm, text='Visibility', bg='#E8E8E8', fg='#87CEFA',
            font=('Microsoft YaHei', 10, 'bold')
        ).grid(row=6, column=0, columnspan=2, sticky='w')
        var_vis = tk.StringVar(value='private')
        row_v = tk.Frame(frm, bg='#E8E8E8')
        row_v.grid(row=7, column=0, columnspan=2, sticky='w', pady=2)
        for v, t in (('private', 'Private'), ('public', 'Public')):
            tk.Radiobutton(
                row_v, text=t, value=v, variable=var_vis,
                bg='#E8E8E8', fg='#333333',
                activebackground='#E8E8E8', selectcolor='#FFFFFF',
                font=('Microsoft YaHei', 10)
            ).pack(side='left', padx=(0, 18))
        desc_row = tk.Frame(frm, bg='#E8E8E8')
        desc_row.grid(row=9, column=0, columnspan=2, sticky='we', pady=(2, 6))
        e_desc = tk.Entry(
            desc_row, bg='#FFFFFF', fg='#4682B4', width=42,
            insertbackground='#4682B4')
        e_desc.pack(side='left', fill='x', expand=True)
        def _paste_to(widget):
            try:
                widget.clipboard_clear()
                widget.event_generate('<<Paste>>')
                import tkinter.messagebox as _mb
                txt = widget.clipboard_get()
                widget.delete(0, 'end'); widget.insert(0, txt)
            except Exception:
                FloatingToast(self, '剪贴板为空', '#D84315')
        tk.Button(
            desc_row, text='📋 Paste', bg=BTN_COLOR1, fg='white',
            relief='flat', activebackground=BTN_COLOR1,
            activeforeground='white',
            font=('Microsoft YaHei', 9), padx=8, pady=1,
            command=lambda: _paste_to(e_desc)
        ).pack(side='left', padx=(4, 0))
        e_token = tk.Entry(
            frm, bg='#FFFFFF', fg='#4682B4', width=42,
            insertbackground='#4682B4', show='*')
        e_token.grid(row=11, column=0, sticky='we', pady=(2, 6))
        tk.Label(
            frm, text='PAT Token (session only)',
            bg='#E8E8E8', fg='#87CEFA',
            font=('Microsoft YaHei', 10, 'bold')
        ).grid(row=10, column=0, columnspan=2, sticky='w')
        token_row = tk.Frame(frm, bg='#E8E8E8')
        token_row.grid(row=11, column=0, columnspan=2, sticky='we', pady=(2, 6))
        e_token.destroy()
        e_token = tk.Entry(
            token_row, bg='#FFFFFF', fg='#4682B4', width=42,
            insertbackground='#4682B4', show='*')
        e_token.pack(side='left', fill='x', expand=True)
        tk.Button(
            token_row, text='📋 Paste', bg=BTN_COLOR1, fg='white',
            relief='flat', activebackground=BTN_COLOR1,
            activeforeground='white',
            font=('Microsoft YaHei', 9), padx=8, pady=1,
            command=lambda: _paste_to(e_token)
        ).pack(side='left', padx=(4, 0))
        tk.Label(
            frm, text='Hint: gh easiest, else paste PAT',
            bg='#E8E8E8', fg='#888888', font=('Microsoft YaHei', 9)
        ).grid(row=12, column=0, columnspan=2, sticky='w', pady=(2, 4))

        def _do_create():
            repo_name = (e_repo.get() or '').strip()
            if not repo_name:
                FloatingToast(top, 'repo name empty', '#D32F2F'); return
            owner = (e_owner.get() or '').strip() or None
            visibility = var_vis.get()
            desc = (e_desc.get() or '').strip()
            token = (e_token.get() or '').strip()
            if gh_state == 'logged_in':
                method = 'gh'
            elif token and has_curl:
                method = 'curl'
            else:
                method = 'none'
            if method == 'gh':
                name_arg = (
                    (owner.rstrip('/') + '/' + repo_name)
                    if owner else repo_name)
                args = [
                    'gh', 'repo', 'create', name_arg,
                    '--source', PROGRAM_DIR, '--remote', 'origin']
                if visibility == 'public':
                    args.append('--public')
                else:
                    args.append('--private')
                if desc:
                    args += ['--description', desc]
                self._log('$ ' + ' '.join(args), 'INFO')
                def _run_gh():
                    try:
                        p_ = subprocess.run(
                            args, cwd=PROGRAM_DIR,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            shell=False, text=True,
                            encoding='utf-8', errors='replace')
                    except Exception as ex:
                        self._log(
                            'gh fail: {}'.format(ex), 'ERROR'); return
                    rc = p_.returncode
                    out = p_.stdout or ''; err = p_.stderr or ''
                    combined = (out + err).lower()
                    if out:
                        for line in out.splitlines()[:50]:
                            self._log(line, 'INFO')
                    if err:
                        for line in err.splitlines()[:50]:
                            self._log(line, 'WARN')
                    if rc == 0:
                        self._log(
                            'ok GitHub repo created (gh)', 'SUCCESS')
                        if not is_git_repo():
                            self._log(
                                'init repo first', 'WARN'); return
                        def _do_push():
                            rc2, _, _ = run_git(
                                ['rev-parse', '--verify', 'HEAD'])
                            if rc2 != 0:
                                files = scan_project_files(PROGRAM_DIR)
                                if files:
                                    paths = [
                                        pp.replace('\\\\', '/')
                                        for _, pp in files]
                                    self._exec_git(
                                        ['add', '--'] + paths,
                                        label='auto add')
                                    self._exec_git(
                                        ['commit', '-m',
                                         DEFAULT_COMMIT_MSG + ' (initial)'],
                                        label='auto commit')
                            self._cmd_push_async()
                        self._run_async(_do_push)
                    else:
                        self._log(
                            'gh create failed (exit={})'.format(rc), 'ERROR')
                        if ('not logged' in combined) or rc == 4:
                            self._log(
                                'gh not logged. A) click gh-login '
                                '   B) paste PAT above', 'WARN')
                            if token and has_curl:
                                self._log(
                                    'PAT given, auto switch to curl...',
                                    'INFO')
                                self._run_curl_create(
                                    repo_name, owner, visibility, desc, token)
                                return
                        if 'already exists' in combined:
                            self._log(
                                'repo exists -> set remote and push', 'INFO')
                            self.cfg['remote_url'] = (
                                'https://github.com/{}/{}.git'.format(
                                    (owner.rstrip('/') if owner else 'your-user'),
                                    repo_name))
                            self._run_async(
                                lambda: self._apply_origin_and_push(
                                    self.cfg['remote_url']))
                self._run_async(_run_gh); top.destroy(); return
            if method == 'curl':
                if not token:
                    FloatingToast(
                        top, 'PAT empty or click gh-login', '#D32F2F'); return
                if not has_curl:
                    FloatingToast(
                        top, 'curl missing', '#D32F2F'); return
                self._run_curl_create(
                    repo_name, owner, visibility, desc, token)
                top.destroy(); return
            FloatingToast(top, 'neither gh logged-in nor PAT', '#D32F2F')

        btns = tk.Frame(top, bg='#E8E8E8')
        btns.pack(fill='x', side='bottom', padx=14, pady=(6, 12))
        tk.Button(
            btns, text='gh login', bg=BTN_COLOR1, fg='white',
            relief='flat', activebackground=BTN_COLOR1,
            activeforeground='white',
            font=('Microsoft YaHei', 10, 'bold'),
            padx=12, pady=6, command=self._gh_login_status
        ).pack(side='left', padx=6)
        tk.Button(
            btns, text='Create', bg=BTN_COLOR2, fg='white',
            relief='flat', activebackground=BTN_COLOR2,
            activeforeground='white',
            font=('Microsoft YaHei', 10, 'bold'),
            padx=14, pady=6, command=_do_create
        ).pack(side='right', padx=6)
        tk.Button(
            btns, text='Cancel', bg=BTN_COLOR1, fg='white',
            relief='flat', activebackground=BTN_COLOR1,
            activeforeground='white',
            font=('Microsoft YaHei', 10, 'bold'),
            padx=14, pady=6, command=top.destroy
        ).pack(side='right', padx=6)
        tk.Label(
            top, text='PAT needs repo scope; never saved.',
            bg='#E8E8E8', fg='#888888', font=('Microsoft YaHei', 8)
        ).pack(fill='x', side='bottom', padx=14, pady=(0, 2))
    def _apply_origin_and_push(self, remote_url):
        """设置 origin 自动推送：如果本地还没有 commit 则自动 add+commit 一次。"""
        _ensure_github_identity(remote_url)
        run_git(["remote", "remove", "origin"])
        self._exec_git(["remote", "add", "origin", remote_url], label="remote add origin")
        rc, out, _ = run_git(["rev-parse", "--verify", "HEAD"])
        if rc != 0:
            self._log("ℹ️ 本地还没有 commit，自动执行 add + commit...", "INFO")
            files = scan_project_files(PROGRAM_DIR)
            if not files:
                self._log("⚠️ 目录为空或全部被过滤，无法提交。请先创建一些文件。", "WARN")
                return
            paths = [p.replace("\\", "/") for _, p in files]
            self._exec_git(["add", "--"] + paths, label="auto add")
            self._exec_git(["commit", "-m", "feat: initial commit"], label="auto commit")
        self._cmd_push_async()

    def _git_push_status(self):
        """Fetch remote, return (ahead, behind, has_rmt, ok, branch, first_push).

        first_push=True means the remote branch does not exist (empty repo).
        """
        branch = self.cfg.get('default_branch', 'main')
        rc, out, _ = run_git(['symbolic-ref', '--short', 'HEAD'])
        if rc == 0 and out.strip():
            branch = out.strip()
        rc_f, _, _ = run_git(['fetch', 'origin'])
        if rc_f != 0:
            return 0, 0, False, False, branch, False
        rc_parse, _, _ = run_git(['rev-parse', '--verify',
                                  'refs/remotes/origin/' + branch])
        if rc_parse != 0:
            # Remote branch doesn't exist -> first push to empty repo
            return 0, 0, True, True, branch, True
        rc_a, out_a, _ = run_git([
            'rev-list', '--count', 'HEAD..origin/' + branch])
        behind = int(out_a.strip() or '0') if rc_a == 0 else 0
        rc_b, out_b, _ = run_git([
            'rev-list', '--count', 'origin/' + branch + '..HEAD'])
        ahead = int(out_b.strip() or '0') if rc_b == 0 else 0
        return ahead, behind, True, True, branch, False

    def _cmd_push_async(self):
        """推送前先检测更新：behind>0 提示/pull --rebase；ahead=0 behind=0 提醒无需重复提交。"""
        branch = self.cfg.get('default_branch', 'main')
        rc, out, _ = run_git(['symbolic-ref', '--short', 'HEAD'])
        if rc == 0 and out.strip():
            branch = out.strip()
        else:
            rc2, out2, _ = run_git(['rev-parse', '--abbrev-ref', 'HEAD'])
            if rc2 == 0 and out2.strip() and out2.strip() != 'HEAD':
                branch = out2.strip()
        self.cfg['default_branch'] = branch

        def _doit():
            url = self.cfg.get('remote_url', '')
            _ensure_github_identity(url)
            # Only do update-detection if origin is configured.
            rc3, _, _ = run_git(['remote', 'get-url', 'origin'])
            if rc3 == 0:
                self._log('📡 检测远端更新状态...', 'INFO')
                ahead, behind, has_rmt, ok, br, first_push = self._git_push_status()
                if not ok:
                    self._log('⚠️ fetch origin 失败（网络或认证），继续尝试推送...', 'WARN')
                elif first_push:
                    self._log('📦 远端仓库为空（首次推送），直接 push -u {}...'.format(br), 'INFO')
                else:
                    if ahead == 0 and behind == 0:
                        self._log('📊 本地与远端一致，无需重复提交。', 'WARN')
                        FloatingToast(
                            self, '本地与远端一致，无需重复提交', '#D84315')
                        return
                    if behind > 0:
                        self._log('📥 远端领先 {} 个 commit，执行 pull --rebase...'.format(behind), 'WARN')
                        ok_pr = self._exec_git(
                            ['pull', '--rebase', 'origin', br], label='pull --rebase')
                        if not ok_pr:
                            self._log(
                                '❌ pull --rebase 失败，请手动解决冲突后再点推送。', 'ERROR')
                            return
                        ahead2, behind2, _, ok2, br2, _fp2 = self._git_push_status()
                        if ok2 and ahead2 == 0 and behind2 == 0:
                            self._log('📊 pull 后无本地变更，无需推送。', 'WARN')
                            return
            # Now push.
            rc_push, _, err = run_git(['push', '-u', 'origin', branch])
            if rc_push == 0:
                self._log('✅ push 执行成功', 'SUCCESS')
                return
            self._log('❌ push 执行失败 (exit={})'.format(rc_push), 'ERROR')
            combined = (err or '').lower()
            if ('gh007' in combined or 'publish a private email' in combined):
                self._log(
                    '检测到 GH007 邮箱保护，自动改用 noreply 邮箱并重试...', 'WARN')
                rc_e, out_e, _ = run_git(
                    ['config', '--global', '--get', 'user.email'])
                if rc_e != 0 or 'users.noreply.github.com' not in out_e.strip():
                    run_git(['config', '--global', 'user.email', _gh_compliant_email()])
                    rc_e2, out_e2, _ = run_git(
                        ['config', '--global', '--get', 'user.email'])
                    if rc_e2 == 0:
                        self._log(
                            '已设置全局 email = {}'.format(out_e2.strip()), 'INFO')
                run_git(['commit', '--amend', '--reset-author', '--no-edit'])
                self._exec_git(
                    ['push', '--force-with-lease', '-u', 'origin', branch],
                    label='push (GH007 retry)')
        self._run_async(_doit)

    def _resolve_remote_then_push(self):
        """No origin configured — prompt user how to get one."""
        top = tk.Toplevel(self)
        top.title('配置远程仓库')
        top.configure(bg='#E8E8E8'); top.geometry('520x260')
        top.transient(self); top.grab_set()
        frm = tk.Frame(top, bg='#E8E8E8')
        frm.pack(fill='both', expand=True, padx=14, pady=12)
        tk.Label(frm, text='当前仓库还没有配置 origin 远程，',
                 bg='#E8E8E8', fg='#333333',
                 font=('Microsoft YaHei', 10)).grid(
            row=0, column=0, columnspan=2, sticky='w')
        tk.Label(frm, text='请选择如何建立远程连接：',
                 bg='#E8E8E8', fg='#333333',
                 font=('Microsoft YaHei', 10)).grid(
            row=1, column=0, columnspan=2, sticky='w', pady=(2, 10))
        def _gh_auto():
            st = self._github_auth_status()
            if st != 'logged_in':
                FloatingToast(self, '请先 gh login 再自动创建', '#D32F2F'); return
            top.destroy()
            repo_name = os.path.basename(os.path.normpath(PROGRAM_DIR))
            def _run():
                args = ['gh', 'repo', 'create', repo_name,
                        '--source', PROGRAM_DIR, '--remote', 'origin', '--public']
                ok = self._exec_git(args, label='gh repo create')
                if not ok:
                    self._log('gh 创建失败，可能已存在，直接手动添加 origin 再推送', 'WARN')
                    self._cmd_remote()
                    return
                self.cfg['remote_url'] = _read_remote_origin_url()
                self._run_async(self._cmd_push_async)
            self._run_async(_run)
        def _manual():
            top.destroy(); self._cmd_remote()
        row_btns = tk.Frame(frm, bg='#E8E8E8')
        row_btns.grid(row=2, column=0, columnspan=2, sticky='we', pady=4)
        tk.Button(
            row_btns, text='🐙 用 gh 自动创建（推荐）',
            bg=BTN_COLOR2, fg='white', relief='flat',
            activebackground=BTN_COLOR2, activeforeground='white',
            font=('Microsoft YaHei', 10, 'bold'), padx=12, pady=8,
            command=_gh_auto).pack(side='left', fill='x', expand=True, padx=(0, 6))
        tk.Button(
            row_btns, text='🔗 手动输入 URL',
            bg=BTN_COLOR1, fg='white', relief='flat',
            activebackground=BTN_COLOR1, activeforeground='white',
            font=('Microsoft YaHei', 10, 'bold'), padx=12, pady=8,
            command=_manual).pack(side='left', fill='x', expand=True, padx=(6, 0))
        lbl_gh = tk.Label(frm,
            text='gh 已登录时自动建仓库并配置 origin',
            bg='#E8E8E8', fg='#555555',
            font=('Microsoft YaHei', 8))
        lbl_gh.grid(row=3, column=0, sticky='w', padx=2, pady=(0, 8))
        tk.Label(frm,
            text='直接粘贴 GitHub 仓库地址 https://github.com/owner/repo.git',
            bg='#E8E8E8', fg='#555555', font=('Microsoft YaHei', 8)).grid(
            row=3, column=1, sticky='w', padx=2, pady=(0, 8))
        tk.Button(
            top, text='❌ 取消',
            bg=BTN_COLOR1, fg='white', relief='flat',
            activebackground=BTN_COLOR1, activeforeground='white',
            font=('Microsoft YaHei', 10, 'bold'), padx=10, pady=4,
            command=top.destroy).place(relx=1.0, rely=1.0, anchor='se', x=-14, y=-10)

    def _cmd_push(self):
        if not is_git_repo():
            FloatingToast(self, '⚠️ 请先初始化仓库', '#D32F2F'); return
        # 1) No HEAD yet → full auto add/commit
        rc_head, _, _ = run_git(['rev-parse', '--verify', 'HEAD'])
        dirty = _working_tree_is_dirty()
        if rc_head != 0 or (rc_head == 0 and dirty):
            if rc_head != 0:
                if not messagebox.askyesno(
                        '本地还没有 commit',
                        '当前仓库还没有任何 commit，\n'
                        '是否自动帮你 add 过滤后的文件 + 创建 commit 并推送？'):
                    return
            else:
                if not messagebox.askyesno(
                        '检测到未提交的修改',
                        '有 {} 个文件已变更但未 commit。\n'
                        '是否自动 commit + push？'.format(
                            len(_collect_changed_files()) or '若干')):
                    return
            # Ensure .gitignore is in place (helps git cleanly ignore things too)
            _ensure_gitignore(PROGRAM_DIR)
            # Also remove any tracked files that should now be ignored
            pruned = _prune_ignored_from_index()
            if pruned:
                self._log('🗑️ 停止追踪 {} 个过滤文件（.env/chat_history/__pycache__ 等）'.format(len(pruned)),
                          'WARN')
            if rc_head != 0:
                _ensure_gitignore(PROGRAM_DIR)
                pruned0 = _prune_ignored_from_index()
                if pruned0:
                    self._log('🗑️ 停止追踪 {} 个过滤文件（chat_history/.env 等）'.format(len(pruned0)),
                              'WARN')
                def _first():
                    self._exec_git(['add', '--', '.gitignore'], label='add .gitignore')
                    self._exec_git(['add', '-A', '.'], label='auto add')
                    self._exec_git(['commit', '-m',
                                   DEFAULT_COMMIT_MSG + ' (initial)'],
                                  label='auto commit')
                    self._push_after_commit()
                self._run_async(_first); return

            def _again():
                _ensure_gitignore(PROGRAM_DIR)
                pruned2 = _prune_ignored_from_index()
                if pruned2:
                    self._log('🗑️ 停止追踪 {} 个过滤文件（chat_history/.env 等）'
                              .format(len(pruned2)), 'WARN')
                self._exec_git(['add', '--', '.gitignore'], label='add .gitignore')
                self._exec_git(['add', '-A', '.'], label='auto add')
                msg = DEFAULT_COMMIT_MSG
                if pruned2:
                    msg += ' (remove {} filtered files)'.format(len(pruned2))
                self._exec_git(['commit', '-m', msg], label='auto commit')
                self._push_after_commit()
            self._run_async(_again); return

        self._push_after_commit()

    def _push_after_commit(self):
        """Read origin, ensure remote configured, run push sync logic."""
        git_remote = _read_remote_origin_url()
        if git_remote:
            if (not self.cfg.get('remote_url')
                    or self.cfg.get('remote_url') != git_remote):
                self.cfg['remote_url'] = git_remote
                self._log(
                    '📡 从 .git 读取到远程 origin = {}'.format(git_remote),
                    'INFO')
        if not self.cfg.get('remote_url'):
            self._resolve_remote_then_push()
            return
        self._run_async(self._cmd_push_async)
    def _cmd_pull(self):
        if not is_git_repo():
            FloatingToast(self, "⚠️ 请先初始化仓库", "#D32F2F"); return

        def _do():
            rc, out, _ = run_git(["symbolic-ref", "--short", "HEAD"])
            branch = out.strip() if rc == 0 and out.strip() else self.cfg.get("default_branch", "main")
            self._exec_git(["pull", "origin", branch, "--rebase"], label="pull")

        self._run_async(_do)

    def _cmd_status(self):
        self._run_async(lambda: self._exec_git(["status"], label="status"))

    def _cmd_log(self):
        self._run_async(lambda: self._exec_git(
            ["log", "--oneline", "--decorate", "-n", "30"], label="log"))

    def _cmd_branch_list(self):
        self._run_async(lambda: self._exec_git(["branch", "-avv"], label="branch"))

    def _cmd_branch_create(self):
        dlg = DialogWithPaste(self, "创建分支", "请输入新分支名称：", initial="dev")
        self.wait_window(dlg)
        b = (dlg.result or "").strip()
        if not b:
            return
        self._run_async(lambda: self._exec_git(["checkout", "-b", b], label="checkout -b"))

    def _cmd_branch_switch(self):
        dlg = DialogWithPaste(self, "切换分支", "请输入目标分支名称：",
                              initial=self.cfg.get("default_branch", "main"))
        self.wait_window(dlg)
        b = (dlg.result or "").strip()
        if not b:
            return
        self._run_async(lambda: self._exec_git(["checkout", b], label="checkout"))

    def _cmd_branch_merge(self):
        dlg = DialogWithPaste(self, "合并分支", "请输入要合并进来的分支名：", initial="dev")
        self.wait_window(dlg)
        b = (dlg.result or "").strip()
        if not b:
            return
        self._run_async(lambda: self._exec_git(["merge", b], label="merge"))

    def _cmd_show_ignore(self):
        self._log("⚠️ 固定黑名单（代码层强制过滤，见顶部配置区）：git_manger.py / AGENTS.md / *.env / *.paml / 点文件", "WARN")
        self._log("⚠️ 临时追加过滤后缀：{}".format(self.cfg.get("extra_suffixes")), "DATA")
        self._log("⚠️ 临时追加过滤文件：{}".format(self.cfg.get("extra_names")), "DATA")
        self._log("ℹ️ 所有已过滤文件不会被 git add 添加，git commit/push 不会含隐私内容。", "INFO")
        self._refresh_filebox()

    def _cmd_open_dir(self):
        if is_windows():
            try:
                os.startfile(PROGRAM_DIR)  # type: ignore[attr-defined]
                FloatingToast(self, "✅ 已打开目录")
            except Exception:
                FloatingToast(self, "❌ 打开失败", "#D32F2F")
        else:
            try:
                subprocess.Popen(["xdg-open", PROGRAM_DIR])
                FloatingToast(self, "✅ 已打开目录")
            except Exception:
                FloatingToast(self, "❌ 打开失败", "#D32F2F")

    def _cmd_settings(self):
        """设置界面：自定义过滤后缀/文件名 + Git 用户信息 + 外观。

        注意：这里的设置仅在本次运行有效。
        永久修改请直接编辑文件顶部【用户可修改的配置区】。
        """
        top = tk.Toplevel(self)
        top.title("🔑 设置")
        top.configure(bg="#E8E8E8")
        top.geometry("560x480")
        top.transient(self)
        top.grab_set()

        pad = 10
        frm = tk.Frame(top, bg="#E8E8E8")
        frm.pack(fill="both", expand=True, padx=pad, pady=pad)

        tk.Label(frm, text="📌 永久规则请直接编辑文件顶部【用户可修改的配置区】",
                 bg="#E8E8E8", fg="#F47524",
                 font=("Microsoft YaHei", 10, "bold")).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))

        tk.Label(frm, text="🔒 临时追加忽略后缀（本次运行有效，如 .bak,.tmp）",
                 bg="#E8E8E8", fg="#87CEFA",
                 font=("Microsoft YaHei", 10, "bold")).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 2))
        e_suf = tk.Entry(frm, bg="#FFFFFF", fg="#4682B4", insertbackground="#4682B4", width=60)
        e_suf.insert(0, ", ".join(self.cfg.get("extra_suffixes") or []))
        e_suf.grid(row=2, column=0, columnspan=3, sticky="we", pady=(0, 8))

        tk.Label(frm, text="🔒 临时追加忽略文件名（本次运行有效，如 credentials.json）",
                 bg="#E8E8E8", fg="#87CEFA",
                 font=("Microsoft YaHei", 10, "bold")).grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 2))
        e_nm = tk.Entry(frm, bg="#FFFFFF", fg="#4682B4", insertbackground="#4682B4", width=60)
        e_nm.insert(0, ", ".join(self.cfg.get("extra_names") or []))
        e_nm.grid(row=4, column=0, columnspan=3, sticky="we", pady=(0, 8))

        tk.Label(frm, text="👤 Git user.name",
                 bg="#E8E8E8", fg="#87CEFA",
                 font=("Microsoft YaHei", 10, "bold")).grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 2))
        e_gn = tk.Entry(frm, bg="#FFFFFF", fg="#4682B4", insertbackground="#4682B4", width=60)
        e_gn.insert(0, self.cfg.get("git_user_name", ""))
        e_gn.grid(row=6, column=0, columnspan=3, sticky="we", pady=(0, 8))

        tk.Label(frm, text="📧 Git user.email",
                 bg="#E8E8E8", fg="#87CEFA",
                 font=("Microsoft YaHei", 10, "bold")).grid(row=7, column=0, columnspan=3, sticky="w", pady=(6, 2))
        e_ge = tk.Entry(frm, bg="#FFFFFF", fg="#4682B4", insertbackground="#4682B4", width=60)
        e_ge.insert(0, self.cfg.get("git_user_email", ""))
        e_ge.grid(row=8, column=0, columnspan=3, sticky="we", pady=(0, 8))

        # 颜色设置（临时预览）
        tk.Label(frm, text="🎨 界面颜色（临时预览，不保存）",
                 bg="#E8E8E8", fg="#87CEFA",
                 font=("Microsoft YaHei", 10, "bold")).grid(row=9, column=0, columnspan=3, sticky="w", pady=(8, 2))
        frm_color = tk.Frame(frm, bg="#E8E8E8")
        frm_color.grid(row=10, column=0, columnspan=3, sticky="we")

        preview_vars = {}

        def _color_row(row_label, key, default):
            r = tk.Frame(frm_color, bg="#E8E8E8")
            r.pack(fill="x", pady=2)
            tk.Label(r, text=row_label, bg="#E8E8E8",
                     font=("Microsoft YaHei", 9)).pack(side="left")
            pv = tk.StringVar(value=default)
            preview_vars[key] = pv
            disp = tk.Entry(r, textvariable=pv, width=14, bg="#FFFFFF", fg="#4169E1")
            disp.pack(side="left", padx=(6, 4))
            color_show = tk.Label(r, bg=pv.get(), width=4, relief="solid", bd=1)
            color_show.pack(side="left", padx=(0, 6))

            def _choose():
                c = colorchooser.askcolor(color=pv.get(), parent=top)
                if c and c[1]:
                    pv.set(c[1])
                    color_show.configure(bg=c[1])
                    self._log("🎨 {} = {}".format(key, c[1]), "INFO")

            tk.Button(r, text="🎨 选择", bg=BTN_COLOR2, fg="white", relief="flat",
                      font=("Microsoft YaHei", 9, "bold"), padx=8, pady=2,
                      command=_choose).pack(side="left")
            return pv

        _color_row("按钮颜色1", "btn_color1", BTN_COLOR1)
        _color_row("按钮颜色2", "btn_color2", BTN_COLOR2)
        _color_row("进度条颜色", "progress_color", "#87CEFA")

        btns = tk.Frame(top, bg="#E8E8E8")
        btns.pack(fill="x", side="bottom", padx=pad, pady=(0, pad))

        def _save():
            def _split(text):
                return [x.strip() for x in text.replace(";", ",").split(",") if x.strip()]

            self.cfg["extra_suffixes"] = _split(e_suf.get())
            self.cfg["extra_names"] = _split(e_nm.get())
            self.cfg["git_user_name"] = e_gn.get().strip()
            self.cfg["git_user_email"] = e_ge.get().strip()
            for k, v in preview_vars.items():
                if v.get().startswith("#"):
                    self.cfg[k] = v.get()
            if self.cfg["git_user_name"]:
                run_git(["config", "user.name", self.cfg["git_user_name"]])
            if self.cfg["git_user_email"]:
                run_git(["config", "user.email", self.cfg["git_user_email"]])
            self._log("✅ 设置已应用（永久规则请编辑代码顶部配置区）", "SUCCESS")
            FloatingToast(self, "✅ 设置已应用")
            self._refresh_filebox()
            top.destroy()

        tk.Button(btns, text="💾 应用（本次运行）", bg=BTN_COLOR2, fg="white",
                  activebackground=BTN_COLOR2, activeforeground="white",
                  relief="flat", font=("Microsoft YaHei", 10, "bold"),
                  padx=16, pady=6, command=_save).pack(side="right", padx=4)
        tk.Button(btns, text="❌ 取消", bg=BTN_COLOR1, fg="white",
                  activebackground=BTN_COLOR1, activeforeground="white",
                  relief="flat", font=("Microsoft YaHei", 10, "bold"),
                  padx=16, pady=6, command=top.destroy).pack(side="right", padx=4)

    # -------- 主题/语言 --------
    def _cycle_theme(self):
        nxt = (self.theme_index + 1) % len(THEMES)
        self._set_theme(idx=nxt, dark=False)

    def _toggle_dark(self):
        self._set_theme(dark=not self.is_dark)

    def _toggle_lang(self):
        if self.cfg.get("language", DEFAULT_LANGUAGE) == "zh":
            self.cfg["language"] = "en"
            self._log("Language switched to English", "INFO")
        else:
            self.cfg["language"] = "zh"
            self._log("语言已切换为中文", "INFO")

    def _help_me(self):
        text = (
            "📖 使用说明\n"
            "──────────────────────────\n"
            "① 把本工具放在项目根目录（与 .git 同级）运行。\n"
            "② 隐私文件会被代码层强制过滤（顶部配置区可改）：\n"
            "    - git_manger.py（自身）\n"
            "    - AGENTS.md（规则说明）\n"
            "    - *.env / *.paml（隐私后缀）\n"
            "    - 隐藏文件（点文件）、构建产物\n"
            "③ 常用流程：\n"
            "   初始化仓库 → 扫描并添加 → 代码提交\n"
            "   → 远程仓库（绑定地址）→ 推送到远程\n"
            "④ 远程仓库 URL 粘贴即可，支持 HTTPS/SSH。\n"
            "⑤ 认证请使用 Personal Access Token 或 SSH Key，\n"
            "   不要在本工具中保存明文密码。\n"
            "⑥ 永久修改过滤规则：编辑文件顶部【用户可修改配置区】。\n"
            "──────────────────────────\n"
            "⚠️ 本工具调用系统原生 Git，请先安装 Git 并配置 PATH。"
        )
        messagebox.showinfo("帮助", text)

    def _about_me(self):
        messagebox.showinfo(
            "关于",
            "🔧 Git 上传辅助工具  v1.0（单文件版）\n\n"
            "作者：GitManager 团队\n"
            "语言：Python 3.10+ / Tkinter\n"
            "特性：\n"
            "  ✅ 强制隐私文件过滤\n"
            "  ✅ 可视化 Git 菜单\n"
            "  ✅ 单文件、零外部依赖、零配置文件\n"
            "  ✅ 跨平台（Windows / Linux / macOS）\n"
            "  ✅ 分支管理 / 日志 / 皮肤\n\n"
            "⚠️ 请遵守 GitHub 用户协议与开源许可。"
        )

    def _on_close(self):
        self.destroy()


def main():
    # 兼容 Windows 控制台编码
    if is_windows():
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            try:
                sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
                sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
            except Exception:
                pass
    app = GitManagerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
