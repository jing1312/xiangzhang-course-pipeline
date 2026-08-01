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

- 服务：**大模型录音文件识别**（豆包录音文件识别模型2.0），资源 ID `volc.seedasr.auc`
- 接口：
  - 提交 `POST https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit`
  - 查询 `POST https://openspeech.bytedance.com/api/v3/auc/bigmodel/query`（轮询 5s 间隔）
- 认证走 **Header**（不是 body）：
  `X-Api-App-Key`（APP ID）、`X-Api-Access-Key`（Access Token）、
  `X-Api-Resource-Id: volc.seedasr.auc`、`X-Api-Request-Id: <uuid>`、`X-Api-Sequence: -1`
- Body：`user.{uid}`、`audio.{format: "mp3", url, data}`（**base64 直传**，
  单文件 ≤512MB、无时长限制）、`request.{model_name: "bigmodel", enable_itn, enable_punc, show_utterances}`
- **任务状态码在响应 Header `X-Api-Status-Code`**：`20000000`=成功，
  `20000001/20000002`=处理中，其他=失败（`X-Api-Message` 带原因）
- 音频参数：**16kHz / 单声道 / 64kbps mp3**（ffmpeg 转码后 base64）
- 结果：`result.result.text` + `utterances`（分句，时间戳单位毫秒）
- 官方文档：https://www.volcengine.com/docs/6561/152289

## MiMo 要点（备选）

- OpenAI 兼容接口：`https://token-plan-cn.xiaomimimo.com/v1/chat/completions`，模型 `mimo-v2.5-asr`
- 认证：Header `api-key`；音频走 `messages[].content` 里的 `input_audio`（`data:audio/mpeg;base64,...`）
- 加 `asr_options: {"language": "zh"}`
- 限制：单次 ≤7MB 或 ≤20 分钟，超长 ffmpeg segment 切片；音频压到 16kbps

## 讯飞踩坑（避免重蹈）

- 实时流式接口适合短句，长音频要切片和组包，逻辑复杂
- 免费并发配额低，批量转写时频繁 403
- 最终弃用，改用火山引擎豆包

## 直链过期问题

- 视频直链（manifest 里 selected.url）带签名，一段时间后失效（HTTP 401/403）
- 解决：重新抓取详情刷新直链（`01_links/rebuild_fresh_manifest.cjs`）
- 下载时携带 `Referer: <portalBase>` 头
