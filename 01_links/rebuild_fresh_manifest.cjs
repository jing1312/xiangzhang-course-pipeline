'use strict';
/**
 * 01_links/rebuild_fresh_manifest.cjs
 *
 * 视频直链（manifest 里的 selected.url）可能带有效期的签名参数，失效后（HTTP 401 / 403）
 * 需要重新抓取 fresh manifest。本脚本逐个打开课程实录页面，拦截 /recordvideo 响应或
 * 读取 Vue 组件数据，重新生成 fresh_media_manifests/<课程>/media-manifest.json。
 *
 * 前置条件：以 --remote-debugging-port=9222 启动 Edge/Chrome 并登录教学平台。
 *
 * 用法：
 *   node rebuild_fresh_manifest.cjs --config config.json --courses=药物分析 --limit=3
 */
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { chromium } = require('playwright');

function loadConfig(configPath) {
  const file = configPath || process.env.PIPELINE_CONFIG || path.join(process.cwd(), 'config.json');
  if (!fs.existsSync(file)) throw new Error(`找不到配置文件: ${file}`);
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function sanitize(value, fallback = 'untitled') {
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

async function currentDevtoolsWsUrl(cfg) {
  const port = cfg.cdp?.port || 9222;
  const response = await fetch(`http://127.0.0.1:${port}/json/version`);
  if (!response.ok) throw new Error(`无法读取浏览器调试端口: HTTP ${response.status}`);
  const data = await response.json();
  if (!data.webSocketDebuggerUrl) throw new Error('浏览器调试端口未返回 webSocketDebuggerUrl');
  return data.webSocketDebuggerUrl.replace('localhost', '127.0.0.1');
}

function itemBaseName(item, courseLabel) {
  return sanitize(`${item.courseName || courseLabel}_${item.startTime || item.videoInfoName || item.id}`);
}

function aiClassroomUrl(cfg, item) {
  const url = new URL(cfg.platform.portalBase);
  const startTime = String(item.startTime || '');
  const monthMatch = startTime.match(/^\d{4}-(\d{1,2})-/);
  const month = monthMatch ? monthMatch[1] : String(item.month || item.mouth || '');
  const params = new URLSearchParams({
    videoId: item.id,
    courseTableId: item.courseTableId,
    schoolYear: item.schoolYear || cfg.platform.schoolYear || '2025-2026',
    term: String(item.term ?? cfg.platform.term ?? 3),
    mouth: month,
    latestWatchTime: '0',
  });
  url.hash = `#/aiClassroom/aiClassroom.htm?${params.toString()}`;
  return url.toString();
}

function recordVideoValidCode(cfg, videoId) {
  return crypto.createHash('md5').update(`id=${videoId}&signKey=${cfg.platform.signKey}`).digest('hex');
}

function detailUrlFor(cfg, videoId) {
  const validCode = recordVideoValidCode(cfg, videoId);
  const api = cfg.platform.teachingApi.replace(/\/+$/, '');
  return `${api}/v1/recordvideo/${encodeURIComponent(videoId)}?validCode=${encodeURIComponent(validCode)}`;
}

function joinMediaUrl(host, storePath) {
  if (!host || !storePath) return '';
  return `${String(host).replace(/\/+$/g, '')}/${String(storePath).replace(/^\/+/, '')}`;
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
    return (b.videoFileSize || 0) - (a.videoFileSize || 0);
  });
  return ranked[0] || null;
}

