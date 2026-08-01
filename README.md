# 香樟云课堂课程流水线

把线上教学平台的课程实录批量处理为可下载的视频与转写文本，共四步：

1. **链接提取**：登录平台，抓取每节课的实录详情，导出视频直链 CSV
2. **下载与整理**：按 CSV 并行下载视频，统一命名，抽取音频
3. **ASR 转写**：火山引擎豆包（主）/ 小米 MiMo（备选）批量转写为文本
4. **AI 产出**：把转写文本交给百度网盘 AI 生成 PPT / 讲义 / 笔记

> 第 4 步在独立仓库：**https://github.com/jing1312/baidu-ai-batch**
> 本流水线 `transcripts/` 的输出可直接作为它的输入。

```
┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│ 01 链接提取   │ → │ 02 下载与整理 │ → │ 03 ASR 转写   │ → │ 04 AI 产出    │
│ 浏览器CDP     │   │ 并行下载/重命名│   │ 火山豆包(主)   │   │ baidu-ai-    │
│ +接口签名     │   │ /抽取音频     │   │ MiMo(备选)    │   │ batch 仓库   │
└──────────────┘   └──────────────┘   └──────────────┘   └──────────────┘
```

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
├── 02_download/            # 阶段 2：下载与整理
│   ├── download_videos.py  # 按 CSV 并行下载（3 并发、>1MB 判定成功、断点续传）
│   ├── rename_videos.py    # 重命名为「简称_序号_课程名_时间.mp4」
│   ├── add_prefix.py       # 备选：只加「简称_序号_」前缀，保留原名
│   └── extract_audio.py    # ffmpeg 抽 16kHz 单声道 wav/mp3（带 Referer）
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
| `platform.referer` | portalBase | 下载视频时的 Referer 头 |
| `cdp.port` | `9222` | 调试浏览器端口 |
| `paths.*` | 见下 | 各阶段输入输出目录 |
| `courses` | `["药物分析", ...]` | 课程名列表（与平台课程目录一致） |
| `preferredView` | `studentViewFiles` | 优先选的视角（学生/老师/屏幕） |
| `download.workers` | `3` | 下载并发数 |
| `download.minSizeBytes` | `1048576` | 判定下载成功的最小字节数 |

`paths` 默认值（在仓库根目录下运行）：

- `courseItemsDir: "downloads_by_course"` — 平台课程目录接口导出的原始数据
- `manifestsDir: "media_manifests"` — 每节课的详情清单（阶段 1 产物）
- `urlsDir: "media_urls"` — 导出的直链 CSV（阶段 1 产物）
- `downloadDir: "downloads"` — 视频下载目录（阶段 2 产物）

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

### 1.4 生成实录页面 URL 清单（可选）

```bash
node 01_links/build_course_page_urls.cjs --config config.json
```

输出 `course_page_urls/`，方便在浏览器里逐课打开实录页核对。

### 1.5 直链过期时刷新（rebuild_fresh_manifest.cjs）

视频直链带签名，一段时间后失效（下载报 401/403）。重新抓取详情即可刷新：

```bash
node 01_links/rebuild_fresh_manifest.cjs --config config.json --courses=药物分析 --limit=3
```

该脚本必须连 CDP（需登录态打开实录页），成功后回到 1.3 重新导出 CSV。

---

## 阶段 2：下载与整理

### 2.1 下载（download_videos.py）

```bash
python 02_download/download_videos.py --csv media_urls/all_fresh_media_urls.csv --out downloads
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--csv` | `media_urls/all_fresh_media_urls.csv` | 阶段 1 导出的 CSV |
| `--out` | `downloads` | 输出目录（按课程分文件夹） |
| `--workers` | `3` | 并行下载数 |
| `--min-size` | `1048576` | 判定成功的最小字节数 |
| `--course` | 全部 | 只下载某门课 |

行为：目标文件已存在且大于 `--min-size` 则跳过（断点续传）；失败自动重试 3 次。

### 2.2 重命名

```bash
# 方案 A：标准命名「简称_序号_课程名_时间.mp4」，序号按上课时间排序
python 02_download/rename_videos.py --dir downloads \
    --short-names '{"临床药理学":"临床药理","生物药剂与药物动力学":"生物药剂","天然药物化学":"天然药化"}'

# 方案 B：只加前缀「简称_序号_」保留原文件名
python 02_download/add_prefix.py --dir downloads \
    --short-names '{"临床药理学":"临床药理"}'
```

两个脚本都支持 `--dry-run` 先预览，确认无误再去掉。

### 2.3 抽取音频（extract_audio.py）

