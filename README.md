# 香樟云课堂课程流水线

把线上教学平台的课程实录批量处理为可复习的资料，全程两条路线：

**路线 A（省空间，主线）：不下载视频，只抽音频转文本**
平台实录的视频太大（153 节约 13GB），占本地空间。所以不下载视频，
直接用 ffmpeg 从视频直链里**抽离音频**（16kHz 单声道，一节课不到 100MB），
再批量 ASR 转成文本，用于复习。

**路线 B（要 PPT）：视频进百度网盘，用网盘 AI 批量出课件**
PPT 很重要，但视频还是不想留在本地——把视频上传到百度网盘（网盘存，
本地不留），网盘 AI 对每节视频能生成课件 PPT / 讲稿 / 笔记，
用浏览器自动化（baidu-ai-batch）批量触发、批量导出。

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ 01 链接提取   │ → │ 02 音频抽取   │ → │ 03 ASR 转写   │
│ 浏览器CDP     │   │ ffmpeg 从直链 │   │ 火山豆包(主)   │
│ +接口签名     │   │ 抽音频，不下载│   │ MiMo(备选)    │
│ → 直链CSV     │   │ 视频         │   │ → 每节txt     │
└──────────────┘   └──────────────┘   └──────────────┘
        │
        ▼ 想要 PPT 时走路线 B
┌──────────────┐   ┌──────────────┐
│ 视频上传百度  │ → │ 网盘 AI 批量  │
│ 网盘(客户端, │   │ 课件/讲稿/笔记 │
│ 本地不留)     │   │ baidu-ai-batch│
└──────────────┘   └──────────────┘
```

> 路线 B 的自动化工具在独立仓库：**https://github.com/jing1312/baidu-ai-batch**

---

## 目录结构

```
xiangzhang-course-pipeline/
├── config.example.json     # 配置模板（复制为 config.json 后填写）
├── requirements.txt        # Python 依赖（requests）
├── 01_links/               # 阶段 1：链接提取
│   ├── collect_media_details.cjs   # 主脚本：抓详情、选最优视角/声道 → media-manifest.json
│   ├── rebuild_fresh_manifest.cjs  # 直链过期后刷新清单（连 CDP 重新抓）
│   ├── export_fresh_media_urls.cjs # manifest → all_fresh_media_urls.csv（10 列，带 BOM）
│   └── build_course_page_urls.cjs  # manifest → 课程实录页面 URL 清单
├── 02_download/            # 阶段 2：音频抽取（主）+ 视频下载（可选）
│   ├── extract_audio.py    # ★ 从视频直链抽音频（16kHz 单声道），不下载视频
│   ├── download_videos.py  # 可选：需要本地视频副本时才用（断点续传）
│   ├── rename_videos.py    # 可选：第一遍改名，文件ID -> 课程名_时间.mp4
│   └── rename_final.py     # 可选：第二遍改名，-> 简称_序号_课程名_时间.mp4（传网盘前用）
└── 03_asr/                 # 阶段 3：转写
    ├── batch_transcribe.py # 主：火山引擎豆包 submit/query 轮询
    └── mimo_asr_batch.py   # 备选：小米 MiMo（7MB/20 分钟自动切片）
```

---

## 环境要求

| 依赖 | 版本/说明 |
|------|-----------|
| Node.js | 18+（脚本只用内置模块 + playwright） |
| Python | 3.10+（`pip install -r requirements.txt`） |
| ffmpeg | 加入 PATH，或用环境变量 `FFMPEG_PATH` 指定路径 |
| Edge / Chrome | 用于登录平台（`01_links` 需要） |

```bash
npm install playwright
pip install -r requirements.txt
```

> `01_links` 通过 CDP 连接**已安装的 Edge**，不需要下载 playwright 自带的浏览器。

---

## 配置（config.json）

```bash
cp config.example.json config.json
```

| 字段 | 示例 | 说明 |
|------|------|------|
| `platform.portalBase` | `https://zbkt.ncu.edu.cn/...` | 教学平台入口页（登录用） |
| `platform.teachingApi` | `https://zbkt.ncu.edu.cn/teachingApi` | 后端 API 根地址 |
| `platform.signKey` | `123123` | 接口签名密钥（与平台协商值一致即可） |
| `platform.schoolYear` / `term` | `"2025-2026"` / `3` | 学年与学期，拼进实录页 URL |
| `platform.referer` | portalBase | 下载/抽音频时的 Referer 头 |
| `cdp.port` | `9222` | 调试浏览器端口 |
| `paths.*` | 见下 | 各阶段输入输出目录 |
| `courses` | `["药物分析", ...]` | 课程名列表（与平台课程目录一致） |
| `preferredView` | `studentViewFiles` | 优先选的视角（学生/老师/屏幕） |
| `download.workers` | `3` | （可选）视频下载并发数 |
| `download.minSizeBytes` | `1048576` | （可选）判定下载成功的最小字节数 |

