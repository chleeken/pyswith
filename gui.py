"""
ccswith.gui - CC-Switch Python 图形界面 (CustomTkinter)
支持：服务商列表、增删改查、一键切换、备份/回滚、导入/导出、皮肤、日志、进度、设置、帮助/关于、右键菜单。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import os
import secrets
import socket
import string
import threading
import tkinter as tk
import tkinter.colorchooser
import tkinter.messagebox
import tkinter.simpledialog
import tkinter.ttk as ttk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from core import (
    CLI_TEMPLATES,
    HERMES_VIRTUAL_MODEL,
    ProxyState,
    Provider,
    ProviderManager,
    SwitchEngine,
    backup_cli_configs,
    create_proxy_http_server,
    get_backup_dir,
    get_config_dir,
    get_program_dir,
    list_backups,
    pick_free_port,
    rollback_from_backup,
)

logger = logging.getLogger("ccswith")


APP_TITLE = "CC-Switch Python 版 - 多 AI CLI 配置切换工具"
# 统一配置文件由 ProviderManager 管理（config/config.yaml），不再独立 ui_settings.json


SKINS = [
    ("皮肤1 粉灰/白灰", "#BDC3DB", "#F9F9FB"),
    ("皮肤2 蓝白/乳绿", "#E6E6E6", "#E0FACF"),
    ("皮肤3 粉白/白粉", "#E4E4E4", "#EEDEE2"),
    ("皮肤4 白灰/白绿", "#E4E4E4", "#DEEFE5"),
    ("皮肤5 淡白/蓝灰白", "#EBEBEB", "#E3EFFF"),
    ("皮肤6 粉/乳粉", "#ECDCE5", "#FFEAEA"),
    ("皮肤7 乳白/乳绿", "#E0E0E0", "#E0FACF"),
    ("暗黑", "#2b2b2b", "#1a1a1a"),
]
DEFAULT_SKIN = 0

BUTTON_PRIMARY = "#4B9956"
BUTTON_SECONDARY = "#2E7D32"
TITLE_BG = "#F47524"
TITLE_FG = "#FFFAF0"
TAG_COLOR = "#87CEFA"
TAG_TEXT = "#9ACD32"
LOG_BG = "#FFFFFF"
INPUT_FG = "#4682B4"
OUTPUT_FG = "#4169E1"
LOG_TEXT_COLOR = "#008080"
LOG_TIME_COLOR = "#D8BFD8"
APP_BG = "#E8E8E8"
FRAME_BG = "#E8E8E8"
TEXT_BG = "#FFFFFF"
CHECK_BG = "#ADD8E6"
CHECK_FG = "#FFFAF0"
CHECK_BORDER = "#9370D8"
SCROLL_BG = "#1a1a1a"
SCROLL_FG = "#87CEFA"
PROGRESS_BG = "#1a1a1a"
PROGRESS_FG = "#87CEFA"


# ---------------------------- 悬浮消息窗 ----------------------------

class FloatingToast(tk.Toplevel):
    """简单悬浮提示窗，1 秒后自动关闭。"""

    def __init__(self, master: tk.Misc, text: str, ok: bool = True, duration_ms: int = 1000):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        fg = "#FFFFFF"
        bg = "#2E7D32" if ok else "#C62828"
        label = tk.Label(self, text=text, fg=fg, bg=bg, font=("Microsoft YaHei", 12, "bold"), padx=20, pady=10)
        label.pack()
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        try:
            self.update()
        except Exception:
            pass
        mx = master.winfo_rootx() + master.winfo_width() // 2 - w // 2
        my = master.winfo_rooty() + master.winfo_height() // 2 - h // 2
        self.geometry(f"{w}x{h}+{max(mx,0)}+{max(my,0)}")
        self.after(duration_ms, self.destroy)


# ---------------------------- 带“粘贴”按钮的对话框 ----------------------------

class DialogWithPaste(ctk.CTkToplevel):
    """对话框基类：包含“粘贴”按钮与 esc 关闭绑定。

    子类可在构造时传入 initial_geometry 和 minsize 以自定义窗口大小，
    布局完成后调用 _auto_resize_and_center() 让窗口按内容自适应并居中。
    支持滚动内容区，按钮行固定在底部始终可见。
    """

    def __init__(
        self,
        master: tk.Misc,
        title: str,
        initial_geometry: str = "560x400",
        min_size: Tuple[int, int] = (520, 360),
        scrollable: bool = False,
    ):
        super().__init__(master)
        self.title(title)
        self.configure(fg_color=APP_BG)
        self.grab_set()
        self.geometry(initial_geometry)
        try:
            self.minsize(min_size[0], min_size[1])
        except Exception:
            pass
        self.result = None
        self.clipboard_failed = False
        self.bind("<Escape>", lambda e: self.destroy())
        self._master_ref = master
        self._scrollable = scrollable

        # 统一的布局骨架：
        #   底部：按钮行（子类自己 pack）
        #   顶部：内容容器（如果 scrollable 就用 CTkScrollableFrame）
        self._content_area = None
        self._button_area = None

        if scrollable:
            # 让按钮行最后 pack（side=bottom），内容区先 pack（side=top, expand）
            # 所以按钮永远固定在窗口底部可见
            self._button_area = ctk.CTkFrame(self, fg_color=FRAME_BG)
            self._button_area.pack(side="bottom", fill="x")
            self._content_area = ctk.CTkScrollableFrame(self, fg_color=FRAME_BG, label_text="")
            self._content_area.pack(side="top", fill="both", expand=True, padx=8, pady=(8, 4))
        else:
            # 非滚动模式：不建容器，子类直接 pack 自己的控件
            # 子类需要保证按钮行用 pack(side="bottom", fill="x") 放在最外面
            pass

    def get_content_frame(self) -> Optional[tk.Misc]:
        """子类取到内容区容器来放置表单控件。

        如果 scrollable=False，返回 None，子类自己直接往 self 上 pack。
        """
        return self._content_area

    def get_button_frame(self) -> Optional[ctk.CTkFrame]:
        """子类取到按钮行容器来放置操作按钮。"""
        return self._button_area

    def _auto_resize_and_center(self, prefer_size: Optional[Tuple[int, int]] = None) -> None:
        """布局完成后：让窗口按内容自适应（但不超过屏幕 90%），再居中显示。"""
        try:
            self.update_idletasks()
            self.update()
        except Exception:
            pass

        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        max_w = int(sw * 0.92)
        max_h = int(sh * 0.88)

        if prefer_size:
            w, h = prefer_size
        else:
            try:
                w = max(self.winfo_reqwidth(), self.winfo_width())
                h = max(self.winfo_reqheight(), self.winfo_height())
            except Exception:
                w, h = 640, 500

        w = min(max(320, w), max_w)
        h = min(max(240, h), max_h)

        try:
            self.geometry(f"{w}x{h}")
        except Exception:
            pass

        try:
            master = self._master_ref
            mx = master.winfo_rootx() + master.winfo_width() // 2 - w // 2
            my = master.winfo_rooty() + master.winfo_height() // 2 - h // 2
            # 防止贴出屏幕顶部/左侧
            mx = max(0, min(mx, sw - w - 20))
            my = max(0, min(my, sh - h - 80))
            self.geometry(f"+{mx}+{my}")
        except Exception:
            # 兜底：屏幕中央
            mx = (sw - w) // 2
            my = (sh - h) // 2
            try:
                self.geometry(f"+{max(mx, 0)}+{max(my, 0)}")
            except Exception:
                pass

    def paste_to(self, widget) -> None:
        """把剪贴板文本粘贴到 widget（Text 或 Entry 均可）。"""
        try:
            cb = self.clipboard_get()
        except Exception:
            tk.messagebox.showwarning("提示", "剪贴板为空或无法访问。", parent=self)
            return
        if not cb:
            return
        try:
            if isinstance(widget, (tk.Text, )) or hasattr(widget, "insert") and not isinstance(widget, (tk.Entry, ctk.CTkEntry)):
                try:
                    widget.delete("1.0", tk.END)
                    widget.insert("1.0", cb)
                except Exception:
                    widget.delete(0, tk.END)
                    widget.insert(0, cb)
            else:
                try:
                    widget.delete(0, tk.END)
                    widget.insert(0, cb)
                except Exception:
                    pass
        except Exception as e:
            tk.messagebox.showwarning("粘贴失败", str(e), parent=self)


# ---------------------------- 服务商编辑对话框 ----------------------------

class ProviderDialog(DialogWithPaste):
    """新增/编辑服务商配置对话框。

    支持：💾保存（保存后不关闭）、✅保存并关闭、❌取消。
    保存会直接写入 ProviderManager 并持久化到 providers.json。
    """

    def __init__(
        self,
        master: tk.Misc,
        provider: "Provider | None" = None,
        manager: "ProviderManager | None" = None,
        on_saved: callable | None = None,
    ):
        super().__init__(
            master,
            "编辑服务商" if provider else "新增服务商",
            initial_geometry="720x560",
            min_size=(680, 480),
            scrollable=True,
        )
        self.provider = provider
        self.manager = manager
        self.on_saved = on_saved
        self.entries: dict[str, ctk.CTkEntry] = {}

        # 取基类预建好的两个容器
        content_area = self.get_content_frame()  # CTkScrollableFrame
        button_area = self.get_button_frame()      # CTkFrame (始终贴底可见)
        assert content_area is not None and button_area is not None

        # ===== 表单区（可滚动） =====
        fields = [
            ("别名 (唯一标识)", "alias"),
            ("显示名称", "display_name"),
            ("API Key", "api_key"),
            ("Base URL", "base_url"),
            ("默认模型", "model"),
            ("API 格式 (openai/anthropic/deepseek/custom)", "api_format"),
        ]
        # 用一个普通 Frame 放 grid 布局
        inner = ctk.CTkFrame(content_area, fg_color=FRAME_BG)
        inner.pack(fill="both", expand=True, padx=4, pady=4)

        values = provider.to_dict() if provider else {}
        for i, (label, key) in enumerate(fields):
            lbl = ctk.CTkLabel(inner, text=f"📋 {label}", text_color=TAG_TEXT, anchor="w")
            lbl.grid(row=i, column=0, sticky="w", padx=6, pady=4)
            ent = ctk.CTkEntry(inner, height=30, fg_color=TEXT_BG, text_color=INPUT_FG)
            ent.grid(row=i, column=1, sticky="we", padx=4, pady=4)
            paste_btn = ctk.CTkButton(
                inner,
                text="📋 粘贴",
                width=60,
                height=30,
                fg_color=BUTTON_SECONDARY,
                hover_color=BUTTON_PRIMARY,
                text_color="#FFFFFF",
                command=lambda e=ent: self.paste_to(e),
            )
            paste_btn.grid(row=i, column=2, padx=4, pady=4)
            self.entries[key] = ent
            if values.get(key):
                ent.insert(0, str(values[key]))

        note_lbl = ctk.CTkLabel(inner, text="📋 备注", text_color=TAG_TEXT, anchor="w")
        note_lbl.grid(row=len(fields), column=0, sticky="w", padx=6, pady=4)
        self.note_box = ctk.CTkTextbox(inner, height=70, fg_color=TEXT_BG, text_color=INPUT_FG)
        self.note_box.grid(row=len(fields), column=1, columnspan=2, sticky="we", padx=4, pady=4)
        if values.get("note"):
            self.note_box.insert("1.0", str(values["note"]))

        inner.grid_columnconfigure(1, weight=1)

        # ===== 按钮区（永远贴底可见） =====
        ctk.CTkButton(
            button_area,
            text="❌ 取消",
            fg_color=BUTTON_SECONDARY,
            hover_color=BUTTON_PRIMARY,
            text_color="#FFFFFF",
            command=self.destroy,
        ).pack(side="right", padx=6, pady=8)

        ctk.CTkButton(
            button_area,
            text="✅ 保存并关闭",
            fg_color=BUTTON_PRIMARY,
            hover_color=BUTTON_SECONDARY,
            text_color="#FFFFFF",
            command=self._on_save_and_close,
        ).pack(side="right", padx=6, pady=8)

        ctk.CTkButton(
            button_area,
            text="💾 保存",
            fg_color=BUTTON_PRIMARY,
            hover_color=BUTTON_SECONDARY,
            text_color="#FFFFFF",
            command=self._on_save_keep_open,
        ).pack(side="right", padx=6, pady=8)

        # 布局完成：固定 720x560 并居中（不再按内容自适应高度，因为内容在滚动区里）
        self._auto_resize_and_center(prefer_size=(720, 560))

    def _collect_form_provider(self) -> "Provider | None":
        """从表单里取字段，校验并构造 Provider。失败返回 None。"""
        data = {k: e.get().strip() for k, e in self.entries.items()}
        data["note"] = self.note_box.get("1.0", tk.END).strip()
        if not data.get("alias"):
            tk.messagebox.showwarning("提示", "别名不能为空。", parent=self)
            return None
        if data["api_format"] not in {"openai", "anthropic", "deepseek", "custom"}:
            data["api_format"] = "openai"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        created_at = self.provider.created_at if self.provider else now
        return Provider(
            alias=data["alias"],
            display_name=data.get("display_name") or data["alias"],
            base_url=data.get("base_url", ""),
            api_key=data.get("api_key", ""),
            model=data.get("model", ""),
            api_format=data.get("api_format", "openai"),
            enabled=True,
            note=data.get("note", ""),
            created_at=created_at,
            updated_at=now,
        )

    def _persist_to_manager(self, p: "Provider") -> bool:
        """把 Provider 写入 ProviderManager（如果有）。返回是否成功。"""
        if not self.manager:
            return False
        try:
            self.manager.add_or_update(p)
            return True
        except Exception as e:
            logger.error("ProviderDialog 保存失败: %s", e)
            tk.messagebox.showerror("保存失败", f"写入 providers.json 出错：\n{e}", parent=self)
            return False

    def _on_save_keep_open(self) -> None:
        """💾 保存：写入 manager 并持久化，但不关闭对话框。"""
        p = self._collect_form_provider()
        if not p:
            return
        ok = self._persist_to_manager(p)
        if ok:
            if self.on_saved:
                try:
                    self.on_saved(p, closed=False)
                except Exception:
                    pass
            FloatingToast(self.master, f"✅ 已保存：{p.alias}", ok=True)
            self.provider = self.manager.get(p.alias) if self.manager else p

    def _on_save_and_close(self) -> None:
        """✅ 保存并关闭：先持久化，再把 Provider 暴露给调用方，最后关闭。"""
        p = self._collect_form_provider()
        if not p:
            return
        self._persist_to_manager(p)
        if self.on_saved:
            try:
                self.on_saved(p, closed=True)
            except Exception:
                pass
        self.result = p
        self.destroy()


# ---------------------------- 设置对话框 ----------------------------

class SettingsDialog(DialogWithPaste):
    """右键菜单/设置界面：字体、字号、颜色、皮肤等。"""

    FONT_PRESETS = ["宋体", "微软雅黑", "楷体"]
    FONT_STYLES = [("常规", "normal"), ("倾斜", "italic"), ("粗体", "bold"), ("粗偏斜体", "bold italic")]

    def __init__(self, master: tk.Misc, current: dict):
        super().__init__(
            master,
            "⚙️ 外观设置",
            initial_geometry="640x520",
            min_size=(600, 460),
        )
        self.cfg = dict(current)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=6, pady=6)
        self.nb = nb

        self._build_font_tab()
        self._build_color_tab()

        btns = ctk.CTkFrame(self, fg_color=FRAME_BG)
        btns.pack(fill="x", padx=6, pady=6)
        ctk.CTkButton(
            btns,
            text="💾 保存设置",
            fg_color=BUTTON_PRIMARY,
            hover_color=BUTTON_SECONDARY,
            text_color="#FFFFFF",
            command=self.on_save,
        ).pack(side="right", padx=6, pady=6)
        ctk.CTkButton(
            btns,
            text="❌ 取消",
            fg_color=BUTTON_SECONDARY,
            hover_color=BUTTON_PRIMARY,
            text_color="#FFFFFF",
            command=self.destroy,
        ).pack(side="right", padx=6, pady=6)

    def _page(self, name: str) -> ctk.CTkFrame:
        f = ctk.CTkFrame(self, fg_color=FRAME_BG)
        self.nb.add(f, text=name)
        return f

    def _build_font_tab(self) -> None:
        f = self._page("字体/字形/字号")
        r = 0
        ctk.CTkLabel(f, text="📋 字体", text_color=TAG_TEXT).grid(row=r, column=0, sticky="w", padx=6, pady=6)
        self.font_var = tk.StringVar(value=self.cfg.get("font_family", "微软雅黑"))
        cb = ctk.CTkComboBox(f, values=self.FONT_PRESETS, variable=self.font_var, width=180)
        cb.grid(row=r, column=1, sticky="w", padx=6, pady=6)

        r += 1
        ctk.CTkLabel(f, text="📋 字形", text_color=TAG_TEXT).grid(row=r, column=0, sticky="w", padx=6, pady=6)
        self.style_var = tk.StringVar(value=self.cfg.get("font_style", "normal"))
        cb2 = ctk.CTkComboBox(f, values=[s[1] for s in self.FONT_STYLES], variable=self.style_var, width=180)
        cb2.grid(row=r, column=1, sticky="w", padx=6, pady=6)

        r += 1
        ctk.CTkLabel(f, text="📋 字号", text_color=TAG_TEXT).grid(row=r, column=0, sticky="w", padx=6, pady=6)
        self.size_var = tk.StringVar(value=str(self.cfg.get("font_size", 11)))
        values = [str(i) for i in range(9, 25)]
        cb3 = ctk.CTkComboBox(f, values=values, variable=self.size_var, width=180)
        cb3.grid(row=r, column=1, sticky="w", padx=6, pady=6)

        ctk.CTkButton(
            f,
            text="💾 保存字体设置",
            fg_color=BUTTON_PRIMARY,
            hover_color=BUTTON_SECONDARY,
            text_color="#FFFFFF",
            command=self._save_font_quick,
        ).grid(row=r + 1, column=0, columnspan=2, sticky="w", padx=6, pady=10)

    def _build_color_tab(self) -> None:
        f = self._page("颜色与皮肤")
        r = 0
        self.color_entries: dict[str, tuple[ctk.CTkEntry, ctk.CTkLabel]] = {}
        items = [
            ("文字颜色", "text_fg", "#222222"),
            ("背景颜色", "bg", APP_BG),
            ("工具栏背景", "toolbar_bg", "#F0F0F0"),
            ("按钮背景色A", "btn_bg_a", BUTTON_PRIMARY),
            ("按钮背景色B", "btn_bg_b", BUTTON_SECONDARY),
        ]
        for label, key, default in items:
            ctk.CTkLabel(f, text=f"🎨 {label}", text_color=TAG_TEXT).grid(row=r, column=0, sticky="w", padx=6, pady=4)
            val = self.cfg.get(key, default)
            ent = ctk.CTkEntry(f, width=110, fg_color=TEXT_BG, text_color=INPUT_FG)
            ent.insert(0, val)
            ent.grid(row=r, column=1, sticky="w", padx=6, pady=4)
            preview = ctk.CTkLabel(f, text="        ", fg_color=val, width=60, height=28)
            preview.grid(row=r, column=2, padx=4, pady=4)

            def picker(e=ent, p=preview):
                c = tk.colorchooser.askcolor(color=e.get())
                if c and c[1]:
                    e.delete(0, tk.END)
                    e.insert(0, c[1])
                    p.configure(fg_color=c[1])

            ctk.CTkButton(
                f,
                text="🎨 选择",
                width=80,
                fg_color=BUTTON_SECONDARY,
                hover_color=BUTTON_PRIMARY,
                text_color="#FFFFFF",
                command=picker,
            ).grid(row=r, column=3, padx=4, pady=4)
            self.color_entries[key] = (ent, preview)
            r += 1

        # 皮肤
        ctk.CTkLabel(f, text="🎨 皮肤", text_color=TAG_TEXT).grid(row=r, column=0, sticky="w", padx=6, pady=8)
        skin_names = [s[0] for s in SKINS]
        self.skin_var = tk.StringVar(value=skin_names[self.cfg.get("skin_index", DEFAULT_SKIN)])
        ctk.CTkComboBox(f, values=skin_names, variable=self.skin_var, width=220).grid(row=r, column=1, sticky="w", padx=6, pady=8)
        r += 1

        ctk.CTkButton(
            f,
            text="💾 保存颜色设置",
            fg_color=BUTTON_PRIMARY,
            hover_color=BUTTON_SECONDARY,
            text_color="#FFFFFF",
            command=self._save_colors_quick,
        ).grid(row=r, column=0, columnspan=4, sticky="w", padx=6, pady=10)

    def _save_font_quick(self) -> None:
        self.cfg["font_family"] = self.font_var.get()
        self.cfg["font_style"] = self.style_var.get()
        try:
            self.cfg["font_size"] = int(self.size_var.get())
        except ValueError:
            self.cfg["font_size"] = 11
        FloatingToast(self.master, "✅ 字体设置已保存", ok=True)
        self.result = self.cfg
        # 不关闭，继续操作

    def _save_colors_quick(self) -> None:
        for key, (ent, _) in self.color_entries.items():
            self.cfg[key] = ent.get().strip() or self.cfg.get(key, "#FFFFFF")
        skin_idx = [s[0] for s in SKINS].index(self.skin_var.get()) if self.skin_var.get() in [s[0] for s in SKINS] else DEFAULT_SKIN
        self.cfg["skin_index"] = skin_idx
        FloatingToast(self.master, "✅ 颜色设置已保存", ok=True)
        self.result = self.cfg

    def on_save(self) -> None:
        # 汇总保存（含字体）
        self.cfg["font_family"] = self.font_var.get()
        self.cfg["font_style"] = self.style_var.get()
        try:
            self.cfg["font_size"] = int(self.size_var.get())
        except ValueError:
            self.cfg["font_size"] = 11
        for key, (ent, _) in self.color_entries.items():
            self.cfg[key] = ent.get().strip() or self.cfg.get(key, "#FFFFFF")
        skin_idx = [s[0] for s in SKINS].index(self.skin_var.get()) if self.skin_var.get() in [s[0] for s in SKINS] else DEFAULT_SKIN
        self.cfg["skin_index"] = skin_idx
        self.result = self.cfg
        self.destroy()


# ---------------------------- 主程序窗口 ----------------------------

class App(ctk.CTk):
    """CC-Switch Python 主窗口。"""

    def __init__(self):
        super().__init__(fg_color=APP_BG)
        self.title(APP_TITLE)
        self.geometry("1200x760")
        self.minsize(960, 600)
        # 延迟最大化，确保窗口完全初始化后再执行
        self.after(50, self._maximize_window)

        # 图标（Windows）
        try:
            ico = get_program_dir() / "icon.ico"
            if ico.exists():
                self.iconbitmap(str(ico))
        except Exception:
            pass

        self.engine = SwitchEngine()
        self.settings = self._load_settings()
        self.lang = tk.StringVar(value="中文")
        self.progress_text = tk.StringVar(value="程序就绪")
        self._apply_skin_from_index(self.settings.get("skin_index", DEFAULT_SKIN))
        self._build_ui()
        # 首次刷新
        self.refresh_provider_list()
        self._log("ℹ️", "程序启动完成。当前服务商：" + (self.engine.manager.current_alias or "(未设置)"))

        # 关窗口时必存（最关键的兜底：用户关窗后下次再开，设置还在）
        try:
            self.protocol("WM_DELETE_WINDOW", self._on_close_and_save)
        except Exception:
            pass

        # 自动启动 API 网关代理（使用 config.yaml 里 proxy 分区的 host/port/virtual_model）
        self._auto_start_proxy()

    def _maximize_window(self) -> None:
        """窗口最大化，延迟调用确保在窗口完全初始化后生效。"""
        try:
            self.state("zoomed")
        except Exception:
            try:
                self.attributes("-fullscreen", True)
            except Exception:
                pass

    # ----------------------- 设置读写（统一由 ProviderManager 写 config.yaml） -----------------------
    def _load_settings(self) -> dict:
        mgr = self.engine.manager
        settings = mgr.get_ui_settings()
        defaults = {
            "skin_index": DEFAULT_SKIN,
            "font_family": "微软雅黑",
            "font_style": "normal",
            "font_size": 11,
            "text_fg": "#222222",
            "bg": APP_BG,
            "toolbar_bg": "#F0F0F0",
            "btn_bg_a": BUTTON_PRIMARY,
            "btn_bg_b": BUTTON_SECONDARY,
            "proxy_host": "127.0.0.1",
            "proxy_port": "8787",
            "virtual_model": HERMES_VIRTUAL_MODEL,
        }
        if not settings:
            # 兼容旧的 ui_settings.json
            old_path = get_config_dir() / "ui_settings.json"
            if old_path.exists():
                try:
                    with open(old_path, "r", encoding="utf-8") as f:
                        old = json.load(f)
                    if isinstance(old, dict):
                        settings = old
                        mgr.set_ui_settings(settings)
                        try:
                            old_bak = old_path.with_suffix(old_path.suffix + ".bak")
                            old_path.rename(old_bak)
                        except Exception:
                            pass
                except Exception:
                    pass
        merged = dict(defaults)
        if isinstance(settings, dict):
            merged.update(settings)
        return merged

    def _save_settings(self) -> None:
        try:
            self.engine.manager.set_ui_settings(self.settings)
        except Exception as e:
            self._log("❌", f"保存设置失败: {e}")

    # ----------------------- 皮肤 -----------------------
    def _apply_skin_from_index(self, idx: int) -> None:
        try:
            name, top, body = SKINS[int(idx)]
        except Exception:
            name, top, body = SKINS[DEFAULT_SKIN]
        self.skin_name = name
        self.skin_top = top
        self.skin_body = body

    def _apply_skin_to_widgets(self) -> None:
        if hasattr(self, "_title_frame"):
            self._title_frame.configure(fg_color=self.skin_top)
            for w in self._title_frame.winfo_children():
                try:
                    w.configure(fg_color=self.skin_top)
                except Exception:
                    pass
        self.configure(fg_color=self.skin_body)
        for attr in ("_sidebar", "_center", "_right", "_bottom"):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.configure(fg_color=self.skin_body)
                    for child in w.winfo_children():
                        try:
                            child.configure(fg_color=self.skin_body)
                        except Exception:
                            pass
                except Exception:
                    pass

    # ----------------------- UI -----------------------
    def _build_ui(self) -> None:
        # 标题栏（使用固定橙色标题条，文字白）
        self._title_frame = ctk.CTkFrame(self, fg_color=TITLE_BG, height=44)
        self._title_frame.pack(fill="x", side="top")
        self._title_frame.pack_propagate(False)
        title_lbl = ctk.CTkLabel(
            self._title_frame,
            text="🚀 " + APP_TITLE,
            text_color=TITLE_FG,
            font=(self.settings.get("font_family", "微软雅黑"), 14, "bold"),
            anchor="w",
        )
        title_lbl.pack(side="left", padx=12, fill="x", expand=True)

        # 主体：左（服务商列表）+ 中（操作区）+ 右（日志）
        body = ctk.CTkFrame(self, fg_color=self.skin_body)
        body.pack(fill="both", expand=True)

        self._sidebar = ctk.CTkFrame(body, fg_color=self.skin_body, width=470)
        self._sidebar.pack(side="left", fill="y")
        self._sidebar.pack_propagate(False)

        self._center = ctk.CTkFrame(body, fg_color=self.skin_body)
        self._center.pack(side="left", fill="both", expand=True)

        self._build_sidebar()
        self._build_center()
        self._build_bottom()

        # 右键设置菜单
        self._build_context_menu()

        # 帮助 / 关于按钮放到标题栏右侧
        help_btn = ctk.CTkButton(
            self._title_frame,
            text="ℹ️ 帮助",
            width=80,
            fg_color=BUTTON_PRIMARY,
            hover_color=BUTTON_SECONDARY,
            text_color="#FFFFFF",
            command=self.on_help,
        )
        help_btn.pack(side="right", padx=4, pady=6)
        about_btn = ctk.CTkButton(
            self._title_frame,
            text="📋 关于",
            width=80,
            fg_color=BUTTON_SECONDARY,
            hover_color=BUTTON_PRIMARY,
            text_color="#FFFFFF",
            command=self.on_about,
        )
        about_btn.pack(side="right", padx=4, pady=6)
        skin_btn = ctk.CTkButton(
            self._title_frame,
            text="🎨 皮肤",
            width=80,
            fg_color=BUTTON_SECONDARY,
            hover_color=BUTTON_PRIMARY,
            text_color="#FFFFFF",
            command=self.on_cycle_skin,
        )
        skin_btn.pack(side="right", padx=4, pady=6)
        lang_btn = ctk.CTkButton(
            self._title_frame,
            text="🌐 中文/En",
            width=100,
            fg_color=BUTTON_PRIMARY,
            hover_color=BUTTON_SECONDARY,
            text_color="#FFFFFF",
            command=self.on_toggle_lang,
        )
        lang_btn.pack(side="right", padx=4, pady=6)

        # 全选/反选按钮（服务商列表标题行）
        row = ctk.CTkFrame(self._sidebar, fg_color=self.skin_body)
        row.pack(fill="x", padx=6, pady=6)
        ctk.CTkLabel(row, text="📋 服务商列表", text_color=TAG_TEXT, font=(self.settings.get("font_family", "微软雅黑"), 12, "bold")).pack(side="left")
        ctk.CTkButton(
            row,
            text="✅ 全选",
            width=72,
            fg_color=BUTTON_PRIMARY,
            hover_color=BUTTON_SECONDARY,
            text_color="#FFFFFF",
            command=self.select_all_providers,
        ).pack(side="right", padx=2)
        ctk.CTkButton(
            row,
            text="❌ 反选",
            width=72,
            fg_color=BUTTON_SECONDARY,
            hover_color=BUTTON_PRIMARY,
            text_color="#FFFFFF",
            command=self.invert_provider_selection,
        ).pack(side="right", padx=2)

    def _build_sidebar(self) -> None:
        # 服务商列表：带勾选框，支持多选 + 拖放（CTk 暂无统一拖放，简化）
        self.provider_frame = ctk.CTkFrame(self._sidebar, fg_color=self.skin_body)
        self.provider_frame.pack(fill="both", expand=True, padx=6, pady=4)
        self.provider_canvas = tk.Canvas(self.provider_frame, bg=self.skin_body, highlightthickness=0)
        self.provider_sb = ctk.CTkScrollbar(self.provider_frame, command=self.provider_canvas.yview, fg_color=SCROLL_BG, button_color=SCROLL_FG, button_hover_color=SCROLL_FG)
        self.provider_inner = ctk.CTkFrame(self.provider_canvas, fg_color=self.skin_body)
        self.provider_inner.bind(
            "<Configure>",
            lambda e: self.provider_canvas.configure(scrollregion=self.provider_canvas.bbox("all")),
        )
        self.provider_canvas.create_window((0, 0), window=self.provider_inner, anchor="nw")
        self.provider_canvas.configure(yscrollcommand=self.provider_sb.set)
        self.provider_canvas.pack(side="left", fill="both", expand=True)
        self.provider_sb.pack(side="right", fill="y")

        def _on_mousewheel(event):
            self.provider_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self.provider_canvas.bind_all("<MouseWheel>", _on_mousewheel)

        # 底部操作按钮两行（颜色交替）
        btn_row1 = ctk.CTkFrame(self._sidebar, fg_color=self.skin_body)
        btn_row1.pack(fill="x", padx=6, pady=(0, 2))
        for (txt, tip, cmd) in [
            ("➕ 新增", "添加新的服务商配置", self.on_add_provider),
            ("✏️ 编辑", "编辑选中的服务商", self.on_edit_provider),
            ("🗑️ 删除", "删除选中的服务商", self.on_delete_selected),
        ]:
            b = ctk.CTkButton(
                btn_row1,
                text=txt,
                fg_color=BUTTON_PRIMARY,
                hover_color=BUTTON_SECONDARY,
                text_color="#FFFFFF",
                command=cmd,
            )
            b.pack(side="left", fill="x", expand=True, padx=2)
            self._bind_tooltip(b, tip)

        btn_row2 = ctk.CTkFrame(self._sidebar, fg_color=self.skin_body)
        btn_row2.pack(fill="x", padx=6, pady=(0, 6))
        for (txt, tip, cmd) in [
            ("🔓 启用", "启用选中服务商", self.on_enable_selected),
            ("🔒 禁用", "禁用选中服务商", self.on_disable_selected),
            ("📦 导出", "导出服务商列表为 JSON", self.on_export_json),
        ]:
            b = ctk.CTkButton(
                btn_row2,
                text=txt,
                fg_color=BUTTON_SECONDARY,
                hover_color=BUTTON_PRIMARY,
                text_color="#FFFFFF",
                command=cmd,
            )
            b.pack(side="left", fill="x", expand=True, padx=2)
            self._bind_tooltip(b, tip)

    def _build_center(self) -> None:
        # 当前选中 & 一键切换
        head = ctk.CTkFrame(self._center, fg_color=self.skin_body)
        head.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(head, text="🔑 当前服务商", text_color=TAG_TEXT, font=(self.settings.get("font_family","微软雅黑"), 12, "bold")).pack(side="left")
        self.current_lbl = ctk.CTkLabel(head, text="（未设置）", text_color="#C62828", font=(self.settings.get("font_family","微软雅黑"), 12, "bold"))
        self.current_lbl.pack(side="left", padx=8)
        self.status_led = tk.Label(head, text="●", fg="#95a5a6", font=("Arial", 16))
        self.status_led.pack(side="left", padx=4)
        self.token_lbl = ctk.CTkLabel(head, text="", text_color="#6A1B9A", font=(self.settings.get("font_family","微软雅黑"), 11))
        self.token_lbl.pack(side="left", padx=12)

        # 服务商下拉选择
        pick_row = ctk.CTkFrame(self._center, fg_color=self.skin_body)
        pick_row.pack(fill="x", padx=10, pady=6)
        ctk.CTkLabel(pick_row, text="📋 选择要切换的服务商", text_color=TAG_TEXT).pack(side="left")
        self.provider_combo = ctk.CTkComboBox(pick_row, values=[], width=300, fg_color=TEXT_BG, text_color=INPUT_FG)
        self.provider_combo.pack(side="left", padx=6)
        try:
            self.provider_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_combo_selected())
        except Exception:
            pass
        self.auto_backup = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            pick_row,
            text="切换前自动备份",
            variable=self.auto_backup,
            fg_color=CHECK_BG,
            hover_color=CHECK_BG,
            border_color=CHECK_BORDER,
            checkmark_color=CHECK_FG,
            text_color=TAG_TEXT,
        ).pack(side="left", padx=10)

        # 切换 / 备份 / 回滚
        row1 = ctk.CTkFrame(self._center, fg_color=self.skin_body)
        row1.pack(fill="x", padx=10, pady=4)
        for (txt, tip, cmd) in [
            ("🚀 一键切换", "一键把选中服务商写入所有 CLI 配置", self.on_switch),
            ("📦 立即备份", "立即对所有 CLI 配置生成快照备份", self.on_backup),
            ("↩️ 回滚备份", "从备份快照恢复", self.on_rollback),
        ]:
            b = ctk.CTkButton(
                row1,
                text=txt,
                fg_color=BUTTON_PRIMARY,
                hover_color=BUTTON_SECONDARY,
                text_color="#FFFFFF",
                command=cmd,
            )
            b.pack(side="left", fill="x", expand=True, padx=4)
            self._bind_tooltip(b, tip)

        row2 = ctk.CTkFrame(self._center, fg_color=self.skin_body)
        row2.pack(fill="x", padx=10, pady=(2, 6))
        for (txt, tip, cmd) in [
            ("📂 导入 JSON", "从 JSON 导入服务商配置", self.on_import_json),
            ("🔑 查看备份", "打开备份目录", self.on_open_backup_dir),
            ("📊 配置目录", "打开程序配置目录", self.on_open_config_dir),
        ]:
            b = ctk.CTkButton(
                row2,
                text=txt,
                fg_color=BUTTON_SECONDARY,
                hover_color=BUTTON_PRIMARY,
                text_color="#FFFFFF",
                command=cmd,
            )
            b.pack(side="left", fill="x", expand=True, padx=4)
            self._bind_tooltip(b, tip)

        # 代理 / Hermes 相关 — 初始值从 config.yaml 读（proxy 分区 + ui_settings 分区）
        proxy_cfg = self.engine.manager.get_proxy_settings() or {}
        proxy_defaults = {
            "proxy_host": proxy_cfg.get("host") or self.settings.get("proxy_host") or "127.0.0.1",
            "proxy_port": str(proxy_cfg.get("port") or self.settings.get("proxy_port") or 8787),
            "virtual_model": (
                proxy_cfg.get("virtual_model")
                or self.settings.get("virtual_model")
                or HERMES_VIRTUAL_MODEL
            ),
        }

        proxy_box = ctk.CTkFrame(self._center, fg_color=self.skin_body)
        proxy_box.pack(fill="x", padx=10, pady=(6, 4))
        ctk.CTkLabel(proxy_box, text="🌐 API 代理地址", text_color=TAG_TEXT).pack(side="left")
        self.proxy_host_var = tk.StringVar(value=proxy_defaults["proxy_host"])
        self.proxy_port_var = tk.StringVar(value=proxy_defaults["proxy_port"])
        self.virtual_model_var = tk.StringVar(value=proxy_defaults["virtual_model"])
        host_entry = ctk.CTkEntry(proxy_box, width=140, fg_color=TEXT_BG, text_color=INPUT_FG)
        host_entry.insert(0, self.proxy_host_var.get())
        host_entry.bind("<KeyRelease>", lambda e: self._sync_proxy_host())
        host_entry.pack(side="left", padx=4)
        self._proxy_host_entry = host_entry
        port_entry = ctk.CTkEntry(proxy_box, width=60, fg_color=TEXT_BG, text_color=INPUT_FG)
        port_entry.insert(0, self.proxy_port_var.get())
        port_entry.bind("<KeyRelease>", lambda e: self._sync_proxy_port())
        port_entry.pack(side="left", padx=4)
        self._proxy_port_entry = port_entry
        ctk.CTkLabel(proxy_box, text="虚拟模型 ID", text_color=TAG_TEXT).pack(side="left", padx=(10, 2))
        vm_entry = ctk.CTkEntry(proxy_box, width=180, fg_color=TEXT_BG, text_color=INPUT_FG)
        vm_entry.insert(0, self.virtual_model_var.get())
        vm_entry.bind("<KeyRelease>", lambda e: self._sync_vm())
        vm_entry.pack(side="left", padx=4)
        self._proxy_vm_entry = vm_entry

        # 💾 保存按钮：把 host/port/virtual 写回 config.yaml（proxy 分区 + ui_settings 分区）
        save_btn = ctk.CTkButton(
            proxy_box,
            text="💾 保存设置",
            fg_color=BUTTON_PRIMARY,
            hover_color=BUTTON_SECONDARY,
            text_color="#FFFFFF",
            command=self.on_save_proxy_settings,
        )
        save_btn.pack(side="left", padx=8)
        self._bind_tooltip(save_btn, "保存代理地址、端口、虚拟模型 ID 到 config.yaml（下次启动自动读取）")

        self.proxy_addr_lbl = ctk.CTkLabel(
            proxy_box,
            text=f"http://{self.proxy_host_var.get()}:{self.proxy_port_var.get()}/v1  (未启动)",
            text_color="#C62828",
        )
        self.proxy_addr_lbl.pack(side="left", padx=4)

        # 操作按钮两行（按钮颜色一行一种交替）
        proxy_btns1 = ctk.CTkFrame(self._center, fg_color=self.skin_body)
        proxy_btns1.pack(fill="x", padx=10, pady=(2, 2))
        for (txt, tip, cmd) in [
            ("🚀 启动代理", "启动本地 OpenAI 兼容 HTTP 代理，第三方 Agent 可通过它调用当前服务商", self.on_start_proxy),
            ("✅ 切换+启动", "一键切换到选中服务商、配置所有 CLI（含 Hermes 指向代理）、并把代理跑起来", self.on_switch_and_serve),
            ("📋 测试代理", "启动代理并发送一次 /v1/chat/completions 冒烟测试", self.on_test_proxy),
        ]:
            b = ctk.CTkButton(
                proxy_btns1,
                text=txt,
                fg_color=BUTTON_PRIMARY,
                hover_color=BUTTON_SECONDARY,
                text_color="#FFFFFF",
                command=cmd,
            )
            b.pack(side="left", fill="x", expand=True, padx=4)
            self._bind_tooltip(b, tip)

        proxy_btns2 = ctk.CTkFrame(self._center, fg_color=self.skin_body)
        proxy_btns2.pack(fill="x", padx=10, pady=(0, 4))
        for (txt, tip, cmd) in [
            ("🔄 停止代理", "停止当前运行的 API 网关", self.on_stop_proxy),
            ("📂 代理日志", "打开 config 目录查看运行日志", self.on_open_config_dir),
            ("📋 配置说明", "打开帮助查看代理使用说明", self.on_help),
        ]:
            b = ctk.CTkButton(
                proxy_btns2,
                text=txt,
                fg_color=BUTTON_SECONDARY,
                hover_color=BUTTON_PRIMARY,
                text_color="#FFFFFF",
                command=cmd,
            )
            b.pack(side="left", fill="x", expand=True, padx=4)
            self._bind_tooltip(b, tip)

        # 目标 CLI 选择
        cli_box = ctk.CTkFrame(self._center, fg_color=self.skin_body)
        cli_box.pack(fill="x", padx=10, pady=6)
        ctk.CTkLabel(cli_box, text="📦 目标 CLI 工具", text_color=TAG_TEXT).pack(side="left")
        # 读取之前保存的勾选状态；如果没保存过，默认全勾选
        saved_clis = self.settings.get("enabled_clis") if isinstance(self.settings, dict) else None
        if not isinstance(saved_clis, list) or not saved_clis:
            saved_clis = []  # 首次启动默认不勾选任何 CLI
        saved_set = {str(k) for k in saved_clis}
        # 自己维护一份勾选状态（避免依赖 CTkCheckBox 内部状态和 BooleanVar 绑定）
        self.cli_selected = {k: (k in saved_set) for k in CLI_TEMPLATES.keys()}
        self.cli_chks = {}
        for key, meta in CLI_TEMPLATES.items():
            chk = ctk.CTkCheckBox(
                cli_box,
                text=meta["display"],
                fg_color=CHECK_BG,
                hover_color=CHECK_BG,
                border_color=CHECK_BORDER,
                checkmark_color=CHECK_FG,
                text_color=TAG_TEXT,
            )
            # CTkCheckBox 默认状态和我们 self.cli_selected 一致
            try:
                if self.cli_selected.get(key):
                    chk.select()
                else:
                    chk.deselect()
            except Exception:
                pass
            # 鼠标松开事件：手动翻转自己维护的状态 + 立即写 config.yaml
            def _on_toggle(_e, k=key, _chk=chk):
                try:
                    v = False
                    try:
                        v = bool(_chk.get())
                    except Exception:
                        # fallback：自己维护的状态取反
                        v = not bool(self.cli_selected.get(k, False))
                    self.cli_selected[k] = bool(v)
                    # 强制同步到控件（防止 CTk 显示和我们记录的不一致）
                    if v:
                        try: _chk.select()
                        except Exception: pass
                    else:
                        try: _chk.deselect()
                        except Exception: pass
                finally:
                    self._save_enabled_clis()
            try:
                chk.bind("<ButtonRelease-1>", _on_toggle)
                chk.bind("<ButtonRelease-2>", _on_toggle)
                chk.bind("<ButtonRelease-3>", _on_toggle)
            except Exception:
                pass
            self.cli_chks[key] = chk
            chk.pack(side="left", padx=6)

        # 代理 API Key 管理
        key_box = ctk.CTkFrame(self._center, fg_color=self.skin_body)
        key_box.pack(fill="x", padx=10, pady=6)
        ctk.CTkLabel(key_box, text="🔑 代理 API Key", text_color=TAG_TEXT).pack(side="left")

        # 从 config.yaml 里的 proxy.api_key 读取（用 manager 原生的 get_proxy_settings，不走 self.settings）
        saved_key = ""
        try:
            proxy_cfg = self.engine.manager.get_proxy_settings() or {}
            if isinstance(proxy_cfg, dict):
                saved_key = str(proxy_cfg.get("api_key", "") or "")
        except Exception:
            saved_key = ""
        self.proxy_key_var = tk.StringVar(value=saved_key)

        self.key_entry = ctk.CTkEntry(
            key_box,
            width=300,
            textvariable=self.proxy_key_var,
            fg_color=TEXT_BG,
            text_color=OUTPUT_FG,
            placeholder_text="（未设置，外部 Agent 连代理无需鉴权）",
        )
        self.key_entry.pack(side="left", padx=6, ipady=2)

        def _on_gen():
            """生成真实风格的 sk- 前缀 key（43 字符字母数字，大小写混合）。"""
            import random as _r
            alphabet = string.ascii_letters + string.digits
            new_key = "sk-" + "".join(_r.choice(alphabet) for _ in range(43))
            self.proxy_key_var.set(new_key)
            self.key_entry.icursor(tk.END)
            self._toast("ℹ️ 已生成 Key，点击 💾 保存")

        def _on_save():
            """把当前输入框里的 Key 保存到 config.yaml。"""
            self._save_proxy_key()
            val = (self.proxy_key_var.get() or "").strip()
            if val:
                self._toast("✅ 代理 API Key 已保存")
            else:
                self._toast("✅ 代理 API Key 已清除并保存")

        def _on_copy():
            """复制输入框里的 key 到剪贴板。"""
            val = (self.proxy_key_var.get() or "").strip()
            if not val:
                self._toast("❌ 没有可复制的 Key")
                return
            try:
                self.clipboard_clear()
                self.clipboard_append(val)
                self.update()
                self._toast("✅ 已复制代理 API Key 到剪贴板")
            except Exception as e:
                self._toast(f"❌ 复制失败: {e}")

        def _on_del():
            """清除 key（同时保存空值到 config.yaml）。"""
            self.proxy_key_var.set("")
            self.key_entry.delete(0, tk.END)
            self._save_proxy_key()
            self._toast("✅ 已清除代理 API Key（代理不再鉴权）")

        key_gen = ctk.CTkButton(
            key_box,
            text="🔑 生成",
            fg_color=BUTTON_PRIMARY,
            hover_color=BUTTON_SECONDARY,
            text_color="#FFFFFF",
            width=80,
            command=_on_gen,
        )
        key_gen.pack(side="left", padx=4)

        key_save = ctk.CTkButton(
            key_box,
            text="� 保存",
            fg_color=BUTTON_SECONDARY,
            hover_color=BUTTON_PRIMARY,
            text_color="#FFFFFF",
            width=80,
            command=_on_save,
        )
        key_save.pack(side="left", padx=4)

        key_copy = ctk.CTkButton(
            key_box,
            text="📋 复制",
            fg_color=BUTTON_PRIMARY,
            hover_color=BUTTON_SECONDARY,
            text_color="#FFFFFF",
            width=80,
            command=_on_copy,
        )
        key_copy.pack(side="left", padx=4)

        key_del = ctk.CTkButton(
            key_box,
            text="❌ 删除",
            fg_color=BUTTON_SECONDARY,
            hover_color=BUTTON_PRIMARY,
            text_color="#FFFFFF",
            width=80,
            command=_on_del,
        )
        key_del.pack(side="left", padx=4)

        # 切换结果显示
        res_box = ctk.CTkFrame(self._center, fg_color=self.skin_body)
        res_box.pack(fill="both", expand=True, padx=10, pady=10)
        ctk.CTkLabel(res_box, text="📋 操作结果", text_color=TAG_TEXT).pack(anchor="w")
        self.result_text = ctk.CTkTextbox(res_box, fg_color=TEXT_BG, text_color=OUTPUT_FG, height=180, corner_radius=4)
        self.result_text.pack(fill="both", expand=True)

    def _build_right_log(self) -> None:
        # 日志框标题 + 操作按钮（两行按钮分别用主色/辅色交替）
        header = ctk.CTkFrame(self._right, fg_color=self.skin_body)
        header.pack(fill="x", padx=4, pady=(4, 2))
        ctk.CTkLabel(
            header,
            text="📋 实时日志",
            text_color=TAG_TEXT,
            font=(self.settings.get("font_family", "微软雅黑"), 12, "bold"),
        ).pack(side="left")

        # 第一行按钮：主色 BUTTON_PRIMARY
        row1 = ctk.CTkFrame(self._right, fg_color=self.skin_body)
        row1.pack(fill="x", padx=4, pady=2)
        for (txt, cmd) in [
            ("🗑️ 清空", self.clear_log),
            ("📋 复制", self.copy_log),
            ("� 停止代理", self.on_stop_proxy),
        ]:
            b = ctk.CTkButton(
                row1,
                text=txt,
                fg_color=BUTTON_PRIMARY,
                hover_color=BUTTON_SECONDARY,
                text_color="#FFFFFF",
                command=cmd,
            )
            b.pack(side="left", fill="x", expand=True, padx=2)

        # 第二行按钮：辅色 BUTTON_SECONDARY（交替颜色）
        row2 = ctk.CTkFrame(self._right, fg_color=self.skin_body)
        row2.pack(fill="x", padx=4, pady=(2, 2))
        for (txt, cmd) in [
            ("📂 查看日志文件", self.on_open_config_dir),
            ("� 配置目录", self.on_open_config_dir),
        ]:
            b = ctk.CTkButton(
                row2,
                text=txt,
                fg_color=BUTTON_SECONDARY,
                hover_color=BUTTON_PRIMARY,
                text_color="#FFFFFF",
                command=cmd,
            )
            b.pack(side="left", fill="x", expand=True, padx=2)

        # 日志 Text（白色背景，彩色显示）
        lf = ctk.CTkFrame(self._right, fg_color=self.skin_body)
        lf.pack(fill="both", expand=True, padx=4, pady=2)
        self.log_text = ctk.CTkTextbox(
            lf,
            fg_color=LOG_BG,
            text_color=LOG_TEXT_COLOR,
            corner_radius=4,
            wrap="word",
        )
        # 彩色标签：不同 icon 用不同前景色
        try:
            self.log_text.tag_config("ts", foreground="#D8BFD8")        # 时间戳 浅粉色
            self.log_text.tag_config("ok", foreground="#008080")        # ✅ 青色
            self.log_text.tag_config("err", foreground="#C62828")      # ❌ 红色
            self.log_text.tag_config("info", foreground="#1565C0")     # ℹ️ 深蓝
            self.log_text.tag_config("data", foreground="#6A1B9A")      # 📊 紫色
            self.log_text.tag_config("msg", foreground="#00695C")      # 📋 深青
        except Exception:
            pass
        self.log_text.pack(fill="both", expand=True)

    def _build_bottom(self) -> None:
        self._bottom = ctk.CTkFrame(self, fg_color=self.skin_body)
        self._bottom.pack(fill="x", side="bottom")
        self._bottom.pack_propagate(False)

        # 日志区
        log_frame = ctk.CTkFrame(self._bottom, fg_color=self.skin_body)
        log_frame.pack(fill="both", expand=True, padx=4, pady=(2, 0))

        # 按钮行
        btn_row = ctk.CTkFrame(log_frame, fg_color=self.skin_body)
        btn_row.pack(fill="x")
        ctk.CTkLabel(btn_row, text="📋 实时日志", text_color=TAG_TEXT,
            font=(self.settings.get("font_family", "微软雅黑"), 11, "bold")).pack(side="left")
        ctk.CTkButton(btn_row, text="🗑️ 清空", width=80,
            fg_color=BUTTON_PRIMARY, hover_color=BUTTON_SECONDARY,
            text_color="#FFFFFF", command=self.clear_log).pack(side="right", padx=2)
        ctk.CTkButton(btn_row, text="📋 复制", width=80,
            fg_color=BUTTON_SECONDARY, hover_color=BUTTON_PRIMARY,
            text_color="#FFFFFF", command=self.copy_log).pack(side="right", padx=2)

        # 日志文本框
        lf = ctk.CTkFrame(log_frame, fg_color=self.skin_body)
        lf.pack(fill="both", expand=True, pady=(2, 0))
        self.log_text = ctk.CTkTextbox(lf, fg_color="#FFFFFF", text_color="#008080",
            corner_radius=4, wrap="word", height=140)
        try:
            self.log_text.tag_config("ts", foreground="#D8BFD8")
            self.log_text.tag_config("ok", foreground="#008080")
            self.log_text.tag_config("err", foreground="#C62828")
            self.log_text.tag_config("info", foreground="#1565C0")
            self.log_text.tag_config("data", foreground="#6A1B9A")
            self.log_text.tag_config("msg", foreground="#00695C")
        except Exception:
            pass
        self.log_text.pack(fill="both", expand=True)

        # 进度条行
        prog_row = ctk.CTkFrame(self._bottom, fg_color=self.skin_body, height=26)
        prog_row.pack(fill="x")
        prog_row.pack_propagate(False)
        self.progress = ttk.Progressbar(prog_row, mode="determinate", length=260)
        self.progress.pack(side="right", padx=6, pady=3)
        try:
            style = ttk.Style()
            style.theme_use("clam")
            style.configure("CCSwitch.Horizontal.TProgressbar", background=PROGRESS_FG, troughcolor=PROGRESS_BG)
            self.progress.configure(style="CCSwitch.Horizontal.TProgressbar")
        except Exception:
            pass
        ctk.CTkLabel(prog_row, textvariable=self.progress_text, text_color=TAG_TEXT).pack(side="left", padx=8)

    def _build_context_menu(self) -> None:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="字体设置", command=self.on_font_settings)
        menu.add_command(label="字形设置", command=self.on_style_settings)
        menu.add_command(label="字号大小", command=self.on_size_settings)
        menu.add_separator()
        menu.add_command(label="颜色设置 / 主题", command=self.on_full_settings)
        menu.add_command(label="循环皮肤", command=self.on_cycle_skin)
        self.menu = menu

        def show(e):
            try:
                menu.tk_popup(e.x_root, e.y_root)
            finally:
                menu.grab_release()

        for w in (self, self._title_frame):
            try:
                w.bind("<Button-3>", show)
            except Exception:
                pass

    # ----------------------- 进度 -----------------------
    def set_progress(self, msg: str, value: int | None = None, maximum: int | None = None) -> None:
        self.progress_text.set(msg)
        if value is not None:
            self.progress["value"] = value
        if maximum is not None:
            self.progress["maximum"] = maximum

    # ----------------------- 日志 -----------------------
    def _log(self, icon: str, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            # 时间戳 浅粉色
            self.log_text.insert(tk.END, f"{ts}  ", "ts")
            # icon + 消息，按 icon 分色
            if icon in ("✅",):
                tag = "ok"
            elif icon in ("❌",):
                tag = "err"
            elif icon in ("ℹ️",):
                tag = "info"
            elif icon in ("📊",):
                tag = "data"
            else:
                tag = "msg"
            self.log_text.insert(tk.END, f"{icon}  {msg}\n", tag)
            self.log_text.see(tk.END)
        except Exception:
            # 兜底
            try:
                self.log_text.insert(tk.END, f"{ts}  {icon}  {msg}\n")
                self.log_text.see(tk.END)
            except Exception:
                pass

    def _log_status(self, ok: bool, msg: str) -> None:
        self._log("✅" if ok else "❌", msg)

    def clear_log(self) -> None:
        try:
            self.log_text.delete("1.0", tk.END)
        except Exception:
            pass

    def copy_log(self) -> None:
        try:
            txt = self.log_text.get("1.0", tk.END)
            self.clipboard_clear()
            self.clipboard_append(txt)
            FloatingToast(self, "✅ 日志已复制", ok=True)
        except Exception as e:
            FloatingToast(self, f"❌ 复制失败: {e}", ok=False)

    # ----------------------- 提示 / 悬浮 -----------------------
    def _bind_tooltip(self, widget, text: str) -> None:
        # 用独立的闭包变量，彻底避免 leave/show_tip 对同一个 dict 字段的竞争访问
        state = {"job": None}
        win_holder = {"toplevel": None}

        def _cancel_job():
            if state["job"] is not None:
                try:
                    self.after_cancel(state["job"])
                finally:
                    state["job"] = None

        def _close_tip():
            top = win_holder["toplevel"]
            if top is not None:
                try:
                    if top.winfo_exists():
                        top.destroy()
                except Exception:
                    pass
                finally:
                    win_holder["toplevel"] = None

        def enter(_e):
            _cancel_job()
            win_holder["toplevel"] = None
            state["job"] = self.after(500, lambda: self._show_tip(widget, text, win_holder))

        def leave(_e):
            _cancel_job()
            _close_tip()

        widget.bind("<Enter>", enter)
        widget.bind("<Leave>", leave)

    def _show_tip(self, widget, text: str, win_holder: dict) -> None:
        if win_holder.get("toplevel") is not None:
            return
        try:
            if not widget.winfo_exists():
                return
            win = tk.Toplevel(widget)
            win.overrideredirect(True)
            try:
                win.attributes("-topmost", True)
            except Exception:
                pass
            tk.Label(
                win,
                text=text,
                bg="#FFFFE0",
                fg="#333",
                font=(self.settings.get("font_family", "微软雅黑"), 10),
                padx=8,
                pady=4,
                relief="solid",
                borderwidth=1,
            ).pack()
            win.update_idletasks()
            try:
                mx = widget.winfo_rootx()
                my = widget.winfo_rooty() + widget.winfo_height() + 4
                w = win.winfo_width()
                sw = self.winfo_screenwidth()
                if mx + w > sw:
                    mx = sw - w - 10
                win.geometry(f"+{max(mx,0)}+{max(my,0)}")
            except Exception:
                pass
            win_holder["toplevel"] = win

            def _auto_close():
                if win_holder.get("toplevel") is win:
                    try:
                        if win.winfo_exists():
                            win.destroy()
                    except Exception:
                        pass
                    win_holder["toplevel"] = None

            win.after(3000, _auto_close)
        except Exception:
            win_holder["toplevel"] = None

    # ----------------------- 服务商列表 -----------------------
    def refresh_provider_list(self) -> None:
        for c in self.provider_inner.winfo_children():
            c.destroy()
        self.provider_vars = []
        providers = self.engine.manager.list_all()
        for i, p in enumerate(providers):
            row = ctk.CTkFrame(self.provider_inner, fg_color=self.skin_body)
            row.pack(fill="x", padx=4, pady=2)

            v = tk.BooleanVar(value=False)
            self.provider_vars.append((v, p.alias))
            chk = ctk.CTkCheckBox(
                row,
                variable=v,
                width=24,
                height=24,
                fg_color=CHECK_BG,
                hover_color=CHECK_BG,
                border_color=CHECK_BORDER,
                checkmark_color=CHECK_FG,
                text="",
            )
            chk.pack(side="left", padx=4, pady=2)

            status = "✅启用" if p.enabled else "❌禁用"
            cur = "（当前）" if p.alias == self.engine.manager.current_alias else ""
            color = "#2E7D32" if p.alias == self.engine.manager.current_alias else "#333333"
            lbl_txt = f"{p.display_name} [{p.alias}] {status} {cur}"
            lbl = ctk.CTkLabel(row, text=lbl_txt, text_color=color, anchor="w")
            lbl.pack(side="left", padx=6, fill="x", expand=True)

            # 切换按钮
            switch_btn = ctk.CTkButton(
                row,
                text="🚀切换",
                width=72,
                fg_color=BUTTON_PRIMARY,
                hover_color=BUTTON_SECONDARY,
                text_color="#FFFFFF",
                command=lambda a=p.alias: self._switch_in_background(a),
            )
            switch_btn.pack(side="right", padx=2)

            edit_btn = ctk.CTkButton(
                row,
                text="✅",
                width=34,
                fg_color="#43A047",
                hover_color="#2E7D32",
                text_color="#FFFFFF",
                command=lambda a=p.alias: self._test_provider_availability(a),
            )
            edit_btn.pack(side="right", padx=2)
            self._bind_tooltip(edit_btn, f"测试 {p.display_name} 服务商可用性（直连上游）")

            edit_btn2 = ctk.CTkButton(
                row,
                text="✏️",
                width=34,
                fg_color=BUTTON_SECONDARY,
                hover_color=BUTTON_PRIMARY,
                text_color="#FFFFFF",
                command=lambda a=p.alias: self._edit_by_alias(a),
            )
            edit_btn2.pack(side="right", padx=2)

        # 刷新下拉
        names = [p.alias for p in providers]
        self.provider_combo.configure(values=names)
        if self.engine.manager.current_alias in names:
            self.provider_combo.set(self.engine.manager.current_alias)
        elif names:
            self.provider_combo.set(names[0])
        else:
            self.provider_combo.set("")

        # 状态显示
        cur = self.engine.manager.get_current()
        if cur:
            self.current_lbl.configure(text=f"{cur.display_name} [{cur.alias}] 模型={cur.model or '(未设置)'}")
            self.status_led.configure(fg="#2ECC71")
        else:
            self.current_lbl.configure(text="（未设置）")
            self.status_led.configure(fg="#95a5a6")

    def select_all_providers(self) -> None:
        for v, _ in getattr(self, "provider_vars", []):
            v.set(True)

    def invert_provider_selection(self) -> None:
        for v, _ in getattr(self, "provider_vars", []):
            v.set(not v.get())

    def _selected_aliases(self) -> list[str]:
        return [a for v, a in getattr(self, "provider_vars", []) if v.get()]

    # ----------------------- 动作 -----------------------
    def _run_async(self, fn) -> None:
        threading.Thread(target=fn, daemon=True).start()

    def on_add_provider(self) -> None:
        def _saved(p: "Provider", closed: bool) -> None:
            self._log_status(True, f"已添加/更新服务商: {p.alias}")
            self.refresh_provider_list()

        dlg = ProviderDialog(self, manager=self.engine.manager, on_saved=_saved)
        self.wait_window(dlg)
        # 兜底：对话框没传 manager 时保留旧逻辑
        if dlg.result is not None and not self.engine.manager.providers.get(dlg.result.alias):
            try:
                self.engine.manager.add_or_update(dlg.result)
                self._log_status(True, f"已添加/更新服务商: {dlg.result.alias}")
                self.refresh_provider_list()
            except Exception as e:
                self._log_status(False, f"保存失败: {e}")

    def on_edit_provider(self) -> None:
        aliases = self._selected_aliases()
        if not aliases:
            tk.messagebox.showinfo("提示", "请先在列表中勾选一个服务商。", parent=self)
            return

        def _saved(p: "Provider", closed: bool) -> None:
            self._log_status(True, f"已更新服务商: {p.alias}")
            self.refresh_provider_list()

        p = self.engine.manager.get(aliases[0])
        dlg = ProviderDialog(self, p, manager=self.engine.manager, on_saved=_saved)
        self.wait_window(dlg)
        if dlg.result is not None and self.engine.manager.providers.get(dlg.result.alias) is None:
            try:
                self.engine.manager.add_or_update(dlg.result)
                self._log_status(True, f"已更新服务商: {dlg.result.alias}")
                self.refresh_provider_list()
            except Exception as e:
                self._log_status(False, f"保存失败: {e}")

    def _edit_by_alias(self, alias: str) -> None:
        p = self.engine.manager.get(alias)

        def _saved(pp: "Provider", closed: bool) -> None:
            self._log_status(True, f"已更新服务商: {pp.alias}")
            self.refresh_provider_list()

        dlg = ProviderDialog(self, p, manager=self.engine.manager, on_saved=_saved)
        self.wait_window(dlg)
        if dlg.result is not None and self.engine.manager.providers.get(dlg.result.alias) is None:
            try:
                self.engine.manager.add_or_update(dlg.result)
                self._log_status(True, f"已更新服务商: {dlg.result.alias}")
                self.refresh_provider_list()
            except Exception as e:
                self._log_status(False, f"保存失败: {e}")

    def on_delete_selected(self) -> None:
        aliases = self._selected_aliases()
        if not aliases:
            tk.messagebox.showinfo("提示", "请先在列表中勾选要删除的服务商。", parent=self)
            return
        if not tk.messagebox.askyesno("确认", f"确认删除 {len(aliases)} 个服务商？", parent=self):
            return
        for a in aliases:
            if self.engine.manager.remove(a):
                self._log_status(True, f"已删除服务商: {a}")
            else:
                self._log_status(False, f"删除失败: {a}")
        self.refresh_provider_list()

    def on_enable_selected(self) -> None:
        aliases = self._selected_aliases()
        for a in aliases:
            if self.engine.manager.toggle_enabled(a) is True:
                self._log_status(True, f"已启用: {a}")
        self.refresh_provider_list()

    def on_disable_selected(self) -> None:
        aliases = self._selected_aliases()
        for a in aliases:
            if self.engine.manager.toggle_enabled(a) is False:
                self._log_status(True, f"已禁用: {a}")
        self.refresh_provider_list()

    # ---- 代理相关状态 ----
    _proxy_httpd = None
    _proxy_thread = None
    _proxy_host = "127.0.0.1"
    _proxy_port = 0
    _proxy_state = None  # ProxyState 实例，持有 logger_cb

    def _make_logger_cb(self) -> Callable[[str, str], None]:
        """返回一个线程安全的回调：把代理日志塞回 GUI 日志区。"""
        def _cb(icon: str, msg: str) -> None:
            try:
                self.after(0, lambda: self._log(icon, msg))
                # token 日志触发用量显示刷新
                if msg.startswith("tokens:"):
                    self.after(0, self._update_token_display)
            except Exception:
                pass
        return _cb

    def _auto_start_proxy(self) -> None:
        """GUI 启动后自动起代理（读取 config.yaml proxy 分区）。"""
        def task():
            try:
                provider = self.engine.manager.get_current()
                if not provider:
                    self.after(0, lambda: self._log("ℹ️", "未设置当前服务商，跳过自动启动 API 网关"))
                    return
                proxy_cfg = self.engine.manager.get_proxy_settings() or {}
                host = (proxy_cfg.get("host") or "127.0.0.1").strip()
                try:
                    port = int(proxy_cfg.get("port") or 8787)
                except ValueError:
                    port = 8787
                virtual = (
                    proxy_cfg.get("virtual_model")
                    or self.settings.get("virtual_model")
                    or HERMES_VIRTUAL_MODEL
                ).strip() or HERMES_VIRTUAL_MODEL

                # 端口被占用就找下一个
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(0.5)
                    s.connect((host, port))
                    s.close()
                    port = pick_free_port(host, start=port + 1)
                    self.after(0, lambda p=port: self._log("ℹ️", f"端口被占用，改用 {p}"))
                except OSError:
                    pass
                except Exception:
                    pass

                # 写回 UI 输入框（必须在主线程执行）
                self.after(0, lambda h=host, p=port, v=virtual: self._apply_proxy_ui(h, p, v))

                # 启动
                state = ProxyState(self.engine.manager)
                state.set_virtual_model(virtual)
                state.logger_cb = self._make_logger_cb()
                httpd = create_proxy_http_server(host, port, state)
                import threading as _th
                t = _th.Thread(target=httpd.serve_forever, daemon=True)
                t.start()
                self._proxy_httpd = httpd
                self._proxy_thread = t
                self._proxy_state = state
                self._proxy_host = host
                self._proxy_port = port
                # 持久化回 config.yaml
                try:
                    self.engine.manager.set_proxy_settings({
                        "host": host,
                        "port": port,
                        "virtual_model": virtual,
                    })
                except Exception:
                    pass
                self.after(0, self._update_proxy_label)
                self.after(0, self._update_token_display)
                self.after(
                    0,
                    lambda h=host, p=port, v=virtual, d=provider.display_name: self._log(
                        "✅",
                        f"API 网关已启动 http://{h}:{p}/v1  虚拟模型={v}  当前服务商={d}",
                    ),
                )
                self.after(0, lambda h=host, p=port: FloatingToast(
                    self,
                    f"✅ API 网关已启动 http://{h}:{p}/v1",
                    ok=True,
                ))
            except Exception as e:
                self.after(0, lambda e=e: self._log("❌", f"自动启动 API 网关失败: {e}"))

        # 用主线程执行任务（socket 检查仅 0.5s 超时不会卡 UI）
        self.after(200, task)

    def _apply_proxy_ui(self, host: str, port: int, virtual: str) -> None:
        """在主线程更新代理 UI 输入框的值。"""
        try:
            self.proxy_host_var.set(host)
            self.proxy_port_var.set(str(port))
            self.virtual_model_var.set(virtual)
            self._proxy_host_entry.delete(0, tk.END)
            self._proxy_host_entry.insert(0, host)
            self._proxy_port_entry.delete(0, tk.END)
            self._proxy_port_entry.insert(0, str(port))
            self._proxy_vm_entry.delete(0, tk.END)
            self._proxy_vm_entry.insert(0, virtual)
        except Exception:
            pass

    def on_stop_proxy(self) -> None:
        """停止代理。"""
        if not getattr(self, "_proxy_httpd", None):
            FloatingToast(self, "ℹ️ API 网关未运行", ok=True)
            return
        try:
            httpd = self._proxy_httpd
            self._proxy_httpd = None
            self._proxy_thread = None
            self._proxy_state = None
            self._proxy_port = 0
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass
            FloatingToast(self, "✅ API 网关已停止", ok=True)
            self._log_status(True, "API 网关已停止")
            self._update_proxy_label()
            self._update_token_display()
        except Exception as e:
            FloatingToast(self, f"❌ 停止失败: {e}", ok=False)

    def on_save_proxy_settings(self) -> None:
        """把 UI 输入框里的代理设置保存到 config.yaml。"""
        try:
            host = (self._proxy_host_entry.get().strip() or "127.0.0.1")
            try:
                port = int(self._proxy_port_entry.get().strip() or 8787)
            except ValueError:
                port = 8787
            virtual = (self._proxy_vm_entry.get().strip() or HERMES_VIRTUAL_MODEL)
            # 校验 virtual 非空
            if not virtual:
                virtual = HERMES_VIRTUAL_MODEL

            # 写 config.yaml proxy 分区
            self.engine.manager.set_proxy_settings({
                "host": host,
                "port": port,
                "virtual_model": virtual,
            })
            # 同时写 ui_settings 分区（与其他 UI 设置同存）
            ui = self.engine.manager.get_ui_settings() or {}
            ui["proxy_host"] = host
            ui["proxy_port"] = str(port)
            ui["virtual_model"] = virtual
            self.engine.manager.set_ui_settings(ui)
            # 同时保存目标 CLI 勾选项（双保险：避免 checkbox command 不触发）
            self._save_enabled_clis()

            # 同步 StringVar
            self.proxy_host_var.set(host)
            self.proxy_port_var.set(str(port))
            self.virtual_model_var.set(virtual)

            FloatingToast(
                self,
                f"✅ 已保存: http://{host}:{port}/v1  虚拟模型={virtual}",
                ok=True,
            )
            self._log(
                "✅",
                f"代理设置已保存: host={host} port={port} virtual_model={virtual}",
            )
            self._update_proxy_label()
        except Exception as e:
            FloatingToast(self, f"❌ 保存失败: {e}", ok=False)
            self._log("❌", f"保存代理设置失败: {e}")

    def _save_enabled_clis(self) -> None:
        """把 self.cli_selected 里当前 True 的 CLI key 立即写入 config.yaml。"""
        try:
            selected = []
            selected_map = getattr(self, "cli_selected", None)
            if isinstance(selected_map, dict):
                selected = [k for k, v in selected_map.items() if bool(v)]
            # 兜底：如果 cli_selected 还没建（比如启动瞬间），从 checkbox 控件直接读
            if not selected:
                chks = getattr(self, "cli_chks", {}) or {}
                for k, chk in chks.items():
                    try:
                        if chk and bool(chk.get()):
                            selected.append(k)
                    except Exception:
                        continue
            ui = self.engine.manager.get_ui_settings() or {}
            ui["enabled_clis"] = selected
            self.engine.manager.set_ui_settings(ui)
            self.settings = self.engine.manager.get_ui_settings() or {}
            self._log("ℹ️", f"已保存勾选: {', '.join(selected) if selected else '(无)'}")
        except Exception as e:
            try:
                self._log("❌", f"保存勾选失败: {e}")
            except Exception:
                pass

    def _on_cli_check_changed(self) -> None:
        """任何一个目标 CLI 勾选改变都立即写 config.yaml。"""
        self._save_enabled_clis()

    def _on_combo_selected(self) -> None:
        """服务商下拉切换后立即生效（更新 current + 刷新列表）。"""
        alias = (self.provider_combo.get() or "").strip()
        if not alias:
            return
        try:
            ok = self.engine.manager.set_current(alias)
            if ok:
                self._toast(f"✅ 已切换当前服务商: {alias}")
            else:
                self._toast(f"❌ 切换失败: 找不到 {alias}")
        except Exception as e:
            self._toast(f"❌ 切换失败: {e}")
            return
        try:
            self.refresh_provider_list()
        except Exception:
            pass

    def _save_proxy_key(self) -> None:
        """把代理 API Key 保存到 config.yaml（proxy.api_key）。"""
        try:
            key = (self.proxy_key_var.get() if hasattr(self, "proxy_key_var") else "") or ""
            key = key.strip()
            # 用 manager 原生的 set_proxy_settings，不自己拼 settings 结构
            cur = self.engine.manager.get_proxy_settings() or {}
            cur = dict(cur)
            cur["api_key"] = key
            self.engine.manager.set_proxy_settings(cur)
            # 同步到 ui_settings 一份（兼容旧逻辑）
            try:
                ui = self.engine.manager.get_ui_settings() or {}
                ui["proxy_api_key"] = key
                self.engine.manager.set_ui_settings(ui)
            except Exception:
                pass
        except Exception as e:
            try:
                FloatingToast(self, f"❌ 保存 Key 失败: {e}", ok=False)
            except Exception:
                pass

    def _toast(self, text: str) -> None:
        """1 秒悬浮提示。"""
        ok = not text.startswith("❌")
        try:
            FloatingToast(self, text, ok=ok)
        except Exception:
            try:
                self._log("ℹ️" if ok else "❌", text)
            except Exception:
                pass

    def _on_close_and_save(self) -> None:
        """关窗口时必存所有 UI 设置（代理地址 + 目标 CLI 勾选 + 外观）。"""
        try:
            # 1) 存目标 CLI 勾选
            self._save_enabled_clis()
            # 2) 存代理 host/port/virtual_model（从 UI 输入框读）
            try:
                host = (self._proxy_host_entry.get().strip() or "127.0.0.1")
            except Exception:
                host = "127.0.0.1"
            try:
                port = int(self._proxy_port_entry.get().strip() or 8787)
            except Exception:
                port = 8787
            try:
                virtual = (self._proxy_vm_entry.get().strip() or HERMES_VIRTUAL_MODEL)
            except Exception:
                virtual = HERMES_VIRTUAL_MODEL
            try:
                self.engine.manager.set_proxy_settings({
                    "host": host, "port": port, "virtual_model": virtual,
                })
            except Exception:
                pass
            try:
                ui = self.engine.manager.get_ui_settings() or {}
                ui["proxy_host"] = host
                ui["proxy_port"] = str(port)
                ui["virtual_model"] = virtual
                self.engine.manager.set_ui_settings(ui)
            except Exception:
                pass
        finally:
            # 无论保存成功与否都关窗口
            try:
                self.destroy()
            except Exception:
                pass

    def _sync_proxy_host(self) -> None:
        try:
            self.proxy_host_var.set(self._proxy_host_entry.get().strip() or "127.0.0.1")
        except Exception:
            pass
        self._update_proxy_label()

    def _sync_proxy_port(self) -> None:
        try:
            self.proxy_port_var.set(self._proxy_port_entry.get().strip() or "0")
        except Exception:
            pass
        self._update_proxy_label()

    def _sync_vm(self) -> None:
        try:
            self.virtual_model_var.set(self._proxy_vm_entry.get().strip() or HERMES_VIRTUAL_MODEL)
        except Exception:
            pass

    def _update_proxy_label(self) -> None:
        try:
            host = self.proxy_host_var.get() or "127.0.0.1"
            port = self.proxy_port_var.get() or "0"
            txt = f"http://{host}:{port}/v1  (未启动)" if not getattr(self, "_proxy_httpd", None) else f"http://{host}:{port}/v1  (运行中)"
            color = "#2ECC71" if getattr(self, "_proxy_httpd", None) else "#C62828"
            self.proxy_addr_lbl.configure(text=txt, text_color=color)
        except Exception:
            pass

    def _update_token_display(self) -> None:
        """刷新 token 用量显示。"""
        try:
            state = getattr(self, "_proxy_state", None)
            if state:
                summary = state.get_token_summary()
                self.token_lbl.configure(text=summary)
            else:
                self.token_lbl.configure(text="")
        except Exception:
            pass

    def on_switch(self) -> None:
        alias = self.provider_combo.get().strip()
        if not alias:
            tk.messagebox.showinfo("提示", "请先选择要切换的服务商。", parent=self)
            return
        self._switch_in_background(alias)

    def _proxy_url(self) -> tuple[str, int, int]:
        host = self.proxy_host_var.get() or "127.0.0.1"
        port_raw = (self.proxy_port_var.get() or "").strip()
        try:
            port = int(port_raw) if port_raw else 0
        except ValueError:
            port = 0
        if not port:
            port = pick_free_port(host)
        return host, port, int(port)

    def _test_provider_availability(self, alias: str) -> None:
        """测试某个服务商的可用性（直连上游，不走代理）。"""
        self._log("ℹ️", f"测试服务商可用性: {alias}")

        def task():
            import json as _json
            import time as _time
            import urllib.request as _req
            import urllib.error as _err
            from core import Provider

            p = self.engine.manager.get(alias)
            if not p:
                self.after(0, lambda: self._log("❌", f"服务商不存在: {alias}"))
                return
            self.after(0, lambda: self.set_progress(f"测试 {p.display_name}…", 1, 3))
            base = (p.base_url or "").rstrip("/")
            if base.endswith("/v1"):
                upstream = f"{base}/chat/completions"
            else:
                upstream = f"{base}/v1/chat/completions"
            payload = {
                "model": p.model or "gpt-4o-mini",
                "messages": [{"role": "user", "content": "ping"}],
                "stream": False,
            }
            data = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = _req.Request(
                upstream,
                data=data,
                method="POST",
                headers={
                    "Authorization": f"Bearer {p.api_key or ''}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            t0 = _time.time()
            try:
                with _req.urlopen(req, timeout=30) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                    dt = int((_time.time() - t0) * 1000)
                    msg_ok = body[:200].replace("\n", " ")
                    self.after(
                        0,
                        lambda: self._log(
                            "✅",
                            f"服务商 {p.display_name} 可用!  upstream={upstream}  status={resp.status}  耗时={dt}ms  body={msg_ok}",
                        ),
                    )
                    self.after(0, lambda: self.set_progress(f"测试通过 {dt}ms", 3, 3))
                    self.after(
                        0,
                        lambda: FloatingToast(self, f"✅ {p.display_name} 可用 {dt}ms"),
                    )
            except _err.HTTPError as e:
                dt = int((_time.time() - t0) * 1000)
                try:
                    body = e.read().decode("utf-8", errors="replace")[:300]
                except Exception:
                    body = ""
                err_code = getattr(e, "code", "?")
                self.after(
                    0,
                    lambda: self._log(
                        "❌",
                        f"服务商 {p.display_name} 失败 HTTP {err_code} upstream={upstream}  耗时={dt}ms  body={body}",
                    ),
                )
                self.after(0, lambda: self.set_progress(f"HTTP {err_code}", 3, 3))
                self.after(0, lambda: FloatingToast(self, f"❌ HTTP {err_code}", ok=False))
            except Exception as e:
                dt = int((_time.time() - t0) * 1000)
                err_name = type(e).__name__
                err_msg = str(e)[:120]
                self.after(
                    0,
                    lambda: self._log(
                        "❌",
                        f"服务商 {p.display_name} 失败: {err_name}: {err_msg}  upstream={upstream}  耗时={dt}ms",
                    ),
                )
                self.after(0, lambda: self.set_progress(f"测试失败", 3, 3))
                self.after(0, lambda: FloatingToast(self, f"❌ {err_name}", ok=False))

        self._run_async(task)

    # 兼容旧名字
    def _test_provider(self, alias: str) -> None:
        self._test_provider_availability(alias)

    def _switch_in_background(self, alias: str, also_start_proxy: bool = False) -> None:
        # 从自己维护的勾选字典读（不再依赖 CTkCheckBox 或 BooleanVar）
        clis = [k for k, on in (getattr(self, "cli_selected", {}) or {}).items() if bool(on)]
        auto = self.auto_backup.get()
        host, port, _ = self._proxy_url()
        proxy_url = f"http://{host}:{port}/v1"

        def task():
            self.after(0, lambda: self.set_progress(f"切换 {alias} 中…", 5, 10))
            try:
                backup_dir = None
                if auto:
                    try:
                        backup_dir = backup_cli_configs()
                        self.after(0, lambda: self._log("ℹ️", f"快照备份完成: {backup_dir}"))
                    except Exception as e:
                        self.after(0, lambda: self._log("❌", f"自动备份失败: {e}"))

                from core import apply_provider_to_cli, Provider, hermes_merge_config, HERMES_VIRTUAL_MODEL

                provider = self.engine.manager.get(alias)
                if not provider:
                    self.after(0, lambda: self._log_status(False, f"服务商不存在: {alias}"))
                    return
                virtual = (self.virtual_model_var.get() or HERMES_VIRTUAL_MODEL).strip() or HERMES_VIRTUAL_MODEL

                details = []
                use_proxy = bool(also_start_proxy) and bool(proxy_url)
                for i, key in enumerate(clis):
                    try:
                        if key == "hermes":
                            yaml_path, msg = hermes_merge_config(provider, proxy_url, virtual)
                            details.append((key, str(yaml_path), True, msg))
                        elif key == "codex":
                            # Codex 0.116+ 必须显式填 model_provider，走代理时是 openai，
                            # 直连时根据 api_format 动态选 openai/anthropic/google/...
                            for pth, ok, msg in apply_provider_to_cli(
                                provider, key,
                                use_proxy=use_proxy,
                                proxy_base_url=proxy_url or "",
                            ):
                                details.append((key, str(pth), ok, msg))
                        else:
                            for pth, ok, msg in apply_provider_to_cli(provider, key):
                                details.append((key, str(pth), ok, msg))
                    except Exception as e:
                        details.append((key, "", False, f"{e}"))
                    self.after(0, lambda v=i: self.set_progress(f"写入进度 {v+1}/{len(clis)}", v + 1, len(clis)))

                self.engine.manager.set_current(alias)

                if also_start_proxy:
                    def start_proxy():
                        try:
                            state = ProxyState(self.engine.manager)
                            state.set_virtual_model(virtual)
                            state.logger_cb = self._make_logger_cb()
                            httpd = create_proxy_http_server(host, port, state)
                            self._proxy_httpd = httpd
                            self._proxy_host = host
                            self._proxy_port = port
                            self._proxy_state = state
                            import threading
                            self._proxy_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                            self._proxy_thread.start()
                            # 持久化 proxy 设置
                            try:
                                self.engine.manager.set_proxy_settings({
                                    "host": host, "port": port, "virtual_model": virtual,
                                })
                            except Exception:
                                pass
                            self.after(0, self._update_proxy_label)
                            self.after(0, self._update_token_display)
                            self.after(0, lambda: self._log("✅", f"API 网关已启动 {proxy_url}  虚拟模型={virtual}"))
                            FloatingToast(self, f"✅ API 网关已启动 {proxy_url}", ok=True)
                        except Exception as e:
                            self.after(0, lambda: self._log("❌", f"启动 API 网关失败: {e}"))

                    self.after(0, start_proxy)

                def show_result():
                    self.result_text.delete("1.0", tk.END)
                    self.result_text.insert(
                        "1.0",
                        f"服务商: {alias}\n模型: {provider.model or '(未设置)'}\nBaseURL: {provider.base_url or '(未设置)'}\n"
                        f"代理: {proxy_url}\n虚拟模型: {virtual}\n\n",
                    )
                    for cli_key, pth, ok, msg in details:
                        mark = "✅" if ok else "❌"
                        line = f"{mark} [{cli_key}] {msg} ({pth})\n"
                        self.result_text.insert(tk.END, line)
                    self._log_status(True, f"切换完成: {alias}")
                    FloatingToast(self, f"✅ 切换到 {alias}", ok=True)
                    self.refresh_provider_list()

                self.after(0, show_result)
            except Exception as e:
                self.after(0, lambda: self._log_status(False, f"切换失败: {e}"))
            finally:
                self.after(0, lambda: self.set_progress("程序就绪", 0, 10))

        self._run_async(task)

    def on_start_proxy(self) -> None:
        if self._proxy_httpd is not None:
            FloatingToast(self, "ℹ️ API 网关已在运行", ok=True)
            return

        host, port, _ = self._proxy_url()
        virtual = (self.virtual_model_var.get() or HERMES_VIRTUAL_MODEL).strip() or HERMES_VIRTUAL_MODEL

        def task():
            try:
                state = ProxyState(self.engine.manager)
                state.set_virtual_model(virtual)
                state.logger_cb = self._make_logger_cb()
                httpd = create_proxy_http_server(host, port, state)
                self._proxy_httpd = httpd
                self._proxy_host = host
                self._proxy_port = port
                self._proxy_state = state
                import threading
                self._proxy_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
                self._proxy_thread.start()
                # 持久化到 config.yaml
                try:
                    self.engine.manager.set_proxy_settings({
                        "host": host, "port": port, "virtual_model": virtual,
                    })
                except Exception:
                    pass
                self.after(0, self._update_proxy_label)
                self.after(0, self._update_token_display)
                self.after(0, lambda h=host, p=port, v=virtual: self._log("✅", f"API 网关已启动 http://{h}:{p}/v1  虚拟模型={v}"))
                self.after(0, lambda h=host, p=port: FloatingToast(self, f"✅ API 网关已启动 http://{h}:{p}/v1", ok=True))
            except Exception as e:
                self.after(0, lambda: self._log("❌", f"启动 API 网关失败: {e}"))

        # 用主线程执行（无阻塞操作）
        self.after(0, task)

    def on_switch_and_serve(self) -> None:
        alias = self.provider_combo.get().strip()
        if not alias:
            tk.messagebox.showinfo("提示", "请先选择要切换的服务商。", parent=self)
            return
        self._switch_in_background(alias, also_start_proxy=True)

    def on_test_proxy(self) -> None:
        alias = self.provider_combo.get().strip()
        if not alias:
            tk.messagebox.showinfo("提示", "请先选择要切换的服务商。", parent=self)
            return
        host, port, _ = self._proxy_url()
        virtual = (self.virtual_model_var.get() or HERMES_VIRTUAL_MODEL).strip() or HERMES_VIRTUAL_MODEL

        def task():
            from core import create_proxy_http_server, ProxyState
            import threading, time, urllib.request, json as _json

            # 确保先有一个当前服务商
            provider = self.engine.manager.get(alias)
            if not provider:
                self.after(0, lambda: self._log_status(False, f"服务商不存在: {alias}"))
                return
            self.engine.manager.set_current(alias)
            try:
                state = ProxyState(self.engine.manager)
                state.set_virtual_model(virtual)
                state.logger_cb = self._make_logger_cb()
                httpd = create_proxy_http_server(host, port, state)
                # 存引用以便测试结束后可停
                self._proxy_httpd = httpd
                self._proxy_host = host
                self._proxy_port = port
                self._proxy_state = state
                t = threading.Thread(target=httpd.serve_forever, daemon=True)
                self._proxy_thread = t
                t.start()
                # 持久化
                try:
                    self.engine.manager.set_proxy_settings({
                        "host": host, "port": port, "virtual_model": virtual,
                    })
                except Exception:
                    pass
                time.sleep(0.3)
                url = f"http://{host}:{port}/v1/chat/completions"
                payload = {
                    "model": virtual,
                    "messages": [{"role": "user", "content": "请用一句话介绍你自己。"}],
                    "temperature": 0.2,
                    "max_tokens": 120,
                }
                req = urllib.request.Request(
                    url,
                    data=_json.dumps(payload).encode("utf-8"),
                    method="POST",
                    headers={
                        "Authorization": "Bearer test-dummy",
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
                    code = resp.getcode() or 0
                try:
                    parsed = _json.loads(body)
                except Exception:
                    parsed = {"raw": body[:300]}
                choices = parsed.get("choices") or [] if isinstance(parsed, dict) else []
                text = ""
                if choices and isinstance(parsed, dict):
                    msg = (choices[0].get("message") or {}) if isinstance(choices[0], dict) else {}
                    text = (msg or {}).get("content", "") or ""
                self.after(0, lambda: self._log("✅", f"代理冒烟测试通过 code={code}  回复长度={len(text)}"))
                self.after(
                    0,
                    lambda: self.result_text.insert(
                        tk.END,
                        f"\n--- 代理测试 ---\n{url}  code={code}\n回复: {text or str(parsed)[:200]}\n",
                    ),
                )
                self.after(0, lambda c=code: FloatingToast(self, f"✅ 测试通过 code={c}", ok=True))
            except Exception as e:
                self.after(0, lambda e=e: self._log("❌", f"代理测试失败: {e}"))
                self.after(0, lambda e=e: FloatingToast(self, f"❌ 测试失败: {e}", ok=False))
            finally:
                try:
                    httpd.shutdown()
                    httpd.server_close()
                except Exception:
                    pass

        self._run_async(task)

    def on_backup(self) -> None:
        def task():
            self.after(0, lambda: self.set_progress("备份中…", 1, 5))
            try:
                b = backup_cli_configs()
            except Exception as e:
                self.after(0, lambda: self._log_status(False, f"备份失败: {e}"))
                return
            self.after(0, lambda: self._log_status(True, f"已备份到: {b}"))
            self.after(0, lambda: self.set_progress("备份完成", 5, 5))
            FloatingToast(self, "✅ 备份完成", ok=True)

        self._run_async(task)

    def on_rollback(self) -> None:
        backups = list_backups()
        if not backups:
            tk.messagebox.showinfo("提示", "暂无备份快照。", parent=self)
            return
        names = [b.name for b in backups]
        # 简化：直接用 simpledialog 选择
        ans = tk.simpledialog.askstring("选择备份", "请输入备份快照名称：\n" + "\n".join(names), parent=self, initialvalue=names[0])
        if not ans:
            return
        b = get_backup_dir() / ans
        if not b.exists():
            tk.messagebox.showerror("错误", f"找不到备份: {b}", parent=self)
            return

        def task():
            self.after(0, lambda: self.set_progress("回滚中…", 1, 3))
            try:
                res = rollback_from_backup(b)
            except Exception as e:
                self.after(0, lambda: self._log_status(False, f"回滚失败: {e}"))
                return
            self.after(0, lambda: self._log_status(True, f"已恢复: 文件={res['files']}, providers={res['providers']}"))
            self.after(0, self.refresh_provider_list)
            self.after(0, lambda: self.set_progress("回滚完成", 3, 3))
            FloatingToast(self, "✅ 回滚完成", ok=True)

        self._run_async(task)

    def on_import_json(self) -> None:
        f = filedialog.askopenfilename(title="选择 JSON 配置", filetypes=[("JSON", "*.json")])
        if not f:
            return
        merge = tk.messagebox.askyesno("导入模式", "合并到现有服务商？\n是=合并 否=清空后导入", parent=self)
        try:
            n = self.engine.manager.import_json(Path(f), merge=merge)
            self._log_status(True, f"已导入 {n} 个服务商")
            self.refresh_provider_list()
            FloatingToast(self, "✅ 导入完成", ok=True)
        except Exception as e:
            self._log_status(False, f"导入失败: {e}")

    def on_export_json(self) -> None:
        f = filedialog.asksaveasfilename(title="导出服务商配置", defaultextension=".json", initialfile="providers_export.json")
        if not f:
            return
        try:
            n = self.engine.manager.export_json(Path(f))
            self._log_status(True, f"已导出 {n} 个服务商到 {f}")
            FloatingToast(self, "✅ 导出完成", ok=True)
        except Exception as e:
            self._log_status(False, f"导出失败: {e}")

    def on_open_backup_dir(self) -> None:
        p = get_backup_dir()
        try:
            p.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(p))
            else:
                tk.messagebox.showinfo("路径", str(p), parent=self)
        except Exception as e:
            self._log_status(False, f"打开失败: {e}")

    def on_open_config_dir(self) -> None:
        p = get_config_dir()
        try:
            p.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(p))
            else:
                tk.messagebox.showinfo("路径", str(p), parent=self)
        except Exception as e:
            self._log_status(False, f"打开失败: {e}")

    # ----------------------- 帮助/关于/设置 -----------------------
    def on_help(self) -> None:
        txt = (
            "CC-Switch Python 版 使用说明\n\n"
            "🚀 核心功能：\n"
            "  • 管理多个 AI 服务商配置（OpenAI、Anthropic、DeepSeek、中转等）\n"
            "  • 一键把选中配置写入 Claude Code / Codex CLI / Gemini CLI\n"
            "  • 切换前自动快照备份，可随时回滚\n"
            "  • 支持 JSON 导入/导出\n\n"
            "📋 操作步骤：\n"
            "  1. 左侧【➕新增】录入服务商的 API Key / BaseURL / 模型\n"
            "  2. 中间下拉选择目标服务商\n"
            "  3. 勾选需要写入的 CLI 工具\n"
            "  4. 点击【🚀 一键切换】即可\n\n"
            "ℹ️ 目录说明：\n"
            "  • 程序目录下 config/providers.json 保存服务商列表\n"
            "  • 程序目录下 config_backups/ 存放自动备份\n\n"
            "⚙️ 右键菜单：\n"
            "  在空白区域右键可进行字体/颜色/皮肤的设置\n"
        )
        tk.messagebox.showinfo("ℹ️ 帮助", txt, parent=self)

    def on_about(self) -> None:
        tk.messagebox.showinfo(
            "📋 关于",
            "CC-Switch Python 版\n版本: 1.0.0\n作者: CC-Switch Python Team\n\n技术栈: Python 3.10+ + CustomTkinter\n许可: MIT",
            parent=self,
        )

    def on_toggle_lang(self) -> None:
        cur = self.lang.get()
        if cur == "中文":
            self.lang.set("En")
            FloatingToast(self, "Language: English (UI text kept Chinese)", ok=True)
        else:
            self.lang.set("中文")
            FloatingToast(self, "语言: 中文", ok=True)

    def on_cycle_skin(self) -> None:
        idx = int(self.settings.get("skin_index", DEFAULT_SKIN))
        idx = (idx + 1) % len(SKINS)
        self.settings["skin_index"] = idx
        self._apply_skin_from_index(idx)
        self._apply_skin_to_widgets()
        self._save_settings()
        self.refresh_provider_list()
        self._log("ℹ️", f"已切换皮肤: {SKINS[idx][0]}")

    def on_full_settings(self) -> None:
        dlg = SettingsDialog(self, self.settings)
        self.wait_window(dlg)
        if dlg.result is not None:
            self.settings = dlg.result
            self._apply_skin_from_index(int(self.settings.get("skin_index", DEFAULT_SKIN)))
            self._apply_skin_to_widgets()
            self._save_settings()
            FloatingToast(self, "✅ 设置已保存", ok=True)
            self.refresh_provider_list()

    def on_font_settings(self) -> None:
        self.on_full_settings()

    def on_style_settings(self) -> None:
        self.on_full_settings()

    def on_size_settings(self) -> None:
        self.on_full_settings()


def run_gui() -> None:
    """启动主窗口。"""
    app = App()
    # 首次应用皮肤到所有子组件
    app._apply_skin_to_widgets()
    app.mainloop()
