#!/usr/bin/env python3
"""Linux Galgame Launcher (XP3)

新增改进（主要部分）：
1) 日志系统（标准库 logging + QueueHandler + RotatingFileHandler）：
   - 日志文件：~/.local/share/linux-galgame/launcher.log
   - 包含时间戳/级别/消息，支持滚动，避免无限增长。
   - 记录启动、配置、扫描、下载、校验、预览、启动、异常堆栈等关键事件。

2) 更详细帮助窗口：
   - 使用可滚动 Text 展示分节说明与 FAQ。

3) ttk 美化 + 可选 sv_ttk 主题：
   - 若安装 sv_ttk（pip install sv-ttk）则启用现代主题；缺失时自动回退。

可选依赖：
- Pillow（背景图缩放支持）：pip install Pillow
- sv_ttk（更现代 ttk 主题）：pip install sv-ttk
"""

from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import queue
import shlex
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

# 背景图可选依赖
try:
    from PIL import Image, ImageTk

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# 主题可选依赖
try:
    import sv_ttk

    SV_TTK_AVAILABLE = True
except ImportError:
    SV_TTK_AVAILABLE = False

APP_NAME = "linux-galgame"
CONFIG_PATH = Path.home() / ".config" / APP_NAME / "config.json"
DATA_DIR = Path.home() / ".local" / "share" / APP_NAME
RUNTIME_DIR = DATA_DIR / "runtime"
LOG_PATH = DATA_DIR / "launcher.log"
DEFAULT_RUNTIME_PATH = RUNTIME_DIR / "krkrsdl2"

DEFAULT_CONFIG = {
    "games_dir": str(Path.home() / "Games" / "galgame"),
    "runtime_path": str(DEFAULT_RUNTIME_PATH),
    "launch_template": "{runtime} {xp3}",
    "runtime_download_url": "https://github.com/krkrsdl2/krkrsdl2/releases/latest/download/krkrsdl2-linux-x86_64",
    "runtime_sha256": "",
    "window_geometry": "1100x760",
    "background_image": "",
}

HELP_CONTENT = """Linux Galgame Launcher 使用指南
===============================

1. 运行时获取（重点）
--------------------
A) 自动下载（推荐）
   - 填写“运行时下载 URL”（建议官方可信链接）
   - 可选填写 SHA256
   - 点击“下载运行时”并确认来源

B) 手动放置
   - 把运行时可执行文件放到任意目录
   - 点击“运行时路径 -> 浏览”选择它
   - 保存配置

2. 模板变量说明
--------------
默认模板：{runtime} {xp3}

可用变量：
- {xp3}: 当前选中的 xp3 完整路径
- {game_dir}: xp3 所在目录
- {game_name}: xp3 文件名（不含后缀）
- {runtime}: 运行时可执行文件路径

示例：
- {runtime} {xp3}
- {runtime} -config ./config.tjs {xp3}

3. 多选启动
----------
- 在游戏列表中按住 Ctrl/Shift 可多选
- 点击“启动选中游戏”批量启动

4. 常见问题
----------
Q1: 下载失败怎么办？
A1: 检查 URL、网络、代理；查看“查看日志”定位具体错误。

Q2: 提示权限不足怎么办？
A2: 检查目标目录写权限；运行时是否可执行（chmod +x）。

Q3: 点击启动无反应？
A3: 先用“预览命令”确认命令；手动在终端执行该命令测试。

Q4: 背景图不生效？
A4: 需要 Pillow 支持 PNG/JPG/GIF 缩放（pip install Pillow）。

5. 日志排查
----------
- 点击“查看日志”可实时查看 launcher.log
- 默认日志路径：~/.local/share/linux-galgame/launcher.log
- 包含扫描、下载、校验、启动、异常堆栈等信息
"""


