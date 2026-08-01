'use strict';
/**
 * 01_links/collect_course_items.cjs
 *
 * 从「教师课程实录列表页」(teacherVideoResource.htm) 按课程抓取全部实录项，
 * 生成下游 collect_media_details.cjs 所需的 video-items.json。
 *
 * 原理（与平台前端一致）：
 *   - 打开课程实录列表页，读取 Vue 组件 .box-video 内的
 *     weeklyList（课程列表，含 classIds/courseName）、currentTermInfo、
 *     pageSize、userInfo.userId、$staticConfig()（teachingApi/validCode）
 *   - 调用 POST {teachingApi}/v1/videoinfos/page?validCode={validCode}，
 *     body: { userId, groupIds, openStatus:'1', week:null, schoolYear, term,
 *             validCode, page, pageSize }，翻页收集该课程全部实录项
 *
 * 两种连接方式：
 *   --cdp       连接已登录的调试浏览器（Edge/Chrome --remote-debugging-port=9222）
 *   （默认）    launchPersistentContext 启动 headless Edge，用持久化会话目录复用登录
 *
 * 用法：
 *   node collect_course_items.cjs --config config.json --cdp
 *   node collect_course_items.cjs --config config.json --courses=药物分析,临床药理学
 *   node collect_course_items.cjs --config config.json --cdp --all --out collected-video-items.json
 *
 * 输出：
 *   downloads_by_course/<课程>/video-items.json   （collect_media_details.cjs 的输入）
 *   downloads_by_course/<课程>/urls.txt           （每节课 AI 课堂页直链）
 *   --all 时额外输出 collected-video-items.json   （全部课程实录项的合并清单）
 */
const fs = require('fs');
const path = require('path');
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

function hasArg(name) {
  return process.argv.includes(name);
}

function argValue(name, fallback) {
  const arg = process.argv.find((item) => item.startsWith(`${name}=`));
  if (!arg) return fallback;
  return arg.slice(`${name}=`.length);
}

