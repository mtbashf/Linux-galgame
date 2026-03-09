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

## 5. 打包建议（新手向，含多发行版依赖）

下面按“**先本地运行成功 -> 再打包**”的顺序来，适合第一次接触打包的用户。

### 5.1 打包前先确认基础依赖

> 目的：避免打包后才发现 `tkinter` 缺失。

#### Arch Linux

```bash
sudo pacman -S --needed python tk python-pip
```

#### Debian / Ubuntu / Linux Mint

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-tk tk
```

#### Fedora

```bash
sudo dnf install -y python3 python3-pip python3-tkinter tk
```

#### openSUSE Tumbleweed / Leap

```bash
sudo zypper install -y python3 python3-pip python3-tk tk
```

#### 验证 Tk 是否可用（所有发行版通用）

```bash
python3 -m tkinter
```

如果能弹出小窗口，说明 GUI 依赖正常。

---

### 5.2 安装 PyInstaller

推荐使用 `pipx`（不污染系统 Python），没有 `pipx` 也可以直接用 `pip`。

#### 方式 A：pipx（推荐）

```bash
python3 -m pip install --user pipx
python3 -m pipx ensurepath
pipx install pyinstaller
```

#### 方式 B：pip（简单直接）

```bash
python3 -m pip install --user pyinstaller
```

---

### 5.3 执行打包（生成单文件可执行程序）

在项目根目录执行：

```bash
pyinstaller --onefile --windowed --name linux-galgame launcher.py
```

如果你是用 `pip --user` 安装，可能需要：

```bash
python3 -m PyInstaller --onefile --windowed --name linux-galgame launcher.py
```

打包后重点看两个目录：

- `dist/linux-galgame`：最终可执行文件
- `build/`：中间构建文件（可删除）

先直接运行打包结果测试：

```bash
./dist/linux-galgame
```

---

### 5.4 打包为 AppImage（面向分发）

最稳妥流程是：**先用 PyInstaller 得到可执行文件，再封装 AppImage**。

#### 步骤 1：准备 AppDir 结构

```bash
mkdir -p AppDir/usr/bin
cp dist/linux-galgame AppDir/usr/bin/
chmod +x AppDir/usr/bin/linux-galgame
```

#### 步骤 2：创建 desktop 文件

创建 `AppDir/linux-galgame.desktop`，内容如下：

```ini
[Desktop Entry]
Type=Application
Name=Linux Galgame Launcher
Exec=linux-galgame
Icon=linux-galgame
Categories=Game;
Terminal=false
```

#### 步骤 3：准备图标（可选但推荐）

将你的图标文件放到：

- `AppDir/linux-galgame.png`（建议 256x256）

#### 步骤 4：使用 appimagetool 生成 AppImage

先下载 `appimagetool`（按你的架构选择 x86_64 / aarch64），然后执行：

```bash
chmod +x appimagetool-*.AppImage
./appimagetool-*.AppImage AppDir
```

成功后会在当前目录生成类似：

- `Linux_Galgame_Launcher-x86_64.AppImage`

---

### 5.5 常见打包问题（小白高频）

1. **命令找不到 `pyinstaller`**  
   用 `python3 -m PyInstaller ...` 运行，或确认 `~/.local/bin` 在 `PATH` 中。

2. **运行打包产物仍报 Tk 缺失**  
   说明构建机环境本身缺 Tk，请先安装 `tk` 再重新打包。

3. **在另一台发行版无法运行**  
   尽量在较老、兼容性更高的环境构建（或使用容器/CI 构建），并优先使用静态运行时。

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

---

## 8. 常见报错：`ImportError: libtk8.6.so`

如果运行 `python launcher.py` 出现：

```text
ImportError: libtk8.6.so: cannot open shared object file: No such file or directory
```

说明当前 Python 环境找不到 Tk 动态库（`tkinter` 依赖系统 `tk`）。

### Arch Linux

```bash
sudo pacman -S tk
```

然后验证：

```bash
python -m tkinter
```

如果能弹出一个小窗口，说明 Tk 可用。

### 其他发行版（参考）

- Debian/Ubuntu：`sudo apt install python3-tk tk`
- Fedora：`sudo dnf install python3-tkinter tk`

### 如果你使用 pyenv/conda/自编译 Python

你当前的 Python 解释器可能没有正确链接系统 Tk。请确保该解释器安装了 Tk 支持，或切换到系统 Python 运行本项目。
