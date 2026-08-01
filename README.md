# 香樟云课堂课程流水线（xiangzhang-course-pipeline）

把「香樟云课堂」线上教学平台的课程实录批量提取成：视频直链 → 下载 → 音频转写为文本。
从 [这个 opencode 会话](https://github.com/jing1312) 里整理出的可复用版本（已参数化、密钥脱敏）。

> 第 4 步（把转写文本丢给百度网盘 AI 生成 PPT / 讲义 / 笔记）在独立的仓库：
> **https://github.com/jing1312/baidu-ai-batch**（本流水线产物可直接作为它的输入）。

## 流水线总览

```
┌─────────────────────┐   ┌─────────────────────┐   ┌─────────────────────┐   ┌─────────────────────┐
│ 01 链接提取          │ → │ 02 下载与整理        │ → │ 03 ASR 转写          │ → │ 04 百度网盘 AI 产出   │
│ 浏览器CDP+接口签名    │   │ 并行下载/重命名/抽音  │   │ 火山引擎豆包(主)      │   │ baidu-ai-batch 仓库  │
│ 课程实录页 → CSV 直链 │   │ 视频存到 downloads/  │   │ 小米MiMo(备选)       │   │ PPT/讲义/笔记       │
└─────────────────────┘   └─────────────────────┘   └─────────────────────┘   └─────────────────────┘
```

## 目录结构

```
xiangzhang-course-pipeline/
├── config.example.json     # 配置模板（复制为 config.json 后填写）
├── requirements.txt        # Python 依赖
├── 01_links/
│   ├── collect_media_details.cjs   # (主) 抓详情，选最优视角/声道，生成 media-manifest.json
│   ├── rebuild_fresh_manifest.cjs  # (刷新) 直链过期后重抓 fresh 清单
│   ├── export_fresh_media_urls.cjs # manifest → all_fresh_media_urls.csv(10列,带BOM+CRLF)
│   └── build_course_page_urls.cjs  # manifest → 课程实录页面URL清单(打开页面用)
├── 02_download/
│   ├── download_videos.py  # 按CSV并行下载(3 workers, >1MB判定成功,断点续传)
│   ├── rename_videos.py    # 重命名为「简称_序号_课程名_时间.mp4」
│   ├── add_prefix.py       # (备选)只加「简称_序号_」前缀保留原名
│   └── extract_audio.py    # ffmpeg 抽 16kHz 单声道 wav/mp3(带Referer头)
└── 03_asr/
    ├── batch_transcribe.py # (主) 火山引擎豆包ASR: submit/query轮询
    └── mimo_asr_batch.py   # (备选) 小米MiMo: 7MB/20min自动切片
```

## 快速开始

### 0. 准备

```bash
npm install playwright          # Node 部分依赖
pip install -r requirements.txt # Python 部分依赖
cp config.example.json config.json   # 填入 signKey、课程列表等
```

### 1. 链接提取（01_links）

教学平台接口需要登录态 + 签名（`validCode = md5("id=<视频id>&signKey=<signKey>")`），
所以用浏览器调试端口带登录态抓取。推荐流程：

```bash
# 1a. 用调试端口启动 Edge，并登录教学平台（一次性）：
msedge --remote-debugging-port=9222 --user-data-dir=C:\edge-debug

# 1b. 抓取详情清单（默认选「学生视角+有声音」，直链即视频URL）：
node 01_links/collect_media_details.cjs --config config.json --courses=全部

# 1c. 导出 CSV 直链（输出 media_urls/all_fresh_media_urls.csv）：
node 01_links/export_fresh_media_urls.cjs --config config.json

# 可选：直链过期（401/403）时刷新清单：
node 01_links/rebuild_fresh_manifest.cjs --config config.json --courses=药物分析 --limit=3
```

### 2. 下载与整理（02_download）

```bash
python 02_download/download_videos.py --csv media_urls/all_fresh_media_urls.csv --out downloads
python 02_download/rename_videos.py --dir downloads --short-names '{"临床药理学":"临床药理"}'
# 或只加前缀：python 02_download/add_prefix.py --dir downloads --short-names '{"临床药理学":"临床药理"}'
```

### 3. ASR 转写（03_asr）

```bash
# 主方案：火山引擎豆包（推荐，免费额度大，中文效果好）
export VOLC_APP_ID=<你的appId>        # 或在 config.json -> asr.volc 填入
export VOLC_ACCESS_TOKEN=<你的token>
python 03_asr/batch_transcribe.py --dir downloads/临床药理学 --out transcripts --ffmpeg ffmpeg

# 备选：小米 MiMo
export MIMO_API_KEY=<你的key>
python 03_asr/mimo_asr_batch.py --dir downloads/临床药理学 --out transcripts_mimo
```

### 4. 产出（另一个仓库）

把 `transcripts/` 的 txt 交给 [baidu-ai-batch](https://github.com/jing1312/baidu-ai-batch)
批量生成 PPT / 讲义 / 复习笔记。

## 参考：原会话与数据

- 本仓库整理自 opencode 会话 `ses_1790c524effeU0oAxiIlwFtJMa`「解决课程音频转写速度慢问题」。
- 原实验脚本在 `_automation/xiangzhang_export/`（含大量一次性/调试脚本，不在此仓库）。
- 各方案效果：讯飞（踩坑：并发/时长限制）→ 阿里云（可用）→ **火山引擎豆包（最终方案，最快）** → 小米 MiMo（探索）。
