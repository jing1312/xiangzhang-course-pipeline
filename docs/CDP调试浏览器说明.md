# 调试浏览器 + 登录态说明

教学平台（zbkt.ncu.edu.cn）的接口需要登录 cookie，
脚本通过浏览器 CDP 连接已登录的 Edge/Chrome 复用会话。

## 一次性启动（Windows）

```powershell
# 用独立用户数据目录启动（避免干扰日常浏览器）
msedge --remote-debugging-port=9222 --user-data-dir=C:\edge-debug-profile
```

打开浏览器手动登录教学平台（建议勾选「记住我」），保持浏览器开着。

## 脚本如何连上

- `collect_media_details.cjs`：默认 `launchPersistentContext` 以无头 Edge 打开 portalBase 首页，
  若配置了 `cdp.port`（或传 `--cdp`）则改连已打开的调试端口，直接复用登录态。
- `rebuild_fresh_manifest.cjs`：必须 `--cdp`（需要带登录态打开实录页）。
- 端口：`config.json` → `cdp.port`（默认 9222）。

## 无头模式兜底

不传 `--cdp` 时脚本尝试无头打开首页。若平台强制登录校验（如滑块验证），
仍建议走 CDP 方案。
