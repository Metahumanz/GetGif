# GetGif

一个面向 Windows 的本地小工具，用来批量扫描文件夹中的视频，并按设定参数导出 GIF、静态图片或 MP4 视频片段。

应用启动后会在本机开启一个 Web 界面，默认地址为 `http://127.0.0.1:6543`，可以在浏览器里完成目录选择、批量扫描、参数设置、进度查看和结果汇总。

## 功能特点

- 递归扫描指定目录下的视频文件
- 按文件夹创建时间顺序处理视频
- 支持批量为每个视频截取多张 GIF
- 支持导出各时间片段的中间帧为静态图片（PNG、JPG）
- 支持导出各时间片段为 MP4 视频（不含音频）
- MP4 支持 H.264 / H.265 两种编码标准
- MP4 编码器可选软件编码（libx264 / libx265）或本机可用的硬件编码（NVIDIA NVENC、Intel QSV、AMD AMF），启动时会自动探测本机可用项
- 支持自定义跳过片头、跳过片尾、GIF 时长、帧率、尺寸
- 支持在 GIF 动图、静态图片和 MP4 视频之间切换导出类型
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
| `export_mode` | 导出类型，支持 `gif`、`image` 和 `mp4` |
| `image_format` | 静态图片格式，支持 `png` 和 `jpg` |
| `video_codec` | MP4 编码标准，支持 `h264`（H.264/AVC）和 `h265`（H.265/HEVC） |
| `video_encoder` | MP4 编码器，支持 `auto`（优先本机可用硬件编码器）或具体编码器名（`libx264`、`libx265`、`h264_nvenc`、`hevc_nvenc`、`h264_qsv`、`hevc_qsv`、`h264_amf`、`hevc_amf`），界面只显示本机真实可用的项 |
| `output_name_template` | 输出命名模板，支持 `{video_name}`、`{index}`、`{index2}`、`{index3}`、`{mode}`、`{format}` |
| `gif_duration` | 每个输出片段的时长，单位秒，在 GIF 和 MP4 模式下生效 |
| `gif_fps` | 输出帧率，在 GIF 和 MP4 模式下生效 |
| `gif_width` | 输出宽度 |
| `gif_height` | 输出高度，`auto` 模式下一般保持为 `0` |
| `scale_mode` | `auto` 为保持比例，`fixed` 为固定尺寸 |
| `use_gpu` | 为 FFmpeg 增加 `-hwaccel auto`（硬件解码加速） |
| `use_parallel` | 同一视频内并行导出多个片段（使用硬件编码器时会自动降低并行数，避免超过显卡编码会话上限） |

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
  video_c/
    video_c_01.mp4
    video_c_02.mp4
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
    app/
      service.py
      webapp.py
    core/
      config.py
      task_helpers.py
    media/
      encoder_catalog.py
      video_pipeline.py
    platform/
      system_ops.py
    runtime/
      activity_monitor.py
      task_history_runtime.py
      task_queue.py
      task_runtime.py
      task_state.py
    stores/
      config_store.py
      history_store.py
      scan_cache.py
```

各文件作用：

- `app.py`：程序入口
- `src/app/webapp.py`：Flask 路由与 Web 服务启动逻辑
- `src/app/service.py`：聚合各模块，提供业务接口
- `src/media/video_pipeline.py`：视频扫描与 FFmpeg 导出（GIF / 图片 / MP4）核心逻辑
- `src/media/encoder_catalog.py`：H.264/H.265 编码器目录、本机硬件编码器探测与选择
- `src/runtime/`：任务状态、队列、进度与历史
- `src/stores/`：配置、历史与扫描缓存的持久化
- `src/core/config.py`：默认配置、路径和常量
- `templates/index.html`：前端页面
- `start.bat`：Windows 启动脚本

## 当前支持的视频格式

当前代码里支持以下扩展名：

`mp4`、`avi`、`mkv`、`mov`、`wmv`、`flv`、`webm`、`m4v`、`mpg`、`mpeg`、`3gp`、`ts`

## 注意事项

- 扫描结果中的“预计占用空间”只是前端粗略估算，不代表最终实际文件大小。
- 如果视频过短，或者跳过片头片尾之后剩余时长不足，任务会跳过该视频。
- 程序在长时间无心跳活动后会自动退出，当前默认超时时间为 300 秒。
- 任务运行期间依赖浏览器心跳保活：心跳中断超过 90 秒时任务会“暂停”等待（应对标签页休眠、电脑短暂睡眠等），浏览器恢复后自动继续；超过 30 分钟仍无心跳才停止任务（状态显示为“已断开”）。只有点击“取消任务”才会显示“已取消”。
- 开启 GPU 解码加速并不保证所有机器都明显提速，效果取决于本机 FFmpeg 和显卡环境。
- MP4 模式下如果选择硬件编码器，实际速度还受显卡编码会话上限影响，程序会自动降低并行数。
- 硬件编码器以“本机试编码成功”为准出现在界面中，未出现在列表中的项表示当前不可用。
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