function devtoolsWsUrl(cfg) {
  const envWs = process.env.CDP_WS_URL;
  if (envWs) return envWs;
  const port = process.env.CDP_PORT || cfg.cdp?.port || '9222';
  return `ws://127.0.0.1:${port}/devtools/browser/`;
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

async function connect(cfg) {
  if (hasArg('--cdp')) {
    const browser = await chromium.connectOverCDP(devtoolsWsUrl(cfg), { timeout: 120000 });
    const context = browser.contexts()[0] || (await browser.newContext());
    const portalHost = new URL(cfg.platform.portalBase).host;
    let page = context.pages().find((candidate) => {
      try {
        return new RegExp(portalHost.replace(/\./g, '\\.')).test(candidate.url());
      } catch (_) {
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
    await page.waitForTimeout(2500);
    return { page, close: async () => browser.close().catch(() => {}) };
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
  await page.waitForTimeout(2500);
  return { page, close: async () => context.close().catch(() => {}) };
}

async function getTeacherState(page) {
  const state = await page.evaluate(() => {
    const comp = document.querySelector('.box-video')?.__vue__;
    if (!comp) throw new Error('未找到课程实录组件 .box-video（请确认已登录并打开课程实录页）');
    const d = comp.$data || {};
    const safe = (value) =>
      JSON.parse(
        JSON.stringify(value, (key, val) => {
          if (typeof val === 'function') return undefined;
          if (val instanceof Node) return undefined;
          if (val instanceof Window) return undefined;
          return val;
        }),
      );
    return {
      weeklyList: safe(d.weeklyList || []),
      currentTermInfo: safe(d.currentTermInfo || {}),
      pageSize: d.pageSize || 30,
      userId: comp.userInfo?.userId,
      teachingApi: comp.$staticConfig?.().teachingApi,
      validCode: comp.$staticConfig?.().validCode,
    };
  });
  if (!state.validCode || !state.teachingApi) {
    throw new Error('未能读取页面配置（teachingApi/validCode），请确认已登录并重新打开列表页');
  }
  return state;
}

async function fetchCourseItems(page, state, course, pageSize) {
  return await page.evaluate(
    async ({ course, pageSize, userId, currentTermInfo, teachingApi, validCode }) => {
      const comp = document.querySelector('.box-video')?.__vue__;
      if (!comp) throw new Error('missing component');
      const url = `${teachingApi}/v1/videoinfos/page?validCode=${validCode}`;
      const basePayload = {
        userId,
        groupIds: course.classIds,
        openStatus: '1',
        week: null,
        schoolYear: currentTermInfo.schoolYear,
        term: currentTermInfo.term,
        validCode,
        pageSize,
      };
      const first = await comp.$Utils.commonAjax({
        url,
        type: 'post',
        isApplication: true,
        data: { ...basePayload, page: 1 },
      });
      const total = Number(first.total || 0);
      const pages = Math.max(1, Math.ceil(total / pageSize));
      const all = [...(first.videoInfoResourceList || [])];
      for (let pageNo = 2; pageNo <= pages; pageNo += 1) {
        const result = await comp.$Utils.commonAjax({
          url,
          type: 'post',
          isApplication: true,
          data: { ...basePayload, page: pageNo },
        });
        all.push(...(result.videoInfoResourceList || []));
      }
      return { total, items: all };
    },
    { course, pageSize, userId: state.userId, currentTermInfo: state.currentTermInfo, teachingApi: state.teachingApi, validCode: state.validCode },
  );
}

function writeCourseDir(cfg, outRoot, courseLabel, items) {
  const outDir = path.join(outRoot, sanitize(courseLabel));
  ensureDir(outDir);
  fs.writeFileSync(path.join(outDir, 'video-items.json'), JSON.stringify(items, null, 2), 'utf8');
  fs.writeFileSync(path.join(outDir, 'urls.txt'), items.map((item) => aiClassroomUrl(cfg, item)).join('\n') + '\n', 'utf8');
  return outDir;
}

async function main() {
  const cfg = loadConfig(argValue('--config'));
  const courseNames = coursesFromArgs(cfg);
  if (courseNames.length === 0) {
    console.error('没有课程可处理（--courses=课程A,课程B 或 config.json 的 courses）');
    process.exit(1);
  }
  const outRoot = path.join(process.cwd(), cfg.paths.courseItemsDir);
  ensureDir(outRoot);

  const { page, close } = await connect(cfg);
  const summary = [];
  try {
    const state = await getTeacherState(page);
    console.log(
      `页面配置就绪：${state.teachingApi} | 学期 ${state.currentTermInfo.schoolYear}-${state.currentTermInfo.term} | 共 ${state.weeklyList.length} 门课`,
    );

    if (hasArg('--all')) {
      const allItems = [];
      for (const course of state.weeklyList) {
        const { items } = await fetchCourseItems(page, state, course, state.pageSize || 30);
        allItems.push(...items);
        console.log(`[全部] ${course.courseName || '未知课程'}: ${items.length} 节`);
      }
      const outFile = path.resolve(argValue('--out', 'collected-video-items.json'));
      fs.writeFileSync(outFile, JSON.stringify(allItems, null, 2), 'utf8');
      console.log(`已写入全部课程清单：${outFile} (${allItems.length} 节)`);
    }

    for (const courseLabel of courseNames) {
      const course = state.weeklyList.find((item) => {
        const name = String(item.courseName || '');
        return name.includes(courseLabel) || new RegExp(courseLabel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).test(name);
      });
      if (!course) {
        console.error(`未找到课程：${courseLabel}（可用 --all 查看课程列表）`);
        summary.push({ course: courseLabel, total: 0, ok: 0, failed: 1 });
        continue;
      }
      const { total, items } = await fetchCourseItems(page, state, course, state.pageSize || 30);
      items.sort((a, b) => String(a.startTime || a.videoInfoName || '').localeCompare(String(b.startTime || b.videoInfoName || ''), 'zh-Hans'));
      const outDir = writeCourseDir(cfg, outRoot, courseLabel, items);
      summary.push({ course: courseLabel, total, items: items.length, ok: items.length, failed: 0, outDir });
      console.log(`[${courseLabel}] ${items.length}/${total} 节已写入 ${outDir}/video-items.json`);
    }
  } finally {
    await close();
  }
  console.log(JSON.stringify(summary, null, 2));
}

main().catch((error) => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});
