# ASR 方案对比与踩坑

本文记录批量转写课程音频时实测过的各方案对比。

## 结论

| 方案 | 结果 | 备注 |
|------|------|------|
| 讯飞语音听写 | ❌ 弃用 | 并发受限、长音频要分段、格式限制多，踩坑耗时 |
| 阿里云录音文件识别 | ⚠️ 可用 | 能用但慢，排队久 |
| **火山引擎豆包（AUC BigModel）** | ✅ **最终方案** | 提交式（submit/query），支持长音频，速度快，免费额度大 |
| 小米 MiMo ASR | 📝 备选 | 每段 ≤7MB 或 ≤20 分钟，需自行切片 |
| 本地 FunASR | 📝 可选 | 服务器方案，无需联网但需 GPU |

## 火山引擎豆包要点

- 接口：
  - 提交 `POST https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit`
  - 查询 `POST https://openspeech.bytedance.com/api/v3/auc/bigmodel/query`（轮询，3s 间隔）
- 请求体关键字段：`app.{appid,token,cluster}`、`request.{reqid,audio{format,sample_rate,bits,channel},model{app_name}}`、`audio_data`（base64）
- 音频参数：**16kHz / 单声道 / 64kbps mp3**（ffmpeg 转码）
- `cluster` 用 `volcengine_streaming_common`；模型 `app_name` 按控制台开通的产品调整
- 查询响应：`status==2` 表示完成（结果在 `data.result`），`status==3` 表示失败
- 官方文档：https://www.volcengine.com/docs/6561/152289

## 讯飞踩坑（避免重蹈）

- 实时流式接口适合短句，长音频要切片和组包，逻辑复杂
- 免费并发配额低，批量转写时频繁 403
- 最终弃用，改用火山引擎豆包

## 直链过期问题

- 视频直链（manifest 里 selected.url）带签名，一段时间后失效（HTTP 401/403）
- 解决：重新抓取详情刷新直链（`01_links/rebuild_fresh_manifest.cjs`）
- 下载时携带 `Referer: <portalBase>` 头
