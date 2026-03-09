#!/usr/bin/env python3
"""Linux Galgame Launcher (XP3)

改进重点：
- 保留原有功能（扫描、下载、校验、预览、多选启动、配置持久化等）。
- 新增可选背景图（Pillow 可用时支持 PNG/JPG/GIF 缩放；不可用时回退纯色背景）。
- 使用 Canvas 作为底层容器，背景随窗口尺寸变化自适应。
- 界面样式优化（配色、间距、按钮悬停、标题区）。
"""

from __future__ import annotations

import hashlib
import json
import shlex
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable
from tkinter import (
    BOTH,
    END,
    EXTENDED,
    LEFT,
    RIGHT,
    Button,
    Canvas,
    Entry,
    Frame,
    Label,
    Listbox,
    StringVar,
    Tk,
    filedialog,
    messagebox,
)

try:
    from PIL import Image, ImageTk

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

APP_NAME = "linux-galgame"
CONFIG_PATH = Path.home() / ".config" / APP_NAME / "config.json"
RUNTIME_DIR = Path.home() / ".local" / "share" / APP_NAME / "runtime"
DEFAULT_RUNTIME_PATH = RUNTIME_DIR / "krkrsdl2"

DEFAULT_CONFIG = {
    "games_dir": str(Path.home() / "Games" / "galgame"),
    "runtime_path": str(DEFAULT_RUNTIME_PATH),
    "launch_template": "{runtime} {xp3}",
    "runtime_download_url": "https://github.com/krkrsdl2/krkrsdl2/releases/latest/download/krkrsdl2-linux-x86_64",
    "runtime_sha256": "",
    "window_geometry": "980x680",
    "background_image": "",
}

HELP_TEXT = (
    "使用说明：\n"
    "1) 设置“游戏根目录”，点击“刷新列表”扫描 .xp3。\n"
    "2) 设置“运行时路径”（可手动选择，或点击“下载运行时”）。\n"
    "3) 列表支持多选（Ctrl/Shift），可一次启动多个游戏。\n"
    "4) 可使用“预览命令”检查模板展开结果。\n"
    "5) 背景图功能需要 Pillow 才能支持 PNG/JPG 等格式。\n\n"
    "模板变量：{xp3} {game_dir} {game_name} {runtime}"
)


def ensure_config() -> dict:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
        return DEFAULT_CONFIG.copy()

    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        cfg = {}

    merged = DEFAULT_CONFIG.copy()
    merged.update(cfg)
    return merged


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_command(template: str, xp3_path: Path, runtime_path: Path) -> tuple[list[str], Path]:
    game_dir = xp3_path.parent
    expanded = template.format(
        xp3=shlex.quote(str(xp3_path)),
        game_dir=shlex.quote(str(game_dir)),
        game_name=shlex.quote(xp3_path.stem),
        runtime=shlex.quote(str(runtime_path)),
    )
    return shlex.split(expanded), game_dir


class LauncherApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("Linux Galgame 启动器（XP3）")

        self.cfg = ensure_config()
        self.root.geometry(self.cfg.get("window_geometry") or DEFAULT_CONFIG["window_geometry"])

        self.games_dir_var = StringVar(value=self.cfg["games_dir"])
        self.runtime_path_var = StringVar(value=self.cfg["runtime_path"])
        self.template_var = StringVar(value=self.cfg["launch_template"])
        self.runtime_url_var = StringVar(value=self.cfg["runtime_download_url"])
        self.runtime_sha_var = StringVar(value=self.cfg.get("runtime_sha256", ""))
        self.bg_path_var = StringVar(value=self.cfg.get("background_image", ""))
        self.status_var = StringVar(value="就绪")

        self.xp3_files: list[Path] = []
        self.download_thread: threading.Thread | None = None
        self._pending_launch_after_download: list[Path] = []

        self._bg_original = None
        self._bg_tk = None
        self._bg_canvas_item = None
        self._bg_resize_job = None

        self._init_style()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        if self.bg_path_var.get().strip():
            self._load_background(Path(self.bg_path_var.get().strip()).expanduser(), silent=True)
        self.refresh_games()

    def _init_style(self) -> None:
        self.colors = {
            "bg": "#1c2434",
            "panel": "#f4f6fb",
            "primary": "#5b7cfa",
            "primary_hover": "#7994ff",
            "secondary": "#63708a",
            "secondary_hover": "#7a88a4",
            "text_dark": "#1d2433",
        }
        self.font_normal = ("Noto Sans CJK SC", 11)
        self.font_title = ("Noto Sans CJK SC", 20, "bold")
        self.root.configure(bg=self.colors["bg"])

    def _mk_button(self, parent: Frame, text: str, command: Callable[[], None], primary: bool = False) -> Button:
        bg = self.colors["primary"] if primary else self.colors["secondary"]
        hover = self.colors["primary_hover"] if primary else self.colors["secondary_hover"]
        btn = Button(
            parent,
            text=text,
            command=command,
            bg=bg,
            fg="white",
            activebackground=hover,
            activeforeground="white",
            relief="flat",
            bd=0,
            padx=12,
            pady=6,
            font=self.font_normal,
            cursor="hand2",
        )
        btn.bind("<Enter>", lambda _e: btn.configure(bg=hover))
        btn.bind("<Leave>", lambda _e: btn.configure(bg=bg))
        return btn

    def _build_ui(self) -> None:
        self.canvas = Canvas(self.root, highlightthickness=0, bg=self.colors["bg"])
        self.canvas.pack(fill=BOTH, expand=True)

        self.main_panel = Frame(self.canvas, bg=self.colors["panel"])
        self.canvas_window = self.canvas.create_window(20, 20, anchor="nw", window=self.main_panel)

        title_bar = Frame(self.main_panel, bg="#202b41")
        title_bar.pack(fill=BOTH)
        Label(
            title_bar,
            text="Linux Galgame Launcher",
            bg="#202b41",
            fg="#dfe8ff",
            font=self.font_title,
            pady=14,
        ).pack(anchor="center")

        top = Frame(self.main_panel, bg=self.colors["panel"])
        top.pack(fill=BOTH, padx=16, pady=14, expand=True)

        self._labeled_entry(top, "游戏根目录", self.games_dir_var, self.choose_games_dir)
        self._labeled_entry(top, "运行时路径（可执行文件）", self.runtime_path_var, self.choose_runtime)

        Label(top, text="运行时下载 URL", bg=self.colors["panel"], fg=self.colors["text_dark"], font=self.font_normal).pack(anchor="w", pady=(10, 2))
        Entry(top, textvariable=self.runtime_url_var, font=self.font_normal, relief="groove", bd=1).pack(fill=BOTH)

        Label(top, text="运行时 SHA256（可选）", bg=self.colors["panel"], fg=self.colors["text_dark"], font=self.font_normal).pack(anchor="w", pady=(10, 2))
        Entry(top, textvariable=self.runtime_sha_var, font=self.font_normal, relief="groove", bd=1).pack(fill=BOTH)

        Label(top, text="启动命令模板（变量: {xp3} {game_dir} {game_name} {runtime}）", bg=self.colors["panel"], fg=self.colors["text_dark"], font=self.font_normal).pack(anchor="w", pady=(10, 2))
        Entry(top, textvariable=self.template_var, font=self.font_normal, relief="groove", bd=1).pack(fill=BOTH)

        bg_row = Frame(top, bg=self.colors["panel"])
        bg_row.pack(fill=BOTH, pady=(10, 2))
        Label(bg_row, text="背景图片", bg=self.colors["panel"], fg=self.colors["text_dark"], font=self.font_normal).pack(side=LEFT)
        Label(bg_row, textvariable=self.bg_path_var, bg=self.colors["panel"], fg="#4a5870", font=("Noto Sans CJK SC", 9)).pack(side=LEFT, padx=8)

        controls = Frame(top, bg=self.colors["panel"])
        controls.pack(fill=BOTH, pady=(10, 8))
        self._mk_button(controls, "刷新列表", self.refresh_games).pack(side=LEFT)
        self._mk_button(controls, "保存配置", self.save_settings).pack(side=LEFT, padx=6)
        self._mk_button(controls, "下载运行时", self.download_runtime_with_confirm).pack(side=LEFT, padx=6)
        self._mk_button(controls, "校验运行时", self.verify_runtime).pack(side=LEFT, padx=6)
        self._mk_button(controls, "预览命令", self.preview_command).pack(side=LEFT, padx=6)
        self._mk_button(controls, "选择背景图片", self.choose_background_image).pack(side=LEFT, padx=6)
        self._mk_button(controls, "清除背景", self.clear_background).pack(side=LEFT, padx=6)
        self._mk_button(controls, "帮助", self.show_help).pack(side=LEFT, padx=6)

        self.games_list = Listbox(
            top,
            selectmode=EXTENDED,
            font=("Noto Sans CJK SC", 11),
            bg="#fbfcff",
            fg="#1e2738",
            selectbackground="#8aa2ff",
            relief="flat",
            bd=1,
        )
        self.games_list.pack(fill=BOTH, expand=True)

        bottom = Frame(self.main_panel, bg="#e8edf7")
        bottom.pack(fill=BOTH, padx=16, pady=(0, 16))
        self._mk_button(bottom, "启动选中游戏", self.launch_selected, primary=True).pack(side=LEFT, pady=8)
        Label(bottom, text="状态：", bg="#e8edf7", fg="#2f3c55", font=self.font_normal).pack(side=LEFT, padx=(12, 0))
        Label(bottom, textvariable=self.status_var, bg="#e8edf7", fg="#2f3c55", font=self.font_normal).pack(side=LEFT)

        self.root.bind("<Configure>", self._on_root_resize)

    def _labeled_entry(self, parent: Frame, label: str, var: StringVar, browse_cmd: Callable[[], None]) -> None:
        Label(parent, text=label, bg=self.colors["panel"], fg=self.colors["text_dark"], font=self.font_normal).pack(anchor="w", pady=(10, 2))
        row = Frame(parent, bg=self.colors["panel"])
        row.pack(fill=BOTH)
        Entry(row, textvariable=var, font=self.font_normal, relief="groove", bd=1).pack(side=LEFT, fill=BOTH, expand=True)
        self._mk_button(row, "浏览", browse_cmd).pack(side=RIGHT, padx=(8, 0))

    def _on_root_resize(self, _event=None) -> None:
        width = max(self.root.winfo_width() - 40, 600)
        height = max(self.root.winfo_height() - 40, 400)
        self.canvas.coords(self.canvas_window, 20, 20)
        self.canvas.itemconfigure(self.canvas_window, width=width, height=height)

        if self._bg_original is not None:
            if self._bg_resize_job:
                self.root.after_cancel(self._bg_resize_job)
            self._bg_resize_job = self.root.after(80, self._redraw_background)

    def _redraw_background(self) -> None:
        self._bg_resize_job = None
        if self._bg_original is None or not PIL_AVAILABLE:
            return

        w = max(self.root.winfo_width(), 1)
        h = max(self.root.winfo_height(), 1)
        resized = self._bg_original.resize((w, h), resample=Image.LANCZOS)
        self._bg_tk = ImageTk.PhotoImage(resized)

        if self._bg_canvas_item is None:
            self._bg_canvas_item = self.canvas.create_image(0, 0, anchor="nw", image=self._bg_tk)
            self.canvas.tag_lower(self._bg_canvas_item)
        else:
            self.canvas.itemconfigure(self._bg_canvas_item, image=self._bg_tk)

    def _load_background(self, path: Path, silent: bool = False) -> None:
        if not PIL_AVAILABLE:
            if not silent:
                messagebox.showwarning("缺少 Pillow", "未安装 Pillow，无法加载 PNG/JPG 背景。\n可执行: python -m pip install Pillow")
            return

        try:
            img = Image.open(path)
            self._bg_original = img.convert("RGB")
            self.bg_path_var.set(str(path))
            self._redraw_background()
            self.set_status(f"已加载背景：{path}")
        except OSError as exc:
            if not silent:
                messagebox.showerror("背景加载失败", f"无法加载图片：{exc}")

    def choose_background_image(self) -> None:
        filetypes = [
            ("Image files", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
            ("All files", "*.*"),
        ]
        selected = filedialog.askopenfilename(title="选择背景图片", filetypes=filetypes)
        if selected:
            self._load_background(Path(selected).expanduser())

    def clear_background(self) -> None:
        self._bg_original = None
        self._bg_tk = None
        if self._bg_canvas_item is not None:
            self.canvas.delete(self._bg_canvas_item)
            self._bg_canvas_item = None
        self.bg_path_var.set("")
        self.set_status("已清除背景")

    def set_status(self, msg: str) -> None:
        self.status_var.set(msg)
        self.root.update_idletasks()

    def show_help(self) -> None:
        messagebox.showinfo("帮助", HELP_TEXT)

    def choose_games_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.games_dir_var.get() or str(Path.home()))
        if selected:
            self.games_dir_var.set(selected)
            self.set_status(f"已选择游戏目录：{selected}")

    def choose_runtime(self) -> None:
        default = Path(self.runtime_path_var.get()).expanduser()
        initialdir = str(default.parent if default.parent.exists() else Path.home())
        selected = filedialog.askopenfilename(initialdir=initialdir)
        if selected:
            self.runtime_path_var.set(selected)
            self.set_status(f"已选择运行时：{selected}")

    def save_settings(self) -> None:
        self.cfg["games_dir"] = self.games_dir_var.get().strip()
        self.cfg["runtime_path"] = self.runtime_path_var.get().strip()
        self.cfg["launch_template"] = self.template_var.get().strip() or DEFAULT_CONFIG["launch_template"]
        self.cfg["runtime_download_url"] = self.runtime_url_var.get().strip()
        self.cfg["runtime_sha256"] = self.runtime_sha_var.get().strip()
        self.cfg["window_geometry"] = self.root.geometry()
        self.cfg["background_image"] = self.bg_path_var.get().strip()
        save_config(self.cfg)
        self.set_status(f"配置已保存：{CONFIG_PATH}")
        messagebox.showinfo("保存成功", f"配置已写入：{CONFIG_PATH}")

    def refresh_games(self) -> None:
        self.games_list.delete(0, END)
        self.games_list.insert(END, "扫描中，请稍候...")
        self.set_status("扫描中...")
        games_dir = Path(self.games_dir_var.get()).expanduser()

        def on_done() -> None:
            self.games_list.delete(0, END)
            if not self.xp3_files:
                self.games_list.insert(END, "(未找到 .xp3 文件)")
                self.set_status("扫描完成：未找到 .xp3")
            else:
                for item in self.xp3_files:
                    self.games_list.insert(END, str(item))
                self.set_status(f"扫描完成：找到 {len(self.xp3_files)} 个 .xp3")

        def worker() -> None:
            found: list[Path] = []
            if games_dir.exists():
                counter = 0
                for p in games_dir.rglob("*"):
                    if p.is_file() and p.suffix.lower() == ".xp3":
                        found.append(p)
                        counter += 1
                        if counter % 100 == 0:
                            self.root.after(0, self.set_status, f"扫描中... 已发现 {counter} 个 .xp3")
            found.sort(key=lambda x: str(x).lower())
            self.xp3_files = found
            self.root.after(0, on_done)

        threading.Thread(target=worker, daemon=True).start()

    def runtime_path(self) -> Path:
        return Path(self.runtime_path_var.get().strip()).expanduser()

    def _select_download_target(self) -> Path:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        chosen = filedialog.asksaveasfilename(
            title="选择运行时保存位置（取消则使用默认路径）",
            initialdir=str(RUNTIME_DIR),
            initialfile=self.runtime_path().name or DEFAULT_RUNTIME_PATH.name,
        )
        return Path(chosen).expanduser() if chosen else DEFAULT_RUNTIME_PATH

    def download_runtime_with_confirm(self) -> None:
        self._start_runtime_download()

    def _start_runtime_download(self, after_download: Callable[[], None] | None = None) -> None:
        if self.download_thread and self.download_thread.is_alive():
            messagebox.showinfo("下载中", "已有下载任务正在进行，请稍候。")
            return

        url = self.runtime_url_var.get().strip()
        if not url:
            messagebox.showwarning("缺少 URL", "请先填写运行时下载 URL。")
            return

        target = self._select_download_target()
        yes = messagebox.askyesno("确认下载", f"将下载运行时到：{target}\n来源：{url}\n\n请确认来源可信，是否继续？")
        if not yes:
            self.set_status("已取消下载")
            return

        target.parent.mkdir(parents=True, exist_ok=True)
        self.set_status("开始下载运行时...")

        def on_success() -> None:
            self.runtime_path_var.set(str(target))
            self.set_status(f"运行时下载完成：{target}")
            messagebox.showinfo("下载成功", f"运行时已准备好：\n{target}")
            if after_download:
                after_download()

        def on_fail(msg: str) -> None:
            messagebox.showerror("下载失败", msg)
            self.set_status("下载失败")

        def worker() -> None:
            try:
                with urllib.request.urlopen(url, timeout=30) as resp, target.open("wb") as out:
                    total = int(resp.headers.get("Content-Length", "0") or "0")
                    downloaded = 0
                    last_percent = -1
                    last_kib = -1
                    while True:
                        block = resp.read(1024 * 128)
                        if not block:
                            break
                        out.write(block)
                        downloaded += len(block)
                        if total > 0:
                            percent = int(downloaded * 100 / total)
                            if percent >= last_percent + 1:
                                last_percent = percent
                                self.root.after(0, self.set_status, f"下载中... {percent}%")
                        else:
                            kib = downloaded // 1024
                            if kib >= last_kib + 100:
                                last_kib = kib
                                self.root.after(0, self.set_status, f"下载中... {kib} KiB")

                target.chmod(0o755)
                expected = self.runtime_sha_var.get().strip().lower()
                if expected:
                    actual = sha256_of_file(target).lower()
                    if actual != expected:
                        target.unlink(missing_ok=True)
                        raise ValueError(
                            "SHA256 校验失败。\n"
                            f"期望: {expected}\n实际: {actual}\n"
                            "已删除下载文件，请检查下载源或校验值。"
                        )
                self.root.after(0, on_success)
            except urllib.error.URLError as exc:
                self.root.after(0, on_fail, f"网络错误：{exc}")
            except OSError as exc:
                self.root.after(0, on_fail, f"文件错误：{exc}")
            except ValueError as exc:
                self.root.after(0, on_fail, str(exc))
            finally:
                self.download_thread = None

        self.download_thread = threading.Thread(target=worker, daemon=False)
        self.download_thread.start()

    def verify_runtime(self) -> None:
        runtime = self.runtime_path()
        if not runtime.exists():
            messagebox.showwarning("校验失败", "运行时文件不存在。")
            self.set_status("校验失败：运行时不存在")
            return

        expected = self.runtime_sha_var.get().strip().lower()
        if not expected:
            messagebox.showinfo("提示", "未填写 SHA256 校验值，无法校验。如需校验，请先填写期望值。")
            self.set_status("未执行校验：缺少 SHA256")
            return

        self.set_status("校验中...")
        actual = sha256_of_file(runtime).lower()
        if actual == expected:
            messagebox.showinfo("校验通过", "SHA256 校验通过。")
            self.set_status("校验通过")
        else:
            messagebox.showerror("校验失败", f"SHA256 不匹配\n期望: {expected}\n实际: {actual}")
            self.set_status("校验失败：SHA256 不匹配")

    def _selected_xp3_paths(self) -> list[Path]:
        return [self.xp3_files[i] for i in self.games_list.curselection() if 0 <= i < len(self.xp3_files)]

    def preview_command(self) -> None:
        selected = self._selected_xp3_paths()
        if not selected:
            messagebox.showwarning("未选择", "请先在列表中选择一个 XP3 文件。")
            return

        try:
            args, _ = build_command(self.template_var.get(), selected[0], self.runtime_path())
            messagebox.showinfo("命令预览（首个选中项）", " ".join(shlex.quote(a) for a in args))
        except (KeyError, ValueError) as exc:
            messagebox.showerror("模板错误", str(exc))

    def _launch_paths(self, targets: list[Path]) -> None:
        runtime = self.runtime_path()
        ok = 0
        for xp3 in targets:
            try:
                args, workdir = build_command(self.template_var.get(), xp3, runtime)
                subprocess.Popen(args, cwd=workdir, start_new_session=True)
                ok += 1
            except FileNotFoundError as exc:
                messagebox.showerror("启动失败", f"命令不存在：{exc}")
            except KeyError as exc:
                messagebox.showerror("启动失败", f"模板变量错误：{exc}")
            except OSError as exc:
                messagebox.showerror("启动失败", f"系统错误：{exc}")
        if ok:
            self.set_status(f"已启动 {ok} 个游戏")

    def launch_selected(self) -> None:
        if not self.xp3_files:
            messagebox.showwarning("无法启动", "列表中没有可启动的 XP3 文件。")
            self.set_status("启动失败：无可用 XP3")
            return

        targets = self._selected_xp3_paths()
        if not targets:
            messagebox.showwarning("未选择", "请先在列表中选择至少一个 XP3 文件。")
            self.set_status("启动失败：未选择游戏")
            return

        if not self.runtime_path().exists():
            yes = messagebox.askyesno("运行时不存在", "运行时不存在，是否立即下载？下载成功后将自动继续启动。")
            if yes:
                self._pending_launch_after_download = targets
                self._start_runtime_download(after_download=lambda: self._launch_paths(self._pending_launch_after_download))
            else:
                self.set_status("启动失败：运行时不存在")
            return

        self._launch_paths(targets)

    def on_close(self) -> None:
        self.cfg["window_geometry"] = self.root.geometry()
        self.cfg["games_dir"] = self.games_dir_var.get().strip()
        self.cfg["runtime_path"] = self.runtime_path_var.get().strip()
        self.cfg["launch_template"] = self.template_var.get().strip() or DEFAULT_CONFIG["launch_template"]
        self.cfg["runtime_download_url"] = self.runtime_url_var.get().strip()
        self.cfg["runtime_sha256"] = self.runtime_sha_var.get().strip()
        self.cfg["background_image"] = self.bg_path_var.get().strip()
        save_config(self.cfg)

        if self.download_thread and self.download_thread.is_alive():
            force = messagebox.askyesno("下载进行中", "当前仍在下载运行时。是否强制退出？")
            if not force:
                self.set_status("已取消退出，等待下载完成")
                return

        self.root.destroy()


def main() -> None:
    root = Tk()
    LauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
