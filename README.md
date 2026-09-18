# fdu-yjsxk · 复旦大学研究生选课脚本

一个用于复旦大学研究生选课系统（`yjsxk.fudan.edu.cn`）的自动抢课脚本，2026 版协议重写。

## 下载桌面版

在 [GitHub Releases](https://github.com/LanYin-coder/fdu-yjsxk-webui/releases/latest) 下载对应系统的附件：

- macOS Apple Silicon：`FDUCourseHelper-macOS-arm64.dmg`，打开后拖入“应用程序”。
- Windows 10 / 11 x64：`FDUCourseHelper-Windows-x64.zip`，完整解压后双击 `FDUCourseHelper.exe`，不需要安装 Python。
- Windows 源码启动包：`FDUCourseHelper-Windows-x64-bootstrap.zip`，完整解压后双击“启动选课助手.bat”；首次启动需要联网准备环境。

GitHub 自动提供的 `Source code` 压缩包是源码，不是安装包。登录态与个人配置仅保存在本机，不随源码或安装包发布。

## WebUI（本地控制台）

本版本附带只监听 `127.0.0.1` 的 WebUI，首页按“登录 → 选课 → 自检 → 运行 → 状态”引导操作。
各步骤在悬浮窗口内完成；首页显示下一步、已保存的登录态、课程队列和最近活动。

1. **登录**：在系统浏览器完成复旦登录，把 Cookie 请求头或 cURL 粘贴到登录窗口，点击“验证并继续选课”。
2. **选课**：按课程类别、教师、校区等筛选当前账号可选的教学班，点击加号加入队列，最多启用 10 个班次。
3. **自检**：点击“保存并开始自检”，检查登录态和课程资格。自检只查询，不提交选课；结果有效期为 5 分钟，换登录态或改课后需重新检查。
4. **运行**：自检通过后点击“通过，设置运行”，选择按时或立即运行，设置截止时间，再点击“确认启动”。此时才会开始后台抢课任务。
5. **状态**：在悬浮窗口查看等待开抢、满员重试、成功反馈或异常提示；也可打开完整记录、停止任务。任务结束不等于全部选上，最终结果以学校已选课程页面为准。

收起流程弹窗、切换页面、最小化桌面窗口或刷新浏览器不会停止后台任务，也不会删除已保存的 Cookie 和课程配置。
关闭独立桌面窗口会退出本地服务；有任务或未保存修改时会先提醒。浏览器模式请从右上角“应用设置 → 退出应用”完整退出。
任务运行时会阻止电脑自动空闲睡眠；手动睡眠、合盖、关机和断网仍会影响运行。
界面图标已随应用打包，无需连接外部图标服务。

### macOS 应用（直接点击使用）

Apple Silicon Mac（M1/M2/M3/M4 等，macOS 12.3+）可以从上面的版本下载页获取成品，或使用本地 `dist` 目录中的构建产物：

1. 打开下载的 `FDUCourseHelper-macOS-arm64.dmg`（本地构建名为 `FDU选课助手-macOS-arm64.dmg`）。
2. 将“FDU选课助手”拖入镜像内的 `Applications` 文件夹。
3. 在“应用程序”中双击“FDU选课助手”，打开独立桌面窗口。

1.2.0 新增单实例管理：重复启动同一数据目录会唤起已有窗口，不会重复运行抢课服务。
应用设置中可打开数据文件夹或退出；启动诊断日志在数据目录的 logs/app.log。
独立窗口加载失败时会尝试打开默认浏览器作为后备界面。

应用只监听本机地址，不需要安装 Python，也不会打包项目中已有的账号、Cookie 或课程配置。
应用版的数据单独保存在：

```text
~/Library/Application Support/FDU选课助手/
├── config.json   # 课程与运行配置
├── cookie.txt    # 临时登录态，权限为 0600
└── logs/         # 启动与诊断日志，自动轮换
```

当前成品是本机临时签名版本，不是 Apple 公证版本。若 macOS 首次打开时阻止运行，请在
Finder 中右键应用并选择“打开”，再确认一次。当前构建仅支持 Apple Silicon，不支持 Intel Mac。

需要重新生成应用、ZIP 和 DMG 时运行：

```bash
./scripts/build_macos_app.command
```

构建产物为 `dist/FDU选课助手.app`、`dist/FDU选课助手-macOS-arm64.zip` 和
`dist/FDU选课助手-macOS-arm64.dmg`。

### Windows 10 / 11（x64）

推荐从版本下载页获取 `FDUCourseHelper-Windows-x64.zip`。完整解压后，双击文件夹中的 `FDUCourseHelper.exe`；请保留同目录的 `_internal` 文件夹，不需要预装 Python 或管理员权限。

也提供 `FDUCourseHelper-Windows-x64-bootstrap.zip` 源码启动包，完整解压后双击“启动选课助手.bat”。
该启动包首次运行会联网下载并准备独立 Python 与依赖，后续直接双击启动即可。
数据独立保存在 %LOCALAPPDATA%\\FDUCourseHelper，更新程序不会清除 Cookie 和课程配置。

独立窗口使用 Microsoft Edge WebView2；缺少组件时会尝试使用默认浏览器。
预编译 EXE 已在 GitHub Actions 的 Windows 环境完成构建、48 项单元测试和打包后启动测试；桌面窗口仍需在实际 Windows 10 / 11 设备上验证。
重新构建可在 Windows 运行 `scripts/build_windows_app.ps1`，或手动运行项目的 GitHub Actions 工作流。
详见 [Windows 使用与构建说明](docs/Windows.md)。

### 从源码运行 WebUI

macOS 直接双击：

```text
scripts/start_webui.command
```

首次启动会在项目内创建 `.venv` 并安装依赖，随后打开
`http://127.0.0.1:8765`。也可以手动启动：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python webui.py --open
```

源码版若需要独立桌面窗口，安装 requirements-desktop.txt 后运行：

```bash
.venv/bin/pip install -r requirements-desktop.txt
.venv/bin/python webui.py --desktop
```

同一数据目录只允许一个服务实例。需要切换浏览器与桌面模式时，先在应用设置中退出旧实例。

运行窗口中的“确认启动”会向选课系统提交真实请求；按时运行会先等待计划时间。
源码运行时 `cookie.txt` 保存在项目目录；应用版保存在上面的 Application Support 目录。
两种方式都不会通过 WebUI API 返回 Cookie。

### WebUI 免钥匙串登录

WebUI 默认使用 `cookie_source=file`，不会请求访问 macOS 的 Chrome Safe Storage：

1. 点击首页“开始登录”或流程中的“登录”→“打开系统”，在 Chrome 或 Edge 完成复旦统一身份认证。
2. 按 `Option+Command+I` 打开 Chrome 开发者工具，在“网络”面板刷新选课页面。
3. 右键选课主页面请求，选择“复制”→“复制为 cURL”。
4. 回到 WebUI 粘贴并点击“验证并继续选课”。登录窗口也有可展开的复制说明。

WebUI 只从粘贴内容中提取 Cookie，请求选课首页验证登录态后写入本机 `cookie.txt`；
不会保存整段 cURL，也不会将 Cookie 通过 API 返回。

### WebUI 直接查询课程

`bjdm` 是选课系统内部用于区分**具体教学班**的班级代码，不是邀请码，也不是院系提供的
“班级群代码”。同一门课程的不同教师、时间或班号会有不同的 `bjdm`，例如
`2026202701WIAS815005.01`。提交选课时系统必须使用它，但正常使用 WebUI 不需要手填：

1. 点击“登录”，按上面的步骤导入并保存登录态。已有有效登录态时可直接选课。
2. 点击“选择课程”，WebUI 会查询并过滤当前账号可选的班次。
3. 从“政治理论课、第一外国语、专业外语、学位基础课、学位专业课、专业选修课、
   公共选修课”页签中查看课程；也可按课程/教师、开课院系、校区、时间冲突和容量筛选。
   点击右侧加号加入队列。
4. 点击“保存并开始自检”。`bjdm`、`lx` 和 `bqmc` 会随课程自动写入配置；在“队列设置”可调整顺序或停用班次。
   每次最多启用 10 门课程；到达设定时间后，这些课程会在 1 秒窗口内开始提交。

“队列设置”里的“选择课程”和空列表里的“打开课程目录”也会打开同一个课程目录，不再要求
手工填写班级代码。

课程接口返回“可见”并不代表当前学生一定有资格选择。WebUI 会额外读取学校页面使用的
`loadAllJxbKxfw.do` 教学班限制，并按年级、学生类别、院系、专业、培养方案、学位类型、
校区及开放时间等条件执行与官方 `filterDatasByXkfw` 相同的过滤。任务启动前还会重新检查
已保存队列；若旧配置含当前账号不可选的课程，会阻止启动并提示重新选择。若选课服务器
仍返回 `#bjzc5` 等永久资格拒绝，该课程会立即停止重试，不会持续发送无效请求。

查询课程、保存队列和自检都不会提交选课请求；只有在运行窗口“确认启动”后才会提交。
原有命令行及 API 的链路演练入口仍可用，它们可能提交真实请求，不属于本地模拟测试。

### 本地验证（不连接学校）

```bash
.venv/bin/python -m unittest discover -s tests -v
```

浏览器回归脚本为 `tests/webui_flow.cjs`，使用 Playwright 和本机 Chrome，拦截全部 API 并拒绝外部请求。
先在独立数据目录启动服务，再在另一个终端运行测试（通过 `NODE_PATH` 指向已安装 Playwright 的 `node_modules`）：

```bash
FDU_WEBUI_DATA_DIR=/tmp/fdu-webui-test-data .venv/bin/python webui.py --port 18878
NODE_PATH=/path/to/node_modules node tests/webui_flow.cjs
```

`FDU_WEBUI_DATA_DIR` 对源码版和应用版均有效，可用于隔离测试数据；正常使用无需设置。

### 登录态保存了什么

复旦统一身份认证成功后，选课系统使用 `_WEU` 与 `JSESSIONID` 这两个临时 Session Cookie
识别当前账号。WebUI 保存的是这段临时登录态，不保存统一身份认证的账号或密码：

- Cookie 只写入本机：源码版使用项目目录的 `cookie.txt`，应用版使用上述 Application Support
  目录；macOS 文件权限为 `0600`，Windows 使用当前用户的数据目录，项目文件也已加入 `.gitignore`。
- `csrfToken` 不是登录 Token；程序会在每次访问选课页面时重新读取，不会单独保存。
- Cookie 会因超时、退出登录或服务器重置而失效；届时重新登录并导入一次即可。
- Cookie 在有效期内等同于已登录状态，请勿发给别人，也不要提交到 Git 或网盘。

## 文件结构

仓库已按用途分目录整理：

```
fdu-yjsxk/
├── src/                  Python 源码
│   ├── grab.py           抢课脚本（自检 / 登录 / 抢课都在这里）
│   └── preselect.py      预选课脚本（拉课程列表 → 生成 config.json）
├── scripts/              Windows(.bat) + macOS(.command) 一键双击脚本
│   └── 选课助手.command / .bat   三合一菜单：1 自检 / 2 预选课 / 3 抢课
├── config.example.json   配置模板（入库，给首次使用者参考）
├── requirements.txt      Python 依赖
└── README.md
```

> `config.json`（你的实际配置）与 `cookie.txt`（登录态）在你本机的仓库根目录，
> 由脚本自动读写，已加入 `.gitignore` 不会误提交。

## 环境要求

- **Python 3.9+**（推荐 3.10 ~ 3.13，3.13 实测可用）
- 依赖包只有两个：`requests`、`browser-cookie3`，见 `requirements.txt`

```bash
pip install -r requirements.txt
```

## 快速开始

命令行脚本自己管理登录态：自动读取浏览器 Cookie 并固化到本地 `cookie.txt`（文件优先），
失效时引导现场重登。**正常使用不需要先手动登录**，只在首次使用或登录态过期时
才需要走一次第 0 步。

### 第 0 步：登录选课系统（首次使用 / Cookie 失效时）

跑 `python src/grab.py --login`（或双击选课助手选 **1 自检**，发现 Cookie 失效时按提示
选 Y 现场登录），脚本会自动用 **Edge 或 Chrome** 打开选课页：

```
http://yjsxk.fudan.edu.cn/yjsxkapp/sys/xsxkappfudan/xsxkHome/gotoChooseCourse.do
```

未登录时会自动跳到复旦统一身份认证（UIS），登录成功后自动回到选课页面。
在浏览器里完成登录后回到终端**回车确认**，脚本会自动等待 Cookie 落盘并验证保存
（写入有延迟，回车后自动重读——进度条倒计时，最多 1 分钟、约 20 次机会，**不用自己掐时间**）。

> 登录态有时效（几小时到一天）。之后自检或抢课时若提示「Cookie 已失效」，跑一次
> `--login`（或按提示选 Y 现场登录）即可，不用手动折腾。

### macOS 命令行菜单（可选）

macOS 双击 `scripts/选课助手.command` 可使用命令行菜单。Windows 使用项目根目录的
`启动选课助手.bat` 打开桌面界面；上面的 Windows 说明包含首次启动与构建方法。

菜单选项：

- **1 自检**：检查登录态与课程配置，不发任何选课请求（可反复跑）
- **2 预选课**：拉出全部课程让你挑选，自动生成 / 更新 `config.json`（每次换课都用这个）
- **3 开始抢课**：按 `config.json` 自动抢（会等到 `start_time` 再开抢）
- **0 退出**

> 建议顺序：先 1 自检 → 2 预选课 → 3 抢课。
> macOS 首次双击 `.command` 若被系统拦截，右键 →「打开」即可。
> Windows 的 `.bat` 会先准备独立运行环境，再打开桌面窗口。
> 自检若发现 Cookie 已失效，会问你是否打开浏览器重新登录，选 Y 即可。

### 命令行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 自检（不会提交任何选课请求；无登录态或已失效会引导现场登录）
python src/grab.py --dry-run

# 3. 抢课（会等到 config.json 里的 start_time 再开始）
python src/grab.py
```

## 命令行参数

```bash
python src/grab.py             # 正常跑，等到 start_time 开始
python src/grab.py --dry-run   # 环境自检，不发请求（Cookie 失效时会引导现场登录）
python src/grab.py --login     # 现场登录：自动打开浏览器 → 你登录后回车 → 自动等待 Cookie 落盘并验证保存
python src/grab.py --now       # 忽略 start_time，立即开始
python src/grab.py --probe     # 链路演练：发 1 次真实请求看服务器回什么
```

## 预选课：生成 / 更新 config.json

不想手动一条条填 `courses` / 抓 `bjdm`？可以先把课选好、自动生成 `config.json`：

双击 `scripts/选课助手.command / .bat` 并在菜单里选
**2 预选课**（或命令行运行 `python3 src/preselect.py`），脚本会：

1. 自动读取并验证登录态（失效时会引导现场登录）
2. 从选课系统拉取全部可抢类别并逐类展示（自动带对 lx / bqmc）：
   **学科专业课**（学位基础课 / 学位专业课 / 专业选修课）→
   **学位公共课**（政治理论课 / 第一外国语 / 专业外语）→
   **公共选修课**；每门含课程代码、班号、学分、教师、时间地点、校区、余量，
   已满或与已选课冲突会标出来
3. 每一类让你输入编号挑选要抢的课（可多选，**输入顺序 = 抢课优先级**，先输的先抢；
   直接回车 = 跳过该类；跨类别时先展示的类别先进入待抢列表）
4. **手工补充列表里看不到的课**（可选）：有的课因为暂时没有选课资格，上面列表里搜不到，
   但你能在 fdjwgl「教学大纲」公开查询里搜到。把它的**详情页链接**粘贴进来（形如
   `https://fdjwgl.fudan.edu.cn/manager/teaching-syllabus/open-info/1053822`），
   脚本自动解析出课程名与课程序号（= 课程代码.班号），按当前学期前缀拼好 bjdm。
   新课程会让你确认子分类（政治/一外/专业外语、学位基础/专业/选修、公共选修等）与
   排在第几顺位（默认最后）；旧 config 里已有同一门课会直接复用原条目（bjdm 早已
   抓准），不重复提问
5. 确认开抢窗口（自动取**最近一场还没开始的放号**：每天 10:00 与 13:00 两场放退课名额，
   提前 5 秒起跑、放号后 30 分钟收工；当天两场都过了就自动顺延到明天第一场 10:00。
   可直接回车接受，也可手动改成别的时间）
6. 写回 `config.json`

其他约定：

- 已存在 `config.json` 时**只重建课程和时间**，浏览器、请求间隔、满课阈值等其它设置
  原样保留；旧文件先自动备份为 `config.json.preselect.bak`（已 gitignore，不会误提交）
- 全程**只读**课程列表，不会提交任何选课请求，可放心反复运行
- 同一门课有多个班（如 `.01` / `.02`）会各占一行，可以都选上：第一个班抢到后，
  第二个班会被判定"已选过"自动跳过
- 按编号挑课覆盖三大页签全部可抢类别：学位公共课（lx=7，bqmc=1/2/3）、学科专业课
  （lx=8，bqmc=4/5/6）、公共选修课（lx=9，bqmc=9），lx / bqmc 按所在页签自动写好，
  不需要手动指定；个别暂时没有选课资格、列表里搜不到的课才用第 4 步的**手工补充**
  （粘贴链接后选子分类 1~6 / 9，或手动填 lx / bqmc）
- 手工补充会自动拼好 bjdm：`学年学期前缀 + 课程代码 + .班号`，前缀自动取自本次拉到的
  课程列表（同一年级全学期一致），不用自己抓；只有班号或前缀实在拿不到时才需要手输

## 配置

`config.json` 由**预选课**自动生成与维护，正常使用**不需要手动编辑**——
脚本每次写回前都会把旧文件备份为 `config.json.preselect.bak`（已 gitignore）。
`bjdm`（班级代码）、`lx` / `bqmc`（分类编码）、开抢窗口都由脚本自动抓好写入，
各字段含义写在文件内的 `_说明` / `_字段含义` 注释里。

少数情况才需要打开文件微调：

- `enabled: false` —— 某门课这轮不抢（例如已经选上），预选课会原样保留这个开关
- `browser` —— 从哪些浏览器读登录 Cookie，默认 `["edge", "chrome"]` 按顺序尝试
- `full_max_tries` —— 兼容旧配置的字段，固定为 `0`；满员课程不会自动放弃
- `request_interval` —— 一轮提交及结果查询结束后，到下一轮之间的等待秒数
- `poll_interval` —— 查询单笔异步选课结果的间隔，不影响首轮课程提交速度
- `serial_mode` —— 兼容旧配置的字段，应用会自动保存为 `false`

### 课满了怎么办（自动行为）

- 课程在 `courses` 里的顺序即优先级，排前面的先试（预选课里先输的编号排前面）。
- 某门课满员被拒**不会卡住后面的课**：打印一行"满员"提示后立刻试下一门。
- 满员失败的课每轮会自动**挪到本轮末尾**再试，不会因失败次数被移出队列。
- 所有启用课程会持续请求，直到选上、服务器判定当前账号不可选、手动停止或到达 `end_time`。

## 批量提交如何工作

每轮先按照课程优先级，为最多 10 门启用课程安排 `choiceCourse.do` 请求。各请求使用同一份
`_WEU`、`JSESSIONID` 和 CSRF Token，并在约 0.8 秒内依次开始发送；这个时间是本机开始调用
接口的时间，网络和学校服务器的排队时间不受程序控制，因此不能保证远端在同一秒完成处理。

所有提交请求返回后，程序保存每门课程自己的异步任务编号，再按照 `poll_interval` 查询
`loadXkjgRes.do`。查询结果不会阻塞同一轮其余课程的首次提交。浏览器刷新 WebUI 只会重新
连接本地后台任务，不会重启抢课进程或清除登录 Cookie；尚未保存的页面表单修改除外。

## 免责声明

- 本程序仅供学习交流，功能仅为辅助选课，**存在抢课失败的可能**。
- 请控制请求频率、及时停止程序，**避免给学校服务器带来过大压力**。
- 请遵守学校相关规定，使用本程序产生的任何后果由使用者自行承担。
