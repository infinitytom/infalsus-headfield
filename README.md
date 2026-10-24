# HeadField：用头部转动控制 In Falsus 横向 Field（实验原型）

> **English summary.** HeadField is an experimental Windows tool that turns slight left/right head turns, seen by an ordinary webcam (MediaPipe face landmarks → head yaw, calibrated per user), into horizontal mouse movement — e.g. to control the Field in the rhythm game *In Falsus* while the hands stay on the keyboard. It includes calibration, validation and a follow test with honest metrics (error, jitter, processing time — **not** end-to-end latency). Images are processed locally and never saved or uploaded. Mouse output is off by default; global hotkeys Ctrl+Alt+M / N / P / C / Q toggle output, cycle injection mode, pause, re-centre and quit. Windows 10/11, Python 3.10+.
>
> **Quick start (Windows):** install 64-bit Python 3.10+ → double-click `1_安装依赖.bat` (install) → `2_启动HeadField.bat` (run; pick your webcam by name and a profile name) → press `c` to calibrate (centre / left / right head turn) → `v` / `t` to test → in game toggle output with Ctrl+Alt+M, re-centre with Ctrl+Alt+C.
>
> Not affiliated with or endorsed by the developers of *In Falsus*. Simulated input may be restricted by a game's rules (e.g. leaderboards) — check them yourself; use at your own risk.

用普通摄像头把**头部的轻微左右转动**变成横向位置，可选地转成鼠标的**横向移动**，用来控制 Field。地面六键照常用键盘。眼睛可以专心读谱，不会和控制抢视线。

> - 它测的是 MediaPipe 估计的**头部姿态**，光线、遮挡、离摄像头太远都会影响结果。
> - 面板上的“处理耗时”只是“收到一帧 → 算出输出”这一段，**不是端到端延迟**。它不包含摄像头曝光、USB 传输、屏幕刷新和游戏读取输入的时间，真实延迟一定更长。
> - 在实测之前，不能保证它能打高难谱。游戏是否接受模拟鼠标输入，需要你自己验证（见“在游戏中使用”）。

## 免责声明
- 本项目与 *In Falsus* 的开发方没有任何关系，也未获得其认可。
- 模拟鼠标输入可能违反某些游戏的规则（例如排行榜、排位成绩）。请自行确认规则，使用风险由使用者自己承担。

## 隐私
- 图像只在本机内存里处理，**不录制、不保存任何画面、不上传**。
- 写入硬盘的只有数字：`config.json`（摄像头名称和参数）、`profiles/<使用者>.json`（每个人的校准）、`logs/`（测试结果的数值和 CSV）。
- 唯一的联网操作是安装时下载 Python 包和人脸模型。

## 安装（只需一次）
0. 需要 Windows 10/11 和 64 位 Python 3.10 以上（推荐 3.13）。没有的话从 python.org 安装，或者运行 `winget install Python.Python.3.13`；安装脚本找不到 Python 时会提示。
1. 双击 **`1_安装依赖.bat`**。它会在项目目录里建立独立环境 `.venv`（不影响系统 Python），安装依赖，并下载 `models/face_landmarker.task`（几 MB，来自 Google MediaPipe）。
2. 如果模型下载失败，手动下载
   `https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task`
   放到 `models\face_landmarker.task`。
3. 日志在 `logs\setup_log.txt`。

## 启动
双击 **`2_启动HeadField.bat`**。命令行窗口里会先问两件事：

1. **选摄像头（只在第一次）**：列出所有摄像头的**名称**，这时不会打开任何一个；你输入编号后，程序只打开你选的那个。名称里带“虚拟 / OBS / 前置 / 后置 / 组合 / phone”等字样的会标注出来，并要求二次确认。像小米互联这类软件会把手机摄像头注册成电脑摄像头，误开会启动手机摄像头。选择按名称保存在 `config.json`；要换摄像头，双击 `重新选择摄像头.bat`。
2. **使用者档案**：输入使用者名称（直接回车 = 上次的档案）。每个人的校准单独保存，换人时不会互相覆盖。

之后会出现两个窗口：
- **HeadField**：摄像头小预览（镜像，只画几个脸部关键点）、状态和帧率、屏幕宽度条、最近 4 秒的轨迹曲线。
- **HeadField Controls**：滑块参数。

跟踪和画面绘制在不同线程里运行。面板第二行同时显示 `camera fps`（摄像头）和 `tracked fps`（实际处理）；任一低于 20 fps，或者实际处理明显慢于摄像头时，该行会变红。

