#!/usr/bin/env python3
"""Linux Galgame Launcher (XP3)

改进说明（相对早期版本）：
- 下载时可选择保存位置，不再强制写死到默认路径。
- 支持多选启动、命令预览、窗口几何信息持久化。
- 下载支持进度显示、异常细分处理，并在关闭窗口时检测下载线程状态。
- 运行时缺失时可引导用户立即下载，下载后自动继续启动。
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
    Entry,
    Frame,
    Label,
    Listbox,
    StringVar,
    Tk,
    filedialog,
    messagebox,
)

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
    "window_geometry": "980x650",
}

HELP_TEXT = (
    "使用说明：\n"
    "1) 先设置“游戏根目录”，点击“刷新列表”扫描 .xp3。\n"
    "2) 设置“运行时路径”（可手动选择，或点击“下载运行时”自动获取）。\n"
    "3) 列表支持多选（Ctrl/Shift），可一次启动多个游戏。\n"
    "4) 可用“预览命令”检查当前模板展开结果（不会执行）。\n\n"
    "模板变量（直接写在模板里即可，不需要手工加引号）：\n"
    "- {xp3}: 当前选中 xp3 的完整路径\n"
    "- {game_dir}: xp3 所在目录\n"
    "- {game_name}: xp3 文件名（不含扩展名）\n"
    "- {runtime}: 运行时可执行文件路径\n"
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
    args = shlex.split(expanded)
    return args, game_dir


class LauncherApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("Linux Galgame 启动器（XP3，自包含运行时）")

        self.cfg = ensure_config()
        self.root.geometry(self.cfg.get("window_geometry") or DEFAULT_CONFIG["window_geometry"])

        self.games_dir_var = StringVar(value=self.cfg["games_dir"])
        self.runtime_path_var = StringVar(value=self.cfg["runtime_path"])
        self.template_var = StringVar(value=self.cfg["launch_template"])
        self.runtime_url_var = StringVar(value=self.cfg["runtime_download_url"])
        self.runtime_sha_var = StringVar(value=self.cfg.get("runtime_sha256", ""))
        self.status_var = StringVar(value="就绪")

        self.xp3_files: list[Path] = []
        self.download_thread: threading.Thread | None = None
        self._pending_launch_after_download: list[Path] = []

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.refresh_games()

    def _build_ui(self) -> None:
        top = Frame(self.root)
        top.pack(fill=BOTH, padx=10, pady=10, expand=True)

        Label(top, text="游戏根目录").pack(anchor="w")
        dir_row = Frame(top)
        dir_row.pack(fill=BOTH)
        Entry(dir_row, textvariable=self.games_dir_var).pack(side=LEFT, fill=BOTH, expand=True)
        Button(dir_row, text="浏览", command=self.choose_games_dir).pack(side=RIGHT)

        Label(top, text="运行时路径（可执行文件）").pack(anchor="w", pady=(8, 0))
        rt_row = Frame(top)
        rt_row.pack(fill=BOTH)
        Entry(rt_row, textvariable=self.runtime_path_var).pack(side=LEFT, fill=BOTH, expand=True)
        Button(rt_row, text="浏览", command=self.choose_runtime).pack(side=RIGHT)

        Label(top, text="运行时下载 URL（需用户确认后下载）").pack(anchor="w", pady=(8, 0))
        Entry(top, textvariable=self.runtime_url_var).pack(fill=BOTH)
        Label(top, text="运行时 SHA256（可选，建议填写）").pack(anchor="w", pady=(8, 0))
        Entry(top, textvariable=self.runtime_sha_var).pack(fill=BOTH)

        Label(top, text="启动命令模板（变量: {xp3} {game_dir} {game_name} {runtime}）").pack(anchor="w", pady=(8, 0))
        Entry(top, textvariable=self.template_var).pack(fill=BOTH)

        controls = Frame(top)
        controls.pack(fill=BOTH, pady=(10, 8))
        Button(controls, text="刷新列表", command=self.refresh_games).pack(side=LEFT)
        Button(controls, text="保存配置", command=self.save_settings).pack(side=LEFT, padx=8)
        Button(controls, text="下载运行时", command=self.download_runtime_with_confirm).pack(side=LEFT)
        Button(controls, text="校验运行时", command=self.verify_runtime).pack(side=LEFT, padx=8)
        Button(controls, text="预览命令", command=self.preview_command).pack(side=LEFT)
        Button(controls, text="帮助", command=self.show_help).pack(side=LEFT, padx=8)

        self.games_list = Listbox(top, selectmode=EXTENDED)
        self.games_list.pack(fill=BOTH, expand=True)

        bottom = Frame(self.root)
        bottom.pack(fill=BOTH, padx=10, pady=(0, 10))
        Button(bottom, text="启动选中游戏", command=self.launch_selected).pack(side=LEFT)
        Label(bottom, text="状态：").pack(side=LEFT, padx=(12, 0))
        Label(bottom, textvariable=self.status_var).pack(side=LEFT)

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
                batch: list[Path] = []
                for p in games_dir.rglob("*"):
                    if p.is_file() and p.suffix.lower() == ".xp3":
                        found.append(p)
                        if len(batch) >= 100:
                            self.root.after(0, self.set_status, f"扫描中... 已发现 {len(found)} 个 .xp3")
                            batch = []
                if batch:
                    self.root.after(0, self.set_status, f"扫描中... 已发现 {len(found)} 个 .xp3")

            found.sort(key=lambda x: str(x).lower())
            self.xp3_files = found
            self.root.after(0, on_done)

        threading.Thread(target=worker, daemon=True).start()

    def runtime_path(self) -> Path:
        return Path(self.runtime_path_var.get().strip()).expanduser()

    def _select_download_target(self) -> Path:
        """让用户优先选择下载目标，取消则回退默认路径。"""
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        chosen = filedialog.asksaveasfilename(
            title="选择运行时保存位置（取消则使用默认路径）",
            initialdir=str(RUNTIME_DIR),
            initialfile=self.runtime_path().name or DEFAULT_RUNTIME_PATH.name,
        )
        if not chosen:
            return DEFAULT_RUNTIME_PATH
        return Path(chosen).expanduser()

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
        yes = messagebox.askyesno(
            "确认下载",
            f"将下载运行时到：{target}\n"
            f"来源：{url}\n\n"
            "请确认你信任该来源。是否继续？",
        )
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
                    last_report_percent = -1
                    last_report_kib = -1
                    while True:
                        block = resp.read(1024 * 128)
                        if not block:
                            break
                        out.write(block)
                        downloaded += len(block)
                        if total > 0:
                            percent = int(downloaded * 100 / total)
                            if percent >= last_report_percent + 1:
                                last_report_percent = percent
                                self.root.after(0, self.set_status, f"下载中... {percent}%")
                        else:
                            kib = downloaded // 1024
                            if kib >= last_report_kib + 100:
                                last_report_kib = kib
                                self.root.after(0, self.set_status, f"下载中... {kib} KiB")

                target.chmod(0o755)

                expected = self.runtime_sha_var.get().strip().lower()
                if expected:
                    actual = sha256_of_file(target).lower()
                    if actual != expected:
                        target.unlink(missing_ok=True)
                        raise ValueError(
                            "SHA256 校验失败。\n"
                            f"期望: {expected}\n"
                            f"实际: {actual}\n"
                            "已删除下载文件，请检查下载源或校验值。"
                        )

                self.root.after(0, on_success)
            except urllib.error.URLError as exc:
                self.root.after(0, on_fail, f"网络错误：{exc}")
            except OSError as exc:
                self.root.after(0, on_fail, f"文件写入/权限错误：{exc}")
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
        indices = list(self.games_list.curselection())
        valid = [self.xp3_files[i] for i in indices if 0 <= i < len(self.xp3_files)]
        return valid

    def preview_command(self) -> None:
        selected = self._selected_xp3_paths()
        if not selected:
            messagebox.showwarning("未选择", "请先在列表中选择一个 XP3 文件。")
            return

        runtime = self.runtime_path()
        try:
            args, _ = build_command(self.template_var.get(), selected[0], runtime)
        except KeyError as exc:
            messagebox.showerror("模板错误", f"模板变量错误：{exc}")
            return
        except ValueError as exc:
            messagebox.showerror("模板错误", str(exc))
            return

        pretty = " ".join(shlex.quote(a) for a in args)
        messagebox.showinfo("命令预览（首个选中项）", pretty)

    def _launch_paths(self, targets: list[Path]) -> None:
        runtime = self.runtime_path()
        ok_count = 0
        for xp3 in targets:
            try:
                args, workdir = build_command(self.template_var.get(), xp3, runtime)
                subprocess.Popen(args, cwd=workdir, start_new_session=True)
                ok_count += 1
            except FileNotFoundError as exc:
                messagebox.showerror("启动失败", f"命令不存在：{exc}")
            except KeyError as exc:
                messagebox.showerror("启动失败", f"模板变量错误：{exc}")
            except OSError as exc:
                messagebox.showerror("启动失败", f"系统错误：{exc}")

        if ok_count:
            self.set_status(f"已启动 {ok_count} 个游戏")

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

        runtime = self.runtime_path()
        if not runtime.exists():
            yes = messagebox.askyesno(
                "运行时不存在",
                "运行时不存在，是否立即下载？\n"
                "若下载成功，将自动继续启动当前选中的游戏。",
            )
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