`paths` 默认值（在仓库根目录下运行）：

- `courseItemsDir: "downloads_by_course"` — 平台课程目录接口导出的原始数据
- `manifestsDir: "media_manifests"` — 每节课的详情清单（阶段 1 产物）
- `urlsDir: "media_urls"` — 导出的直链 CSV（阶段 1 产物）

---

## 阶段 1：链接提取

### 1.1 准备登录态（一次性）

教学平台接口需要登录 Cookie，脚本通过浏览器调试端口复用已登录的浏览器：

```bash
# Windows PowerShell，用独立用户目录启动 Edge：
msedge --remote-debugging-port=9222 --user-data-dir=%LOCALAPPDATA%\edge-debug-profile
```

在打开的浏览器里登录教学平台（建议勾选「记住我」），之后保持浏览器开着。

### 1.2 抓取详情（collect_media_details.cjs）

```bash
node 01_links/collect_media_details.cjs --config config.json --courses=全部
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--config=` | `config.json` | 配置文件路径 |
| `--courses=` | 配置里的 courses | 逗号分隔的课程名，`全部` 表示所有课程 |
| `--limit=` | 0（全部） | 每门课只抓前 N 节（调试用） |
| `--cdp` | 关 | 连接 9222 调试浏览器（带登录态，推荐） |
| `--node-fetch` | 关 | 不启浏览器，直接用 Node fetch（需自行提供 Cookie） |

每个课程一个输出：`media_manifests/<课程>/media-manifest.json`，
含 `status(ok/failed)`、`base`（文件名）、`startTime`、`teacherNames`、`classroomName`、
`aiClassroomUrl`（实录页）、`detailUrl`（签名 API）、`selected`（选中的视频流）、
`mediaFiles`（三视角全部候选流）。

选流规则（`selected`）：优先 `voiceStatus==1`（有声音）→ `preferredView` 视角
→ 声道优先级 → 视角顺序（学生/老师/屏幕）→ 文件大小。

### 1.3 导出直链 CSV

```bash
node 01_links/export_fresh_media_urls.cjs --config config.json
```

输出到 `media_urls/`：

- `all_fresh_media_urls.csv` — 全部课程汇总（10 列，UTF-8 BOM + CRLF）
- `all_fresh_media_urls.txt` / `.tsv` — 直链清单 / 紧凑表格
- `by_course/<课程>/` — 每门课单独的 `fresh_media_urls.csv`、`fresh_media_urls.txt`、`filename_url.tsv`

CSV 列：`课程、文件名、上课时间、教师、教室、视角、时长秒、视频大小MB、课程实录页面URL、视频直链`

> 直链 CSV 也是路线 B 的入口：离线下载到网盘、或本地下载后上传网盘，都从这里拿 URL。

### 1.4 直链过期时刷新（rebuild_fresh_manifest.cjs）

视频直链带签名，一段时间后失效（下载报 401/403）。重新抓取详情即可刷新：

```bash
node 01_links/rebuild_fresh_manifest.cjs --config config.json --courses=药物分析 --limit=3
```

该脚本必须连 CDP（需登录态打开实录页），成功后回到 1.3 重新导出 CSV。

---

## 阶段 2：音频抽取（不下载视频）

### 2.1 抽音频（extract_audio.py，主线）

```bash
python 02_download/extract_audio.py --config config.json --courses=全部
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--config` | `config.json` | 配置文件 |
| `--courses` | 配置里的 courses | 逗号分隔课程名，`全部`=所有课程 |
| `--limit` | 0（全部） | 每门课只抽前 N 节（调试用） |
| `--overwrite` | 关 | 覆盖已存在的音频 |
| `--ffmpeg` | `FFMPEG_PATH` 或 `ffmpeg` | ffmpeg 路径 |
| `--referer` | config 里的 referer | 抽音频时的 Referer 头（缺失会 403） |
| `--out` | `transcripts/audio` | 音频输出目录（按课程分文件夹） |
| `--output-format` | `wav` | `wav`（16kHz 单声道）或 `mp3`（64k） |

