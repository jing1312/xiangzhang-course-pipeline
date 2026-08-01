'use strict';
/**
 * 01_links/collect_media_details.cjs
 *
 * 从「课程实录页面」抓取每节课的媒体详情（老师/学生/屏幕三个视角的视频流），
 * 按「有声音 + 视角优先级 + 文件大小」选出最佳视频流，输出 media-manifest.json。
 *
 * 三种取数模式：
 *   --cdp          连接已登录的调试浏览器（Edge/Chrome --remote-debugging-port=9222），
 *                  优先读 Vue 组件内的 videoDetailObj，失败再走页面内 fetch
 *   --node-fetch   直接 Node fetch（需要浏览器 Cookie，通常用于已有会话可直连的场景）
 *   （默认）        launchPersistentContext 启动 headless Edge，用持久化会话目录登录
 *
 * 用法：
 *   node collect_media_details.cjs --config config.json --cdp --courses=药物分析,临床药理学
 *   node collect_media_details.cjs --config config.json --cdp --courses=全部 --limit=5
 */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { chromium } = require('playwright');

function loadConfig(configPath) {
  const file = configPath || process.env.PIPELINE_CONFIG || path.join(process.cwd(), 'config.json');
  if (!fs.existsSync(file)) {
    throw new Error(`找不到配置文件: ${file}（先 cp config.example.json config.json）`);
  }
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function sanitize(value, fallback = '未命名') {
  return (
    String(value || fallback)
      .replace(/[\\/:*?"<>|]+/g, ' ')
      .replace(/[\u0000-\u001f]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 150)
      .replace(/[. ]+$/g, '') || fallback
  );
}

function coursesFromArgs(cfg) {
  const arg = process.argv.find((item) => item.startsWith('--courses='));
  if (!arg) return cfg.courses || [];
  const value = arg.slice('--courses='.length).trim();
  if (value === '全部') return cfg.courses || [];
  return value.split(',').map((item) => item.trim()).filter(Boolean);
}

function limitFromArgs() {
  const arg = process.argv.find((item) => item.startsWith('--limit='));
  if (!arg) return 0;
  const value = Number(arg.slice('--limit='.length));
  return Number.isFinite(value) ? value : 0;
}

function devtoolsWsUrl(cfg) {
  const envWs = process.env.CDP_WS_URL;
  if (envWs) return envWs;
  const port = process.env.CDP_PORT || cfg.cdp?.port || '9222';
  return `ws://127.0.0.1:${port}/devtools/browser/`;
}

function itemBaseName(item, courseLabel) {
  return sanitize(`${item.courseName || courseLabel}_${item.startTime || item.videoInfoName || item.id}`);
}

function aiClassroomUrl(cfg, item) {
  const url = new URL(cfg.platform.portalBase);
  const params = new URLSearchParams({
    videoId: item.id,
    courseTableId: item.courseTableId,
    schoolYear: item.schoolYear || cfg.platform.schoolYear || '2025-2026',
    term: String(item.term ?? cfg.platform.term ?? 3),
    mouth: String(item.week ?? ''),
    latestWatchTime: '0',
  });
  url.hash = `#/aiClassroom/aiClassroom.htm?${params.toString()}`;
  return url.toString();
}

function recordVideoValidCode(cfg, videoId) {
  return crypto
    .createHash('md5')
    .update(`id=${videoId}&signKey=${cfg.platform.signKey}`)
    .digest('hex');
}

function detailUrlFor(cfg, videoId) {
  const validCode = recordVideoValidCode(cfg, videoId);
  const api = cfg.platform.teachingApi.replace(/\/+$/, '');
  return `${api}/v1/recordvideo/${encodeURIComponent(videoId)}?validCode=${encodeURIComponent(validCode)}`;
}

function joinMediaUrl(host, storePath) {
  if (!host || !storePath) return '';
  const normalizedHost = String(host).replace(/\/+$/g, '');
  const normalizedPath = String(storePath).startsWith('/') ? storePath : `/${storePath}`;
  return `${normalizedHost}${normalizedPath}`;
}

function flattenMediaFiles(detail) {
  const groups = [
    ['teacherViewFiles', '老师'],
    ['studentViewFiles', '学生'],
    ['vgaViewFiles', '屏幕'],
  ];
  const files = [];
  for (const [key, viewName] of groups) {
    for (const file of detail?.[key] || []) {
      const voiceStatus = Number(file.voiceStaus ?? file.voiceStatus ?? file.voice_status ?? 0);
      const url = joinMediaUrl(file.outerIp || file.innerIp || detail.outerIp || detail.innerIp, file.videoStorePath);
      files.push({
        viewType: key,
        viewName,
        recordDeviceName: file.recordDeviceName || viewName,
        id: file.id || '',
        subRecordVideoId: file.subRecordVideoId || '',
        voiceStatus,
        voicePriority: Number(file.voicePriority ?? 0),
        fileLength: Number(file.fileLength || 0),
        videoFileSize: Number(file.videoFileSize || 0),
        startTime: file.startTime || '',
        endTime: file.endTime || '',
        url,
        videoStorePath: file.videoStorePath || '',
      });
    }
  }
  return files.filter((file) => file.url);
}

function chooseMedia(files, preferredView) {
  const order = { studentViewFiles: 3, teacherViewFiles: 2, vgaViewFiles: 1 };
  const ranked = [...files].sort((a, b) => {
    const aVoice = a.voiceStatus === 1 ? 1 : 0;
    const bVoice = b.voiceStatus === 1 ? 1 : 0;
    if (aVoice !== bVoice) return bVoice - aVoice;
    if (preferredView) {
      const aPref = a.viewType === preferredView ? 1 : 0;
      const bPref = b.viewType === preferredView ? 1 : 0;
      if (aPref !== bPref) return bPref - aPref;
    }
    if (a.voicePriority !== b.voicePriority) return b.voicePriority - a.voicePriority;
    if ((order[a.viewType] || 0) !== (order[b.viewType] || 0)) {
      return (order[b.viewType] || 0) - (order[a.viewType] || 0);
    }
    return (b.videoFileSize || 0) - (a.videoFileSize || 0);
  });
  return ranked[0] || null;
}

async function createFetcher(cfg) {
  if (process.argv.includes('--cdp')) {
    const browser = await chromium.connectOverCDP(devtoolsWsUrl(cfg), { timeout: 120000 });
    const context = browser.contexts()[0] || (await browser.newContext());

    const portalHost = new URL(cfg.platform.portalBase).host;
    let page = context.pages().find((candidate) => {
      try {
        return new RegExp(portalHost.replace(/\./g, '\\.')).test(candidate.url());
      } catch (e) {
        return false;
      }
    });

    if (!page) {
      page = await context.newPage();
      await page
        .goto(cfg.platform.portalBase + '#/teacherVideoResource.htm', {
          waitUntil: 'domcontentloaded',
          timeout: 90000,
        })
        .catch(() => {});
    }
    await page.waitForTimeout(1500);
    return {
      close: async () => browser.close().catch(() => {}),
      fetchRecordDetail: async (pageUrl, apiUrl) => {
        await page.goto(pageUrl, { waitUntil: 'domcontentloaded', timeout: 90000 });
        await page.waitForTimeout(2000);
        await page.reload({ waitUntil: 'domcontentloaded', timeout: 90000 });
        await page.waitForTimeout(5000);
        await page.waitForSelector('.ai-classroom', { timeout: 10000 }).catch(() => {});
        await page.waitForTimeout(2000);

        let detail = null;
        for (let attempt = 0; attempt < 30; attempt += 1) {
          detail = await page
            .evaluate(() => {
              const el = document.querySelector('.ai-classroom');
              if (!el?.__vue__) return null;
              const value = el.__vue__.$data?.videoDetailObj;
              if (!value?.id) return null;
              return JSON.parse(
                JSON.stringify(value, (key, val) => {
                  if (typeof val === 'function') return undefined;
                  if (val instanceof Node) return undefined;
                  if (val instanceof Window) return undefined;
                  return val;
                }),
              );
            })
            .catch(() => null);
          if (detail?.id) return { status: 200, text: JSON.stringify(detail), json: detail };
          await page.waitForTimeout(1000);
        }

        return page.evaluate(
          async (url) => {
            const response = await fetch(url, {
              credentials: 'include',
              headers: { Accept: 'application/json, text/plain, */*' },
            });
            const text = await response.text();
            let json = null;
            try {
              json = JSON.parse(text);
            } catch (_) {}
            return { status: response.status, text, json };
          },
          apiUrl,
        );
      },
    };
  }

  if (process.argv.includes('--node-fetch')) {
    return {
      close: async () => {},
      fetchRecordDetail: async (pageUrl, apiUrl) => {
        const response = await fetch(apiUrl, {
          headers: {
            Accept: 'application/json, text/plain, */*',
            Referer: cfg.platform.portalBase,
          },
        });
        const text = await response.text();
        let json = null;
        try {
          json = JSON.parse(text);
        } catch (_) {}
        return { status: response.status, text, json };
      },
    };
  }

  const sessionDir = process.env.XZ_SESSION_DIR || path.join(process.cwd(), 'session');
  const context = await chromium.launchPersistentContext(sessionDir, {
    channel: 'msedge',
    headless: true,
    acceptDownloads: true,
  });
  const page = await context.newPage();
  await page
    .goto(cfg.platform.portalBase + '#/teacherVideoResource.htm', {
      waitUntil: 'domcontentloaded',
      timeout: 90000,
    })
    .catch(() => {});
  await page.waitForTimeout(1500);
  return {
    close: async () => context.close().catch(() => {}),
    fetchRecordDetail: async (pageUrl, apiUrl) =>
      page.evaluate(
        async (url) => {
          const response = await fetch(url, {
            credentials: 'include',
            headers: { Accept: 'application/json, text/plain, */*' },
          });
          const text = await response.text();
          let json = null;
          try {
            json = JSON.parse(text);
          } catch (_) {}
          return { status: response.status, text, json };
        },
        apiUrl,
      ),
  };
}

async function main() {
  const cfg = loadConfig(configArg());
  const courseNames = coursesFromArgs(cfg);
  if (courseNames.length === 0) {
    console.error('没有课程可处理（--courses=课程A,课程B 或 config.json 的 courses）');
    process.exit(1);
  }
  const sourceRoot = path.join(process.cwd(), cfg.paths.courseItemsDir);
  const outRoot = path.join(process.cwd(), cfg.paths.manifestsDir);
  ensureDir(outRoot);

  const summary = [];
  const fetcher = await createFetcher(cfg);
  try {
    for (const courseLabel of courseNames) {
      const sourceFile = path.join(sourceRoot, courseLabel, 'video-items.json');
      if (!fs.existsSync(sourceFile)) {
        console.error(`[${courseLabel}] 找不到课程清单：${sourceFile}`);
        summary.push({ course: courseLabel, total: 0, ok: 0, failed: 1 });
        continue;
      }
      const outDir = path.join(outRoot, sanitize(courseLabel));
      ensureDir(outDir);
      const items = JSON.parse(fs.readFileSync(sourceFile, 'utf8'));
      const manifest = [];
      const limit = limitFromArgs();
      const selectedItems = limit > 0 ? items.slice(0, limit) : items;
      for (let i = 0; i < selectedItems.length; i += 1) {
        const item = selectedItems[i];
        const base = itemBaseName(item, courseLabel);
        const pageUrl = aiClassroomUrl(cfg, item);
        const apiUrl = detailUrlFor(cfg, item.id);
        try {
          const fetched = await fetcher.fetchRecordDetail(pageUrl, apiUrl);
          if (fetched.status !== 200 || !fetched.json) {
            throw new Error(`recordvideo 返回异常：HTTP ${fetched.status} ${String(fetched.text || '').slice(0, 120)}`);
          }
          const detail = fetched.json.data || fetched.json;
          const mediaFiles = flattenMediaFiles(detail);
          const selected = chooseMedia(mediaFiles, cfg.preferredView);
          if (!selected) throw new Error('详情中没有可用媒体 URL');
          manifest.push({
            status: 'ok',
            course: courseLabel,
            base,
            id: item.id,
            startTime: item.startTime || '',
            endTime: item.endTime || '',
            teacherNames: item.teacherNames || detail.teacherNames || '',
            classroomName: item.classroomName || detail.roomName || detail.classroomName || '',
            aiClassroomUrl: pageUrl,
            detailUrl: apiUrl,
            selected,
            mediaFiles,
          });
          console.log(`[${courseLabel}] [${i + 1}/${selectedItems.length}] ok: ${base} -> ${selected.viewType} voice=${selected.voiceStatus}`);
        } catch (error) {
          console.error(`[${courseLabel}] 失败：${base}: ${error.message}`);
          manifest.push({
            status: 'failed',
            course: courseLabel,
            base,
            id: item.id,
            startTime: item.startTime || '',
            aiClassroomUrl: pageUrl,
            detailUrl: apiUrl,
            error: error.stack || error.message,
          });
        }
        fs.writeFileSync(path.join(outDir, 'media-manifest.json'), JSON.stringify(manifest, null, 2), 'utf8');
      }
      const ok = manifest.filter((row) => row.status === 'ok').length;
      summary.push({ course: courseLabel, total: manifest.length, ok, failed: manifest.length - ok, outDir });
    }
  } finally {
    await fetcher.close();
  }
  console.log(JSON.stringify(summary, null, 2));
}

function configArg() {
  const arg = process.argv.find((item) => item.startsWith('--config='));
  return arg ? arg.slice('--config='.length) : null;
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