## 校准
1. 坐成平时打歌的姿势，在 HeadField 窗口按 **c**。全屏界面会依次提示：**看中间 → 头向左转 → 头向右转 → 回到中间**，每一步保持约 1 秒，全程约 12 秒。眼睛一直看屏幕中央的十字，只转头。
2. **转多少由你决定**：向左转到的那个程度就对应左边缘，向右同理。“略微转动”就够了，选一个能长时间保持、不累的幅度。转动幅度小于 3° 时会提示你转大一点。
3. **回中**：坐姿慢慢偏移后，正视屏幕中间，按全局快捷键 **Ctrl+Alt+C**（主窗口里也可以按 `r`），就会把当前姿势重新设为中心，左右幅度不变。
4. 可选三种输入信号（`config.json` 的 `head_signal`）：`"yaw"`（默认，MediaPipe 头部转角）、`"nose_rel"`（鼻尖相对两侧脸颊中点的偏移，也是转头，换算成脸宽的比例，不受离摄像头远近影响）、`"nose_x"`（鼻尖在画面中的位置，适合习惯平移头部的人）。校准时三种都会记录，切换后不用重新校准。哪种更稳，可以分别用 `v` 验证比较一下。
5. 眨眼不影响输出，只有丢失人脸时才暂停。

## 测试：先看效果，再进游戏
- **v 验证**：依次出现 5 个白点，转头把青色光标对准并保持。结果给出平均误差和保持时的抖动。
- **t 跟随测试**（约 45 秒）：第 1 部分白点左右匀速摆动，用光标跟住；第 2 部分白点突然跳到别处，尽快把光标移过去。结果给出误差和到达 50% / 90% 的时间。这些时间**包含你自己的反应和转头时间**，不是系统延迟，适合在改参数前后各测一次做对比。
- 结果保存在 `logs/` 里（`calib_* / valid_* / test_*`），同时记录了当时的摄像头帧率和实际处理帧率。

## 在游戏中使用
1. 先启动 HeadField 并完成校准，确认 `v` 和 `t` 的结果你满意。
2. 打开 In Falsus。**建议用无边框窗口或窗口化**：独占全屏时看不到 HeadField 窗口，不过全局快捷键和提示音仍然有效。
3. 进入练习或简单谱，按 **Ctrl+Alt+M** 打开鼠标输出（高音 = 开，低音 = 关）。
4. 转头，看 Field 是否跟着动。默认用 `abs` 方式，并把光标高度固定在屏幕中间（`config.json` 的 `output_y: 0.5`）；已知可用的同类工具用的就是这种方式（`pydirectinput.moveTo`）。如果不动，按 **Ctrl+Alt+N** 切换注入方式（响 1 / 2 / 3 声分别对应 `setpos` / `abs` / `rel`），每种都试一下：
   - `setpos`：直接设置光标位置。
   - `abs`：以绝对坐标发送鼠标移动事件（默认）。
   - `rel`：发送相对移动。如果游戏读的是鼠标原始增量，通常只有这一种有效；幅度用 `rel scale` 滑块和游戏内灵敏度配合调整。
   三种都没反应，说明游戏不接受这类模拟输入。
5. 如果 Field 只对应屏幕的一部分，用 `out left %` / `out right %` 把输出限制在 Field 的范围内，用 `gain %` 调整幅度。
6. 打歌中途：**Ctrl+Alt+C** 回中、**Ctrl+Alt+P** 暂停或恢复、**Ctrl+Alt+Q** 退出。暂停后光标不再移动，鼠标可以正常使用。

如果还是不动，先看 `logs/session_*.log`：程序每秒记一行，包括当时的前台程序、鼠标输出是否打开，以及**这一秒实际发出了多少次移动**（`moves_sent/s`）。如果游戏在前台时这个数是 0，说明输出根本没打开（例如组合键被游戏吞掉了），可以先在 HeadField 窗口里按 `o` 打开输出，再切回游戏。`tools\input_probe.bat` 可以不用摄像头，单独测试各种注入方式在游戏里是否有效。

## 帧率太低怎么办
每秒处理的帧数决定了跟手程度。帧率低时，先看面板上的 `camera fps`：
- **camera fps 本身就低（例如 10–15）**：通常是光线暗，摄像头自动延长了曝光时间。解决办法：
  1. 给脸部补光（屏幕亮度调高、开台灯），这一步最有效；
  2. 在主窗口按 **e**，打开摄像头驱动的设置面板，关闭“低光补偿”之类的选项，或者把曝光改成手动；
  3. 或者在 `config.json` 里设置 `"cam_exposure": -6`（固定 16 ms 曝光），画面会变暗，但帧率能稳定在 30 左右。设回 `null` 就恢复自动曝光。
  4. 双击 `tools\fps_check.bat`，自动比较自动曝光和几种固定曝光下的帧率、亮度和人脸检测率，结果在 `logs\fps_check.json`。它只会打开你选定的那个摄像头。
- **camera fps 正常、tracked fps 低**：说明电脑处理不过来。程序启动时已经提高了自身优先级，并关闭了 Windows 对它的节能降频；如果还是低，先关掉其他占用 CPU 的程序再试。