行为：直接读 manifest 里的视频直链（`selected.url`）→ ffmpeg 只取音频流
（`-vn`，16kHz / 单声道），**不下载视频**。已存在且 >1KB 的自动跳过，失败记录
在 `transcripts/audio/extract-audio-errors.log`。

输出：`transcripts/audio/<课程>/<文件名>.wav`

### 2.2 可选：需要本地视频副本时

路线 A 全程不需要视频文件。只有当你想本地也留一份视频（或改好名再传网盘）时才用。
下载的文件名是平台文件 ID，需要**两步重命名**：

```bash
# ① 下载（3 并发、已存在且 >1MB 则跳过、失败重试 3 次）
python 02_download/download_videos.py --csv media_urls/all_fresh_media_urls.csv --out downloads

# ② 第一遍：文件ID（ff80...）-> 课程名_上课时间.mp4（从直链 CSV 提取映射）
python 02_download/rename_videos.py --dir downloads --csv media_urls/all_fresh_media_urls.csv

# ③ 第二遍：-> 简称_序号_课程名_上课时间.mp4（序号按上课时间排序，传网盘前用这套）
python 02_download/rename_final.py --dir downloads \
    --short-names '{"临床药理学":"临床药理","生物药剂与药物动力学":"生物药剂","天然药物化学":"天然药化"}'
```

两个改名脚本都支持 `--dry-run` 先预览。

---

## 阶段 3：ASR 转写

### 3.1 主方案：火山引擎豆包（batch_transcribe.py）

