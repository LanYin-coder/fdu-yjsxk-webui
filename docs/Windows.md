# Windows 版使用说明

支持 Windows 10 / 11 x64，不需要管理员权限。

## 预编译桌面版（推荐）

1. 从 [GitHub Releases](https://github.com/LanYin-coder/fdu-yjsxk-webui/releases/latest) 下载 `FDUCourseHelper-Windows-x64.zip`。
2. 完整解压，打开 `FDUCourseHelper` 文件夹。
3. 双击 `FDUCourseHelper.exe`，按“登录 → 选课 → 自检 → 运行”操作。

不需要安装 Python，请保留同目录的 `_internal` 文件夹。程序使用 WebView2 显示独立窗口，缺少组件时尝试打开默认浏览器。
EXE 已通过 Windows 云端构建、48 项单元测试和打包后启动测试（单实例、端口切换、后台任务、中文日志、退出与登录态保存）；尚未手工验证实际 Windows 10 / 11 桌面窗口。

## 源码一键启动包

1. 完整解压 FDUCourseHelper-Windows-x64-bootstrap.zip，不要在压缩包里直接运行。
2. 双击“启动选课助手.bat”。
3. 首次启动联网下载官方 uv、独立 Python 3.12 和应用依赖，通常需要几分钟。
4. 出现独立窗口后，按“登录 → 选课 → 自检 → 运行”操作。之后再双击同一个启动入口即可。

不需要预先安装 Python，不修改系统 Python，也不会写入系统目录。首次安装依赖需要能访问 GitHub、Python 运行时下载源和 PyPI。下载失败时显示原因，再次运行会重试。
该包包含应用源码与启动器，首次联网准备运行环境；它不是预编译的单文件 EXE。

窗口使用 Windows 的 Microsoft Edge WebView2 Runtime。Windows 11 通常已包含；若本机缺少它，程序会尝试打开默认浏览器作为后备界面。要使用独立窗口，可从微软官方安装 WebView2：
https://developer.microsoft.com/microsoft-edge/webview2/#download-section

## 预编译 EXE 的构建

在 Windows 安装 Python 3.10+ 后，从 PowerShell 运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_windows_app.ps1
```

构建产物为 dist/FDUCourseHelper-Windows-x64.zip。
解压后双击文件夹中的 FDUCourseHelper.exe；请保留同目录的 _internal 文件夹。
项目也提供 GitHub Actions 的 Build desktop apps 工作流，可手动构建 Mac 和 Windows 成品；默认只保存构建附件，不发布 Release。

## 数据和退出

登录态、课程设置与日志均保存在：
`%LOCALAPPDATA%\FDUCourseHelper\`

更新或重新解压应用不会删除这些数据。右上角“应用设置”可以打开数据文件夹。
登录步骤中，在 Edge / Chrome 按 F12 或 Ctrl+Shift+I 打开网络面板，复制请求为 cURL（bash）后粘贴。无需填写系统密码或访问 macOS 钥匙串。

最小化窗口会继续运行任务。关闭独立窗口或点击“退出应用”会停止本地服务；有任务或未保存修改时会提示确认。
浏览器后备模式下，关闭网页不等于退出服务，请通过“应用设置 → 退出应用”完整退出。
任务运行时会防止自动空闲睡眠；手动睡眠、关机、合盖与断网仍会影响任务。

## 遇到问题

- 首次安装失败：确认网络能够访问上述依赖来源，再双击启动器重试。
- 窗口空白：更新微软 WebView2；也可使用运行时在浏览器打开的后备页面。
- 重复双击：正常会激活原来的窗口，不会创建第二个抢课服务。
- 启动报错：查看数据目录下 logs/app.log、launcher-error.log。分享日志前请检查其中是否包含个人课程信息。
- Cookie 过期：在学校系统重新登录并重新导入，程序不能延长学校 Session 的有效期。