## 参数（Controls 窗口）
| 滑块 | 含义 | 调整方向 |
|---|---|---|
| smooth mincut Hz x10 | One Euro 滤波的最低截止频率（15 = 1.5 Hz） | 调低更稳、拖得更慢；调高更跟手、更抖 |
| speed beta x100 | 速度自适应：移动越快，截止频率越高 | 调高后快速转头时滞后更小，静止时不受影响 |
| gain % | 幅度：相对中心放大或缩小 | >100 时，转一点头，输出动更多 |
| curve x100 | 非线性曲线：100 = 线性；大于 100 时，中心附近移动慢、便于精细控制，越靠两边越快（左右边缘位置不变） | 想要中间更稳就试 130–180 |
| offset (50=0) | 整体左右偏移 | 一般用回中（Ctrl+Alt+C）就够了 |
| out left % / out right % | 输出范围夹紧（屏幕百分比） | 设成 Field 在屏幕上的左右边界 |
| deadband 0.1% | 死区：小于这个变化量就不动 | 想让静止更稳时设 5–15 |
| rel scale x100 | 仅在 rel 模式下：每个屏幕像素对应多少鼠标计数 | 配合游戏内灵敏度一起调 |

按 **s** 保存参数（退出时也会自动保存）。

## 快捷键
| 全局（游戏在前台时也有效） | 作用 |
|---|---|
| **Ctrl+Alt+M** | 开 / 关鼠标输出（未校准时拒绝开启） |
| **Ctrl+Alt+N** | 切换注入方式 setpos → abs → rel（响 1 / 2 / 3 声） |
| **Ctrl+Alt+C** | 回中：把当前头部姿势设为中心 |
| **Ctrl+Alt+P** | 暂停 / 恢复输出 |
| **Ctrl+Alt+Q** | 退出程序 |

主窗口按键：`c` 校准、`v` 验证、`t` 跟随测试、`r` 回中、`o` 开关鼠标输出、`m` 切换注入方式、`e` 摄像头设置、`s` 保存、`q` 退出、`ESC` 中止校准或测试。如果全局组合键被别的软件占用，启动时面板上会显示是哪几个，可以在 `config.json` 的 `hotkeys` 里改成别的组合（例如 `"ctrl+shift+f9"`），支持 ctrl / alt / shift / win 加字母、数字、F1–F24。最常见的占用者是**另一个还在运行的 HeadField**：同时开两个会互相抢鼠标和快捷键，启动时检测到会提示你。

鼠标输出只在**已校准、检测到人脸、没暂停、不在校准或测试中**时发送，并且只改变横向位置，纵向保持不变。

## 常见问题
- **一直显示 FACE LOST**：太暗、背光、离得太远，或者选错了摄像头（双击 `重新选择摄像头.bat`）。
- **提示 CAMERA FRAME IS BLACK**：摄像头的遮挡片合上了，或者被系统开关关闭了。
- **中心慢慢偏了**：按 Ctrl+Alt+C 回中；如果左右幅度也变了，就重新按 `c` 校准。
- **给别人用**：下载仓库（或复制文件夹，不需要带 `.venv`、`config.json`、`profiles/`、`logs/`），在对方电脑上运行 `1_安装依赖.bat`，再按上面的流程选摄像头、建档案、校准。参数可以共享，校准不能共享：每个人都要重新校准。
- `tools\selftest.bat`：不开窗口，跑 8 秒，输出帧率、检测率和耗时，结果在 `logs\selftest_report.json`。

## 相关项目
- [788009/infalsus-head-tracker](https://github.com/788009/infalsus-head-tracker)（MIT）：同样用 MediaPipe 和头部转动控制 In Falsus 的天键。它用鼻尖相对两侧脸颊中点的偏移作为信号，用 `pydirectinput` 注入鼠标；HeadField 的 `nose_rel` 信号和“纵坐标固定在屏幕中间的 abs 注入”参考了它的做法。

## 开发过程说明
最早尝试的是用虹膜位置做眼动控制。在笔记本自带摄像头上，眼睛从屏幕最左看到最右，虹膜位置只变化眼宽的约 7%，校准误差约为屏宽的 10%，换新点验证时达到 23%，而且对头部移动非常敏感，所以放弃了，改成头部转动控制。在同一台设备上，头部控制的验证误差约为屏宽的 1.4%。

## 开源说明
- 人脸模型 `face_landmarker.task` 来自 Google MediaPipe，由安装脚本下载，不随仓库分发。
- 本机产生的文件已写进 `.gitignore`：`.venv/`、`config.json`、`profiles/`、`logs/`、模型文件、`env_report.txt`（里面有本机用户路径和设备名）。提交前请再用 `git status` 确认一遍。
- 主要依赖：MediaPipe（Apache-2.0）、OpenCV、NumPy、pygrabber。
- 本项目使用 [MIT 许可证](LICENSE)。

## 文件结构
```
src/headfield.py   主程序：界面、参数、映射、输出控制、选摄像头和档案
src/tracker.py     摄像头线程（按名称打开、曝光设置）+ MediaPipe 头部姿态
src/headmodel.py   头部校准（左 / 中 / 右三点映射、回中）+ One Euro 滤波
src/routines.py    全屏校准 / 验证 / 跟随测试
src/winio.py       DPI 感知、鼠标注入、全局热键、进程优先级（Windows API）
tools/             安装、自检、帧率检查、鼠标注入诊断脚本
```
