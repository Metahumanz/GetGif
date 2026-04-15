# GetGif

一个面向 Windows 的本地小工具，用来批量扫描文件夹中的视频，并按设定参数导出 GIF。

应用启动后会在本机开启一个 Web 界面，默认地址为 `http://127.0.0.1:6543`，可以在浏览器里完成目录选择、批量扫描、参数设置、进度查看和结果汇总。

## 功能特点

- 递归扫描指定目录下的视频文件
- 按文件夹创建时间顺序处理视频
- 支持批量为每个视频截取多张 GIF
- 支持自定义跳过片头、跳过片尾、GIF 时长、帧率、尺寸
- 支持保持比例缩放或固定尺寸输出
- 支持并行处理和硬件加速开关
- 启动任务前可先扫描视频并查看预计输出体积
- 任务过程支持心跳保活、取消任务、查看进度与结果汇总
- 自动保存上一次使用的配置到本地 `settings.json`

## 运行环境

- Windows 10 / 11
- Python 3.10+
- 浏览器

说明：

- 项目当前的启动脚本是 `start.bat`，并且代码里使用了 `os.startfile` 和 `tkinter` 文件夹选择框，所以当前更适合在 Windows 下使用。
- FFmpeg 通过 Python 依赖链提供。如果启动时报 `imageio_ffmpeg` 缺失，可手动执行 `pip install imageio-ffmpeg`。

## 安装依赖

推荐先创建虚拟环境：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

如果你更习惯直接运行，也可以交给 `start.bat` 自动检测虚拟环境并安装依赖。

## 启动方式

### 方式一：直接双击

双击仓库根目录下的 `start.bat`。

这个脚本会：

1. 优先使用 `venv` 或 `.venv` 中的 Python
2. 自动执行 `pip install -r requirements.txt`
3. 启动 `app.py`

### 方式二：命令行启动

```powershell
python app.py
```

启动成功后会自动尝试打开浏览器。

## 使用流程

1. 选择“视频文件夹”
2. 选择“输出文件夹”
3. 根据需要调整参数
4. 点击“扫描视频”确认待处理内容和预计占用空间
5. 点击“开始导出”
6. 在进度区查看当前处理状态
7. 完成后点击“打开输出目录”

## 参数说明

| 参数 | 说明 |
| --- | --- |
| `skip_head` | 每个视频开头跳过多少秒 |
| `skip_tail` | 每个视频结尾跳过多少秒 |
| `num_gifs` | 每个视频导出多少张 GIF |
| `gif_duration` | 每张 GIF 的时长，单位秒 |
| `gif_fps` | GIF 帧率 |
| `gif_width` | 输出宽度 |
| `gif_height` | 输出高度，`auto` 模式下一般保持为 `0` |
| `scale_mode` | `auto` 为保持比例，`fixed` 为固定尺寸 |
| `use_gpu` | 为 FFmpeg 增加 `-hwaccel auto` |
| `use_parallel` | 同一视频内并行导出多张 GIF |

## 输出结构

程序会在输出目录下按“视频名”创建子文件夹，并把对应 GIF 放进去：

```text
output_dir/
  video_a/
    video_a_01.gif
    video_a_02.gif
  video_b/
    video_b_01.gif
    video_b_02.gif
```

## 本地配置

- 本地配置文件：`settings.json`
- 模板目录：`templates/`
- 核心逻辑目录：`src/`

`settings.json` 会在任务启动时保存当前配置，便于下次打开时自动回填。这个文件已在 `.gitignore` 中忽略，不会进入版本控制。

## 项目结构

```text
getGif/
  app.py
  start.bat
  requirements.txt
  templates/
    index.html
  src/
    __init__.py
    config.py
    service.py
    webapp.py
```

各文件作用：

- `app.py`：程序入口
- `src/webapp.py`：Flask 路由与 Web 服务启动逻辑
- `src/service.py`：扫描视频、读取信息、调用 FFmpeg 导出 GIF 的核心逻辑
- `src/config.py`：默认配置、路径和常量
- `templates/index.html`：前端页面
- `start.bat`：Windows 启动脚本

## 当前支持的视频格式

当前代码里支持以下扩展名：

`mp4`、`avi`、`mkv`、`mov`、`wmv`、`flv`、`webm`、`m4v`、`mpg`、`mpeg`、`3gp`、`ts`

## 注意事项

- 扫描结果中的“预计占用空间”只是前端粗略估算，不代表最终实际文件大小。
- 如果视频过短，或者跳过片头片尾之后剩余时长不足，任务会跳过该视频。
- 程序在长时间无心跳活动后会自动退出，当前默认超时时间为 300 秒。
- 开启 GPU 并不保证所有机器都明显提速，效果取决于本机 FFmpeg 和显卡环境。
- 如果输出目录中已存在同名 GIF，FFmpeg 会覆盖旧文件。

## 常见问题

### 1. 双击后窗口一闪而过

通常说明 Python 或依赖没有安装完整。建议在 PowerShell 中手动运行：

```powershell
python app.py
```

这样可以直接看到报错信息。

### 2. 启动时报 `No module named ...`

先确认你使用的是项目虚拟环境，然后重新安装依赖：

```powershell
pip install -r requirements.txt
```

如果提示缺少 `imageio_ffmpeg`，再执行：

```powershell
pip install imageio-ffmpeg
```

### 3. 浏览器没有自动打开

服务通常已经启动，可以手动访问：

```text
http://127.0.0.1:6543
```

## License

This project is licensed under the GNU General Public License v3.0.

See [LICENSE](LICENSE) for the full license text.

## 后续可改进方向

- 增加 README 截图或使用演示
- 补充日志导出
- 增加输出命名模板
- 增加多任务队列和历史记录
- 精简 `requirements.txt` 中未使用的依赖