```bash
python 02_download/extract_audio.py --dir downloads --out transcripts/audio --ffmpeg ffmpeg
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--dir` | 必填 | 视频目录（递归找 mp4/mkv/flv） |
| `--out` | `transcripts/audio` | WAV 输出目录 |
| `--ffmpeg` | `FFMPEG_PATH` 或 `ffmpeg` | ffmpeg 路径 |
| `--referer` | portalBase | 下载时的 Referer 头（缺失会 403） |
| `--filter` | 全部 | 只处理文件名含该关键词的 |
| `--limit` | 0（全部） | 只处理前 N 个 |
| `--overwrite` | 关 | 覆盖已存在的 |
| `--output-format` | `wav` | `wav`（16kHz 单声道）或 `mp3`（64k） |

> 阶段 3 会自动调用 ffmpeg 抽音频，这步可跳过；单独抽出 wav 便于先人工试听或换别的 ASR。

---

## 阶段 3：ASR 转写

### 3.1 主方案：火山引擎豆包（batch_transcribe.py）

开通[火山引擎语音技术-大模型语音识别](https://www.volcengine.com/docs/6561/152289)后，
在控制台拿 `AppID` 和 `Access Token`，通过环境变量注入（不落盘）：

```bash
export VOLC_APP_ID=<你的appId>
export VOLC_ACCESS_TOKEN=<你的token>

python 03_asr/batch_transcribe.py --dir downloads/临床药理学 --out transcripts --ffmpeg ffmpeg
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--dir` | 必填 | 视频/音频目录 |
| `--out` | `transcripts` | 每节课一个 txt |
| `--ffmpeg` | `FFMPEG_PATH` 或 `ffmpeg` | ffmpeg 路径 |
| `--referer` | portalBase | 抽音频时的 Referer |
| `--filter` | 全部 | 只转写文件名含该关键词的 |
| `--limit` | 0（全部） | 只转写前 N 个 |
| `--skip-existing` | 关 | 已有非空 txt 则跳过（断点续传） |
| `--config` | 无 | 也可从 config.json 的 `asr.volc` 读密钥/ffmpeg 路径 |

内部流程：视频 → ffmpeg 抽 16kHz/单声道/64k mp3 → base64 → `submit` → 轮询 `query`
（3 秒间隔，最长 30 分钟）→ 文本写入 `<课程>/<文件名>.txt`。

> 若返回「任务失败」或一直无结果，通常是控制台里开通的产品与代码中
> `cluster` / `model.app_name` 不一致，按实际开通项调整（见 docs）。

### 3.2 备选：小米 MiMo（mimo_asr_batch.py）

```bash
export MIMO_API_KEY=<你的key>
python 03_asr/mimo_asr_batch.py --dir downloads/临床药理学 --out transcripts_mimo
```

限制：单次提交音频 ≤7MB 或 ≤20 分钟，超长自动切片后合并结果。

### 3.3 方案对比

| 方案 | 结论 | 说明 |
|------|------|------|
| 讯飞语音听写 | 弃用 | 并发受限、长音频需切片组包 |
| 阿里云录音文件识别 | 可用 | 慢，排队久 |
| **火山引擎豆包** | **主方案** | 提交式、支持长音频、快、免费额度大 |
| 小米 MiMo | 备选 | 有 7MB/20 分钟限制 |

详见 [docs/ASR-方案对比与踩坑.md](docs/ASR-方案对比与踩坑.md)。

---

## 阶段 4：AI 产出

把 `transcripts/` 里的 txt 交给 [baidu-ai-batch](https://github.com/jing1312/baidu-ai-batch)
批量生成 PPT / 讲义 / 复习笔记。

---

## 常见问题

| 现象 | 处理 |
|------|------|
| 下载报 401/403 | 直链过期，跑 `rebuild_fresh_manifest.cjs` 后重新导出 CSV |
| CDP 连不上 | 确认 Edge 以 `--remote-debugging-port=9222` 启动；浏览器页面保持打开 |
| 页面提示未登录 | 在调试浏览器里重新登录平台 |
| `validCode` 不匹配 | 确认 `config.json` 的 `signKey` 与平台一致 |
| ASR 任务失败/无结果 | 控制台开通的产品与 `cluster`/`model.app_name` 不符，按 docs 调整 |
| ffmpeg 找不到 | 把 ffmpeg 加入 PATH，或设 `FFMPEG_PATH` 指向可执行文件 |

---

## 数据流向

```
平台课程目录接口 ──> downloads_by_course/<课程>/video-items.json   （课程列表）
        │  collect_media_details.cjs
        ▼
media_manifests/<课程>/media-manifest.json                        （详情+直链）
        │  export_fresh_media_urls.cjs
        ▼
media_urls/all_fresh_media_urls.csv   ──> download_videos.py ──> downloads/<课程>/*.mp4
                                                                    │  rename_videos.py
                                                                    ▼
                                                               downloads/<课程>/简称_序号_*.mp4
                                                                    │  extract_audio.py / batch_transcribe.py 内置抽取
                                                                    ▼
                                                               transcripts/<课程>/*.txt  ──> baidu-ai-batch
```