def setup_logging() -> tuple[logging.Logger, logging.handlers.QueueListener]:
    """配置异步日志：避免耗时任务日志写盘阻塞 GUI。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    log_queue: queue.Queue[logging.LogRecord] = queue.Queue()
    queue_handler = logging.handlers.QueueHandler(log_queue)

    rotating = logging.handlers.RotatingFileHandler(
        LOG_PATH,
        maxBytes=1_000_000,
        backupCount=2,
        encoding="utf-8",
    )
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    rotating.setFormatter(formatter)

    listener = logging.handlers.QueueListener(log_queue, rotating)
    listener.start()

    logger = logging.getLogger(APP_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.addHandler(queue_handler)
    logger.propagate = False

    logger.info("=== Launcher process started ===")
    return logger, listener


LOGGER, LOG_LISTENER = setup_logging()


def ensure_config() -> dict:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, ensure_ascii=False, indent=2), encoding="utf-8")
        LOGGER.info("Config not found, created default config: %s", CONFIG_PATH)
        return DEFAULT_CONFIG.copy()

    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        LOGGER.info("Config loaded: %s", CONFIG_PATH)
    except json.JSONDecodeError:
        LOGGER.warning("Config JSON decode failed, fallback to defaults: %s", CONFIG_PATH)
        cfg = {}

    merged = DEFAULT_CONFIG.copy()
    merged.update(cfg)
    return merged


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    LOGGER.info("Config saved: %s", CONFIG_PATH)


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
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Linux Galgame 启动器（XP3）")

        self.cfg = ensure_config()
        self.root.geometry(self.cfg.get("window_geometry") or DEFAULT_CONFIG["window_geometry"])

        self.games_dir_var = tk.StringVar(value=self.cfg["games_dir"])
        self.runtime_path_var = tk.StringVar(value=self.cfg["runtime_path"])
        self.template_var = tk.StringVar(value=self.cfg["launch_template"])
        self.runtime_url_var = tk.StringVar(value=self.cfg["runtime_download_url"])
        self.runtime_sha_var = tk.StringVar(value=self.cfg.get("runtime_sha256", ""))
        self.bg_path_var = tk.StringVar(value=self.cfg.get("background_image", ""))
        self.status_var = tk.StringVar(value="就绪")

        self.xp3_files: list[Path] = []
        self.download_thread: threading.Thread | None = None
        self._pending_launch_after_download: list[Path] = []

        # 背景图状态
        self._bg_original = None
        self._bg_tk = None
        self._bg_canvas_item = None
        self._bg_resize_job = None

        # 日志窗口状态
        self.log_window: tk.Toplevel | None = None
        self.log_text: ScrolledText | None = None
        self.log_auto_refresh_var = tk.BooleanVar(value=True)
        self.log_refresh_job = None

        self._init_style()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        if self.bg_path_var.get().strip():
            self._load_background(Path(self.bg_path_var.get().strip()).expanduser(), silent=True)

        self.refresh_games()

    # =========================
    # UI / Theme
    # =========================
    def _init_style(self) -> None:
        style = ttk.Style(self.root)

        if SV_TTK_AVAILABLE:
            try:
                sv_ttk.set_theme("dark")
                LOGGER.info("sv_ttk theme enabled")
            except Exception:
                LOGGER.exception("sv_ttk apply failed, fallback to default ttk theme")
        else:
            LOGGER.warning("sv_ttk not installed, using default ttk theme")

        default_font = ("Noto Sans CJK SC", 11)
        self.root.option_add("*Font", default_font)

        style.configure("Card.TLabelframe", padding=8)
        style.configure("Card.TLabelframe.Label", font=("Noto Sans CJK SC", 11, "bold"))
        style.configure("Title.TLabel", font=("Noto Sans CJK SC", 22, "bold"))
        style.configure("Status.TLabel", padding=8)
        style.configure("Wide.TButton", padding=(10, 6))

    def _build_ui(self) -> None:
        # Canvas 作为背景层容器（满足背景图缩放需求）
        self.canvas = tk.Canvas(self.root, highlightthickness=0, bg="#1f2430")
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.main_panel = ttk.Frame(self.canvas, padding=14)
        self.canvas_window = self.canvas.create_window(16, 16, anchor="nw", window=self.main_panel)

        title_bar = ttk.Frame(self.main_panel)
        title_bar.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(title_bar, text="Linux Galgame Launcher", style="Title.TLabel").pack(side=tk.LEFT)

        if not SV_TTK_AVAILABLE:
            ttk.Label(title_bar, text="(提示: 可安装 sv-ttk 获得更现代主题)").pack(side=tk.LEFT, padx=10)

        # 目录设置
        path_group = ttk.LabelFrame(self.main_panel, text="路径与运行时", style="Card.TLabelframe")
        path_group.pack(fill=tk.X, pady=(0, 10))

        self._row_with_entry(path_group, "游戏根目录", self.games_dir_var, self.choose_games_dir)
        self._row_with_entry(path_group, "运行时路径", self.runtime_path_var, self.choose_runtime)

        ttk.Label(path_group, text="运行时下载 URL").pack(anchor="w", padx=8, pady=(8, 2))
        ttk.Entry(path_group, textvariable=self.runtime_url_var).pack(fill=tk.X, padx=8)

        ttk.Label(path_group, text="运行时 SHA256（可选）").pack(anchor="w", padx=8, pady=(8, 2))
        ttk.Entry(path_group, textvariable=self.runtime_sha_var).pack(fill=tk.X, padx=8)

        ttk.Label(path_group, text="启动模板（变量: {xp3} {game_dir} {game_name} {runtime}）").pack(anchor="w", padx=8, pady=(8, 2))
        ttk.Entry(path_group, textvariable=self.template_var).pack(fill=tk.X, padx=8)

        # 背景设置
        bg_group = ttk.LabelFrame(self.main_panel, text="背景图片", style="Card.TLabelframe")
        bg_group.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(bg_group, textvariable=self.bg_path_var).pack(side=tk.LEFT, padx=8)
        ttk.Button(bg_group, text="选择背景图片", style="Wide.TButton", command=self.choose_background_image, width=14).pack(side=tk.RIGHT, padx=4)
        ttk.Button(bg_group, text="清除背景", style="Wide.TButton", command=self.clear_background, width=12).pack(side=tk.RIGHT, padx=4)

        # 操作按钮
        action_group = ttk.LabelFrame(self.main_panel, text="操作", style="Card.TLabelframe")
        action_group.pack(fill=tk.X, pady=(0, 10))

        actions = [
            ("刷新列表", self.refresh_games),
            ("保存配置", self.save_settings),
            ("下载运行时", self.download_runtime_with_confirm),
            ("校验运行时", self.verify_runtime),
            ("预览命令", self.preview_command),
            ("查看日志", self.open_log_window),
            ("帮助", self.open_help_window),
        ]
        for i, (txt, cmd) in enumerate(actions):
            ttk.Button(action_group, text=txt, style="Wide.TButton", command=cmd, width=12).grid(row=0, column=i, padx=4, pady=6)

        # 游戏列表
        list_group = ttk.LabelFrame(self.main_panel, text="XP3 游戏列表（支持多选）", style="Card.TLabelframe")
        list_group.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        list_container = ttk.Frame(list_group)
        list_container.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.games_list = tk.Listbox(
            list_container,
            selectmode=tk.EXTENDED,
            height=12,
            relief="flat",
            highlightthickness=1,
            highlightbackground="#8a8a8a",
            selectbackground="#6289ff",
        )
        self.games_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        ybar = ttk.Scrollbar(list_container, orient=tk.VERTICAL, command=self.games_list.yview)
        ybar.pack(side=tk.RIGHT, fill=tk.Y)
        self.games_list.configure(yscrollcommand=ybar.set)

        # 底栏
        status_bar = ttk.Frame(self.main_panel)
        status_bar.pack(fill=tk.X)

        ttk.Button(status_bar, text="启动选中游戏", style="Wide.TButton", command=self.launch_selected, width=14).pack(side=tk.LEFT)

        status_card = ttk.Label(status_bar, textvariable=self.status_var, style="Status.TLabel", relief=tk.GROOVE)
        status_card.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))

        self.root.bind("<Configure>", self._on_root_resize)

    def _row_with_entry(self, parent: ttk.LabelFrame, label: str, var: tk.StringVar, browse_cmd: Callable[[], None]) -> None:
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, padx=8, pady=(8, 2))
        ttk.Label(row, text=label, width=14).pack(side=tk.LEFT)
        ttk.Entry(row, textvariable=var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 6))
        ttk.Button(row, text="浏览", style="Wide.TButton", command=browse_cmd, width=8).pack(side=tk.RIGHT)

    def _on_root_resize(self, _event=None) -> None:
        width = max(self.root.winfo_width() - 32, 900)
        height = max(self.root.winfo_height() - 32, 640)
        self.canvas.coords(self.canvas_window, 16, 16)
        self.canvas.itemconfigure(self.canvas_window, width=width, height=height)

        if self._bg_original is not None:
            if self._bg_resize_job:
                self.root.after_cancel(self._bg_resize_job)
            self._bg_resize_job = self.root.after(80, self._redraw_background)

    # =========================
    # Background image
    # =========================
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
            LOGGER.warning("Background requested but Pillow missing")
            if not silent:
                messagebox.showwarning("缺少 Pillow", "未安装 Pillow，无法加载 PNG/JPG 背景。\n安装: python -m pip install Pillow")
            return

        try:
            img = Image.open(path)
            self._bg_original = img.convert("RGB")
            self.bg_path_var.set(str(path))
            self._redraw_background()
            self.set_status(f"已加载背景：{path}")
            LOGGER.info("Background loaded: %s", path)
        except OSError:
            LOGGER.exception("Background load failed: %s", path)
            if not silent:
                messagebox.showerror("背景加载失败", f"无法加载图片：{path}")

    def choose_background_image(self) -> None:
        filetypes = [("Image", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"), ("All", "*.*")]
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
        LOGGER.info("Background cleared")

    # =========================
    # Core operations
    # =========================
    def set_status(self, msg: str) -> None:
        self.status_var.set(msg)
        self.root.update_idletasks()

    def choose_games_dir(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.games_dir_var.get() or str(Path.home()))
        if selected:
            self.games_dir_var.set(selected)
            self.set_status(f"已选择游戏目录：{selected}")
            LOGGER.info("Games dir selected: %s", selected)

    def choose_runtime(self) -> None:
        default = Path(self.runtime_path_var.get()).expanduser()
        initialdir = str(default.parent if default.parent.exists() else Path.home())
        selected = filedialog.askopenfilename(initialdir=initialdir)
        if selected:
            self.runtime_path_var.set(selected)
            self.set_status(f"已选择运行时：{selected}")
            LOGGER.info("Runtime selected: %s", selected)

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
        self.games_list.delete(0, tk.END)
        self.games_list.insert(tk.END, "扫描中，请稍候...")
        self.set_status("扫描中...")

        games_dir = Path(self.games_dir_var.get()).expanduser()
        LOGGER.info("Scan started: %s", games_dir)

        def on_done() -> None:
            self.games_list.delete(0, tk.END)
            if not self.xp3_files:
                self.games_list.insert(tk.END, "(未找到 .xp3 文件)")
                self.set_status("扫描完成：未找到 .xp3")
            else:
                for item in self.xp3_files:
                    self.games_list.insert(tk.END, str(item))
                self.set_status(f"扫描完成：找到 {len(self.xp3_files)} 个 .xp3")

            LOGGER.info("Scan finished, count=%d", len(self.xp3_files))

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

        LOGGER.info("Download started: url=%s target=%s", url, target)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.set_status("开始下载运行时...")

        def on_success() -> None:
            self.runtime_path_var.set(str(target))
            self.set_status(f"运行时下载完成：{target}")
            messagebox.showinfo("下载成功", f"运行时已准备好：\n{target}")
            LOGGER.info("Download finished: %s", target)
            if after_download:
                after_download()

        def on_fail(msg: str) -> None:
            messagebox.showerror("下载失败", msg)
            self.set_status("下载失败")
            LOGGER.error("Download failed: %s", msg)

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
                LOGGER.exception("Download network error")
                self.root.after(0, on_fail, f"网络错误：{exc}")
            except OSError as exc:
                LOGGER.exception("Download file error")
                self.root.after(0, on_fail, f"文件错误：{exc}")
            except ValueError as exc:
                LOGGER.exception("Download verify error")
                self.root.after(0, on_fail, str(exc))
            except Exception:
                LOGGER.exception("Unexpected download exception")
                self.root.after(0, on_fail, "未知下载错误，请查看日志。")
            finally:
                self.download_thread = None

        self.download_thread = threading.Thread(target=worker, daemon=False)
        self.download_thread.start()

    def verify_runtime(self) -> None:
        runtime = self.runtime_path()
        if not runtime.exists():
            messagebox.showwarning("校验失败", "运行时文件不存在。")
            self.set_status("校验失败：运行时不存在")
            LOGGER.warning("Verify failed: runtime not found: %s", runtime)
            return

        expected = self.runtime_sha_var.get().strip().lower()
        if not expected:
            messagebox.showinfo("提示", "未填写 SHA256 校验值，无法校验。如需校验，请先填写期望值。")
            self.set_status("未执行校验：缺少 SHA256")
            LOGGER.warning("Verify skipped: SHA256 empty")
            return

        self.set_status("校验中...")
        actual = sha256_of_file(runtime).lower()
        if actual == expected:
            messagebox.showinfo("校验通过", "SHA256 校验通过。")
            self.set_status("校验通过")
            LOGGER.info("Verify passed: %s", runtime)
        else:
            messagebox.showerror("校验失败", f"SHA256 不匹配\n期望: {expected}\n实际: {actual}")
            self.set_status("校验失败：SHA256 不匹配")
            LOGGER.error("Verify failed: expected=%s actual=%s file=%s", expected, actual, runtime)

    def _selected_xp3_paths(self) -> list[Path]:
        return [self.xp3_files[i] for i in self.games_list.curselection() if 0 <= i < len(self.xp3_files)]

    def preview_command(self) -> None:
        selected = self._selected_xp3_paths()
        if not selected:
            messagebox.showwarning("未选择", "请先在列表中选择一个 XP3 文件。")
            return

        try:
            args, _ = build_command(self.template_var.get(), selected[0], self.runtime_path())
            cmd = " ".join(shlex.quote(a) for a in args)
            LOGGER.info("Command preview: %s", cmd)
            messagebox.showinfo("命令预览（首个选中项）", cmd)
        except Exception:
            LOGGER.exception("Command preview failed")
            messagebox.showerror("模板错误", "命令预览失败，请检查模板变量。")

    def _launch_paths(self, targets: list[Path]) -> None:
        runtime = self.runtime_path()
        ok = 0
        for xp3 in targets:
            try:
                args, workdir = build_command(self.template_var.get(), xp3, runtime)
                cmd = " ".join(shlex.quote(a) for a in args)
                LOGGER.info("Launch command: %s (cwd=%s)", cmd, workdir)
                subprocess.Popen(args, cwd=workdir, start_new_session=True)
                ok += 1
                LOGGER.info("Launch success: %s", xp3)
            except Exception:
                LOGGER.exception("Launch failed: %s", xp3)
                messagebox.showerror("启动失败", f"启动失败：{xp3.name}\n请查看日志获取详情。")

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
            LOGGER.warning("Runtime missing when launch: %s", self.runtime_path())
            if yes:
                self._pending_launch_after_download = targets
                self._start_runtime_download(after_download=lambda: self._launch_paths(self._pending_launch_after_download))
            else:
                self.set_status("启动失败：运行时不存在")
            return

        self._launch_paths(targets)

    # =========================
    # Log viewer
    # =========================
    def open_log_window(self) -> None:
        if self.log_window and self.log_window.winfo_exists():
            self.log_window.lift()
            self._refresh_log_text()
            return

        self.log_window = tk.Toplevel(self.root)
        self.log_window.title("查看日志")
        self.log_window.geometry("900x560")

        toolbar = ttk.Frame(self.log_window, padding=6)
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="刷新", command=self._refresh_log_text).pack(side=tk.LEFT)
        ttk.Checkbutton(toolbar, text="自动刷新(2s)", variable=self.log_auto_refresh_var).pack(side=tk.LEFT, padx=8)
        ttk.Label(toolbar, text=str(LOG_PATH)).pack(side=tk.LEFT, padx=10)

        self.log_text = ScrolledText(self.log_window, wrap=tk.NONE)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.configure(state=tk.DISABLED)

        self._refresh_log_text()
        self._schedule_log_auto_refresh()

        def on_close() -> None:
            if self.log_refresh_job:
                self.log_window.after_cancel(self.log_refresh_job)
                self.log_refresh_job = None
            self.log_window.destroy()

        self.log_window.protocol("WM_DELETE_WINDOW", on_close)

    def _refresh_log_text(self) -> None:
        if not self.log_window or not self.log_window.winfo_exists() or self.log_text is None:
            return
        try:
            content = LOG_PATH.read_text(encoding="utf-8") if LOG_PATH.exists() else "(日志文件不存在)"
        except OSError as exc:
            content = f"读取日志失败: {exc}"

        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, content)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _schedule_log_auto_refresh(self) -> None:
        if not self.log_window or not self.log_window.winfo_exists():
            return
        if self.log_auto_refresh_var.get():
            self._refresh_log_text()
        self.log_refresh_job = self.log_window.after(2000, self._schedule_log_auto_refresh)

    # =========================
    # Help window
    # =========================
    def open_help_window(self) -> None:
        win = tk.Toplevel(self.root)
        win.title("详细使用指南")
        win.geometry("860x620")

        text = ScrolledText(win, wrap=tk.WORD)
        text.pack(fill=tk.BOTH, expand=True)

        text.tag_configure("title", font=("Noto Sans CJK SC", 16, "bold"), spacing3=8)
        text.tag_configure("h2", font=("Noto Sans CJK SC", 12, "bold"), spacing1=10, spacing3=4)
        text.tag_configure("body", font=("Noto Sans CJK SC", 11), lmargin1=8, lmargin2=8, spacing3=2)

        lines = HELP_CONTENT.splitlines()
        for line in lines:
            if not line.strip():
                text.insert(tk.END, "\n", "body")
            elif line.endswith("===") or "使用指南" in line:
                text.insert(tk.END, line.replace("=", "") + "\n", "title")
            elif line.endswith("----") or line[0:2].isdigit() or line.startswith("Q"):
                text.insert(tk.END, line.replace("-", "") + "\n", "h2")
            else:
                text.insert(tk.END, line + "\n", "body")

        text.configure(state=tk.DISABLED)

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

        LOGGER.info("Launcher closing")
        try:
            LOG_LISTENER.stop()
        except Exception:
            # 关闭过程避免再次抛错影响退出
            pass
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    LauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
