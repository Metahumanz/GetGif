# GetGif

一个面向 Windows 的本地小工具，用来批量扫描文件夹中的视频，并按设定参数导出 GIF 或静态图片。

应用启动后会在本机开启一个 Web 界面，默认地址为 `http://127.0.0.1:6543`，可以在浏览器里完成目录选择、批量扫描、参数设置、进度查看和结果汇总。

## 功能特点

- 递归扫描指定目录下的视频文件
- 按文件夹创建时间顺序处理视频
- 支持批量为每个视频截取多张 GIF
- 支持导出各时间片段的中间帧为静态图片（PNG、JPG）
- 支持自定义跳过片头、跳过片尾、GIF 时长、帧率、尺寸
- 支持在 GIF 动图和静态图片之间切换导出类型
- 支持保持比例缩放或固定尺寸输出
- 支持并行处理和硬件加速开关
- 启动任务前可先扫描视频并查看预计输出体积
- 支持输出命名模板
- 任务过程支持心跳保活、取消任务、查看进度、日志导出与结果汇总
- 支持多任务顺序队列和历史记录查看
- 自动保存上一次使用的配置到本地 `settings.json`

## 运行环境

- Windows 10 / 11
- Python 3.10+
- 浏览器

说明：

- 项目当前的启动脚本是 `start.bat`，并且代码里使用了 `os.startfile` 和 `tkinter` 文件夹选择框，所以当前更适合在 Windows 下使用。
- FFmpeg 可执行文件由 `imageio-ffmpeg` 提供，已经包含在 `requirements.txt` 中。

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
3. 根据需要设置命名模板和导出参数
4. 点击“扫描视频”确认待处理内容和预计占用空间
5. 点击“开始导出”
6. 在进度区查看当前处理状态，并在需要时导出日志
7. 在“队列与历史”中查看排队任务和已完成任务
8. 完成后点击“打开输出目录”

## 参数说明

| 参数 | 说明 |
| --- | --- |
| `skip_head` | 每个视频开头跳过多少秒 |
| `skip_tail` | 每个视频结尾跳过多少秒 |
| `num_gifs` | 每个视频导出多少个输出文件 |
| `export_mode` | 导出类型，支持 `gif` 和 `image` |
| `image_format` | 静态图片格式，支持 `png` 和 `jpg` |
| `output_name_template` | 输出命名模板，支持 `{video_name}`、`{index}`、`{index2}`、`{index3}`、`{mode}`、`{format}` |
| `gif_duration` | 每张 GIF 的时长，单位秒，仅在 GIF 模式下生效 |
| `gif_fps` | GIF 帧率，仅在 GIF 模式下生效 |
| `gif_width` | 输出宽度 |
| `gif_height` | 输出高度，`auto` 模式下一般保持为 `0` |
| `scale_mode` | `auto` 为保持比例，`fixed` 为固定尺寸 |
| `use_gpu` | 为 FFmpeg 增加 `-hwaccel auto` |
| `use_parallel` | 同一视频内并行导出多张 GIF |

## 输出结构

程序会在输出目录下按“视频名”创建子文件夹，并把对应输出文件放进去。

```text
output_dir/
  video_a/
    video_a_01.gif
    video_a_02.gif
  video_b/
    video_b_01.png
    video_b_02.jpg
```

## 本地配置

- 本地配置文件：`settings.json`
- 本地历史记录：`task_history.json`
- 模板目录：`templates/`
- 核心逻辑目录：`src/`

`settings.json` 会在任务启动时保存当前配置，便于下次打开时自动回填。`task_history.json` 会保存最近的任务历史与日志摘要。这两个文件都已在 `.gitignore` 中忽略，不会进入版本控制。

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
- 如果命名模板没有包含序号，程序会自动追加后缀以避免同一批任务中的文件重名覆盖。
- 如果输出目录中已存在同名文件，FFmpeg 会覆盖旧文件。

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

### 3. 浏览器没有自动打开

服务通常已经启动，可以手动访问：

```text
http://127.0.0.1:6543
```

## License

本项目按照GNU General Public License v3.0协议开源。

点击[LICENSE](LICENSE)查看协议全文。

## 后续可改进方向

- 增加 README 截图或使用演示
- 增加历史记录筛选与搜索
- 增加任务日志级别过滤
- 增加更灵活的命名变量，例如日期和分辨率
