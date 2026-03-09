# Linux Galgame Launcher (XP3)

一个面向 Linux 的 Galgame 启动器（Tkinter GUI），核心目标是：
- 扫描并启动 `.xp3` 文件；
- 运行时可“手动指定”或“用户确认后自动下载”；
- 配置持久化，便于长期使用与后续打包分发。

## 功能概览

- 递归扫描游戏目录中的 `.xp3` 文件。
- 启动模板可配置，支持变量：`{xp3}`、`{game_dir}`、`{game_name}`、`{runtime}`。
- 支持下载运行时到：`~/.local/share/linux-galgame/runtime/`。
- 支持 SHA256 校验（推荐填写）。
- 状态栏反馈 + 常见错误中文提示。
- 启动游戏使用非阻塞方式（可同时启动多个游戏）。
- 支持多选启动、命令预览、窗口大小位置记忆。

---

## 1. 依赖

仅需 Python 和 Tkinter（标准库）。

Arch Linux 示例：

```bash
sudo pacman -S python tk
```

---

## 2. 运行

```bash
python launcher.py
```

首次运行会自动创建配置文件：

- `~/.config/linux-galgame/config.json`

默认运行时目标路径：

- `~/.local/share/linux-galgame/runtime/krkrsdl2`

---

## 3. 运行时获取方式

### 方式 A：自动下载（推荐，需用户确认）

1. 在界面里填写“运行时下载 URL”（建议官方发布页直链）。
2. 可选填写 SHA256。
3. 点击“下载运行时”，在确认弹窗中确认来源可信。
4. 下载后会自动赋予可执行权限并写入运行时路径。
5. 下载时可选择保存位置；如果取消选择，则自动使用默认路径。

### 方式 B：手动放置

1. 将运行时二进制放到任意目录（例如默认目录）。
2. 在界面中通过“运行时路径 -> 浏览”选择可执行文件。
3. 点击“保存配置”。

> 建议优先使用“静态编译”的 Linux 通用二进制，以提高跨发行版兼容性。

---

## 4. 启动模板

默认模板：

```text
{runtime} {xp3}
```

可用变量：

- `{xp3}`：当前选中的 xp3 完整路径
- `{game_dir}`：xp3 所在目录
- `{game_name}`：xp3 文件名（不含扩展名）
- `{runtime}`：运行时可执行文件路径

示例：

```text
{runtime} {xp3}
```

```text
{runtime} -config ./config.tjs {xp3}
```

---

## 5. 打包建议（PyInstaller / AppImage）

本项目仅使用标准库，结构简单，方便打包。

### 5.1 PyInstaller（先打包）

```bash
pip install pyinstaller
pyinstaller --onefile --windowed launcher.py
```

输出在 `dist/launcher`。

### 5.2 再制作 AppImage（示意流程）

可使用 `python-appimage` 或 AppImage 工具链，将 `dist/launcher` 放入 AppDir 后生成 AppImage。

示意（实际命令按你选择的工具调整）：

1. 准备 `AppDir/usr/bin/launcher`
2. 放置 `.desktop` 与图标
3. 运行打包工具生成 `LinuxGalgameLauncher-x86_64.AppImage`

---

## 6. 配置文件示例

```json
{
  "games_dir": "/home/you/Games/galgame",
  "runtime_path": "/home/you/.local/share/linux-galgame/runtime/krkrsdl2",
  "launch_template": "{runtime} {xp3}",
  "runtime_download_url": "https://example.com/krkrsdl2-linux-x86_64",
  "runtime_sha256": ""
}
```

---

## 7. 注意事项

- 自动下载前请确认来源可信。
- 若填写 SHA256，下载后会校验；不匹配会删除已下载文件。
- 若运行时不存在，启动时会提示你是否立即下载，下载完成可自动继续启动。
- 若扫描目录很大，程序会显示“扫描中...”，并在后台线程中完成扫描。
- 若关闭窗口时仍在下载，会询问是否强制退出。
- 若启动失败，请先检查：运行时路径、模板变量、文件权限。