async function fetchDetailFromPage(context, cfg, item, pageUrl, apiUrl) {
  const page = await context.newPage();
  page.setDefaultTimeout(90000);
  const responseDetails = [];
  page.on('response', async (response) => {
    const url = response.url();
    if (!url.includes(`/recordvideo/${item.id}`) || url.includes('/summarize')) return;
    const contentType = response.headers()['content-type'] || '';
    if (!/json|text/.test(contentType)) return;
    try {
      const text = await response.text();
      let json = null;
      try {
        json = JSON.parse(text);
      } catch (_) {}
      responseDetails.push({ status: response.status(), url, text, json });
    } catch (_) {}
  });

  await page.goto(pageUrl, { waitUntil: 'domcontentloaded', timeout: 90000 });

  for (let attempt = 0; attempt < 15; attempt += 1) {
    const detail = await page
      .evaluate((expectedId) => {
        const value = document.querySelector('.ai-classroom')?.__vue__?.$data?.videoDetailObj || null;
        if (!value || value.id !== expectedId) return null;
        return JSON.parse(JSON.stringify(value));
      }, item.id)
      .catch(() => null);
    if (detail?.id === item.id) {
      await page.close().catch(() => {});
      return { source: 'vue', status: 200, json: detail };
    }
    const responseHit = responseDetails.find((hit) => hit.status === 200 && (hit.json?.data?.id === item.id || hit.json?.id === item.id));
    if (responseHit) {
      await page.close().catch(() => {});
      return { source: 'network', status: 200, json: responseHit.json?.data || responseHit.json };
    }
    await page.waitForTimeout(1000);
  }

  const responseHit = responseDetails.find((hit) => hit.status === 200 && (hit.json?.data?.id === item.id || hit.json?.id === item.id));
  if (responseHit) {
    await page.close().catch(() => {});
    return { source: 'network', status: 200, json: responseHit.json?.data || responseHit.json };
  }

  const fetched = await page.evaluate(
    async ({ url, expectedId }) => {
      const response = await fetch(url, {
        credentials: 'include',
        headers: { Accept: 'application/json, text/plain, */*' },
      });
      const text = await response.text();
      let json = null;
      try {
        json = JSON.parse(text);
      } catch (_) {}
      const data = json?.data || json;
      return { status: response.status, text, json, matched: Boolean(data?.id === expectedId) };
    },
    { url: apiUrl, expectedId: item.id },
  );

  if (fetched.status === 200 && fetched.matched) {
    await page.close().catch(() => {});
    return { source: 'fetch', status: 200, json: fetched.json?.data || fetched.json };
  }

  await page.close().catch(() => {});
  throw new Error(`详情不匹配或不可用: HTTP ${fetched.status}; expected=${item.id}; body=${String(fetched.text || '').slice(0, 200)}`);
}

async function main() {
  const cfg = loadConfig(configArg());
  const courseNames = coursesFromArgs(cfg);
  if (courseNames.length === 0) {
    console.error('没有课程可处理');
    process.exit(1);
  }
  const sourceRoot = path.join(process.cwd(), cfg.paths.courseItemsDir);
  const outRoot = path.join(process.cwd(), cfg.paths.manifestsDir);
  ensureDir(outRoot);

  const wsUrl = await currentDevtoolsWsUrl(cfg);
  const browser = await chromium.connectOverCDP(wsUrl, { timeout: 120000 });
  const context = browser.contexts()[0] || (await browser.newContext());

  const limit = limitFromArgs();
  const summary = [];
  try {
    for (const courseLabel of courseNames) {
      const sourceFile = path.join(sourceRoot, courseLabel, 'video-items.json');
      if (!fs.existsSync(sourceFile)) {
        summary.push({ course: courseLabel, total: 0, ok: 0, failed: 1, error: 'missing video-items.json' });
        continue;
      }
      const outDir = path.join(outRoot, sanitize(courseLabel));
      ensureDir(outDir);
      const items = JSON.parse(fs.readFileSync(sourceFile, 'utf8'));
      const selectedItems = limit > 0 ? items.slice(0, limit) : items;
      const manifest = [];

      for (let index = 0; index < selectedItems.length; index += 1) {
        const item = selectedItems[index];
        const base = itemBaseName(item, courseLabel);
        const pageUrl = aiClassroomUrl(cfg, item);
        const apiUrl = detailUrlFor(cfg, item.id);
        try {
          const fetched = await fetchDetailFromPage(context, cfg, item, pageUrl, apiUrl);
          const detail = fetched.json;
          if (detail.id !== item.id) throw new Error(`页面详情 id 不匹配: got=${detail.id}, expected=${item.id}`);
          const mediaFiles = flattenMediaFiles(detail);
          const selected = chooseMedia(mediaFiles, cfg.preferredView);
          if (!selected) throw new Error('没有可用媒体 URL');
          manifest.push({
            status: 'ok',
            source: fetched.source,
            course: courseLabel,
            base,
            id: item.id,
            detailId: detail.id,
            startTime: item.startTime || detail.startTime || '',
            endTime: item.endTime || detail.endTime || '',
            teacherNames: item.teacherNames || detail.teacherNames || '',
            classroomName: item.classroomName || detail.roomName || detail.classroomName || '',
            aiClassroomUrl: pageUrl,
            detailUrl: apiUrl,
            selected,
            mediaFiles,
          });
          console.log(`[${courseLabel}] [${index + 1}/${selectedItems.length}] ok: ${selected.viewType} voice=${selected.voiceStatus}`);
        } catch (error) {
          console.log(`[${courseLabel}] [${index + 1}/${selectedItems.length}] failed: ${error.message}`);
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
      const uniqueSelected = new Set(manifest.filter((row) => row.status === 'ok').map((row) => row.selected?.url).filter(Boolean)).size;
      summary.push({ course: courseLabel, total: manifest.length, ok, failed: manifest.length - ok, uniqueSelected, outDir });
    }
  } finally {
    await browser.close().catch(() => {});
  }

  fs.writeFileSync(path.join(outRoot, 'summary.json'), JSON.stringify(summary, null, 2), 'utf8');
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