开通[火山引擎-大模型录音文件识别](https://www.volcengine.com/docs/6561/152289)后，
在控制台拿 `APP ID` 和 `Access Token`，通过环境变量注入（不落盘）：

```bash
export VOLC_APP_ID=<你的appId>
export VOLC_ACCESS_TOKEN=<你的token>

# 方式 A：直接吃阶段1的直链 CSV（ffmpeg 现场抽音频，原版流程）
python 03_asr/batch_transcribe.py --csv media_urls/all_fresh_media_urls.csv --out transcripts

# 方式 B：吃阶段2抽好的本地音频
python 03_asr/batch_transcribe.py --dir transcripts/audio/临床药理学 --out transcripts
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--csv` | 无 | 直链 CSV（`media_urls/all_fresh_media_urls.csv`），与 `--dir` 二选一 |
| `--dir` | 无 | 本地音频/视频目录（阶段 2 的 wav 直接可用） |
| `--out` | `transcripts` | 每节课一个 txt（按课程分文件夹） |
| `--ffmpeg` | `FFMPEG_PATH` 或 `ffmpeg` | ffmpeg 路径 |
| `--referer` | portalBase | 从直链抽音频时的 Referer |
| `--filter` | 全部 | 只转写文件名/课程名含该关键词的 |
| `--limit` | 0（全部） | 只转写前 N 个 |
| `--skip-existing` | 关 | 已有非空 txt 则跳过（断点续传） |
| `--config` | 无 | 也可从 config.json 的 `asr.volc` 读密钥/ffmpeg 路径 |

内部流程：音频 → ffmpeg 转 16kHz/单声道/64k mp3（已是 wav 则直接用）→ base64 →
`submit`（Header 认证，`X-Api-Resource-Id: volc.seedasr.auc`）→ 轮询 `query`
（5 秒间隔，最长 10 分钟）→ 文本 + 分句详情写入 `<课程>/<文件名>.txt`。

> 若返回「任务失败」或一直无结果，通常是控制台里开通的产品与代码中
> `cluster` / `model.app_name` 不一致，按实际开通项调整（见 docs）。

### 3.2 备选：小米 MiMo（mimo_asr_batch.py）

```bash
export MIMO_API_KEY=<你的key>
python 03_asr/mimo_asr_batch.py --csv media_urls/all_fresh_media_urls.csv --out transcripts_mimo
# 或吃本地音频：--dir transcripts/audio/临床药理学
```

限制：单次提交音频 ≤7MB 或 ≤20 分钟，超长自动切片（16kbps 压缩）后合并结果；
自带静音课检测（音量过低直接跳过）。

### 3.3 方案对比

| 方案 | 结论 | 说明 |
|------|------|------|
| 讯飞语音听写 | 弃用 | 并发受限、长音频需切片组包 |
| 阿里云录音文件识别 | 可用 | 慢，排队久 |
| **火山引擎豆包** | **主方案** | 提交式、支持长音频、快、免费额度大 |
| 小米 MiMo | 备选 | 有 7MB/20 分钟限制 |

详见 [docs/ASR-方案对比与踩坑.md](docs/ASR-方案对比与踩坑.md)。

---

## 阶段 4：百度网盘 + 网盘 AI 产出（路线 B）

**前提**：视频要进网盘，但不用占本地空间。

### 4.1 视频进网盘

- 用阶段 1.3 的直链 CSV（`media_urls/all_fresh_media_urls.csv`）拿全部视频 URL
- 百度网盘客户端 → 离线下载 → 新建链接任务 → 粘贴 URL 列表
- 实测平台域名是内网地址（`*.ncu.edu.cn`），**网盘离线下载不保证可用**；
  不可用就换：本地批量下载（`02_download/download_videos.py`）→
  改名（`rename_videos.py`）→ 网盘客户端拖拽上传 → **传完本地删掉**，
  视频只在网盘里
- 传完记得清理网盘里重复的原始名文件，只留「简称_序号_课程名_时间.mp4」

### 4.2 网盘 AI 批量产出（baidu-ai-batch）

网盘 AI 对每节视频的课件 / 讲稿 / 笔记是**按需生成**的：要挨个打开视频 →
切到「课件/文稿/笔记」标签 → 触发 AI 生成 → 等服务端跑完 → 再导出。
153 节手动点要一整天。

用 [baidu-ai-batch](https://github.com/jing1312/baidu-ai-batch) 自动化：

```bash
# 在 baidu-ai-batch 仓库里：
node bin/export-ppt.cjs          # ① 批量导出 AI 课件 PPT（存回网盘视频目录）
node bin/extract-manuscript.cjs  # ② 批量提取 AI 讲稿 → 本地 TXT
node bin/export-notes.cjs        # ③ 批量生成并导出 AI 笔记（PDF 存网盘 + 本地 TXT）
```

实际战果（原会话）：4 门课 153 节视频，自动导出 **146 份 AI 课件 PPT、
122 份讲稿、55+ 份笔记**，全程无人值守。

---

## 常见问题

| 现象 | 处理 |
|------|------|
| 抽音频/下载报 401/403 | 直链过期，跑 `rebuild_fresh_manifest.cjs` 后重新导出 CSV |
| CDP 连不上 | 确认 Edge 以 `--remote-debugging-port=9222` 启动；浏览器页面保持打开 |
| 页面提示未登录 | 在调试浏览器里重新登录平台 |
| `validCode` 不匹配 | 确认 `config.json` 的 `signKey` 与平台一致 |
| ASR 任务失败/无结果 | 检查密钥与资源 ID（`volc.seedasr.auc`）；任务状态码在响应 Header `X-Api-Status-Code`，非 `20000000` 看 `X-Api-Message` |
| ffmpeg 找不到 | 把 ffmpeg 加入 PATH，或设 `FFMPEG_PATH` 指向可执行文件 |
| 网盘离线下载链接无效 | 平台 URL 是内网地址，改走「本地下载 → 上传网盘 → 本地删」 |
| 网盘里文件重复 | 下载/上传两次会产生原始名+改名两套文件，手动删原始名那套 |

---

## 数据流向

```
平台课程目录接口 ──> downloads_by_course/<课程>/video-items.json   （课程列表）
        │  collect_media_details.cjs
        ▼
media_manifests/<课程>/media-manifest.json                        （详情+直链）
        │  export_fresh_media_urls.cjs
        ▼
media_urls/all_fresh_media_urls.csv
        │                                   ┌─ 路线 B：离线下载/本地下载 ─> 百度网盘
        │  extract_audio.py（ffmpeg 从直链  │        │  baidu-ai-batch
        ▼  只取音频流，不下载视频）          ▼        ▼
transcripts/audio/<课程>/*.wav  ──> 03_asr ──> transcripts/*.txt（复习用）
                                            └─ 网盘 AI 课件/讲稿/笔记（在网盘侧）
```
