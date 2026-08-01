'use strict';
/**
 * 01_links/export_fresh_media_urls.cjs
 *
 * 把 fresh 媒体清单（media_manifests/<课程>/media-manifest.json）汇总导出为：
 *   media_urls/all_fresh_media_urls.csv / .tsv / .txt
 *   media_urls/by_course/<课程>/fresh_media_urls.csv / filename_url.tsv
 *
 * CSV 列：课程、文件名、上课时间、教师、教室、视角、时长秒、视频大小MB、
 *         课程实录页面URL、视频直链（selected.url）
 *
 * 用法：node export_fresh_media_urls.cjs --config config.json
 */
const fs = require('fs');
const path = require('path');

function loadConfig(configPath) {
  const file = configPath || process.env.PIPELINE_CONFIG || path.join(process.cwd(), 'config.json');
  if (!fs.existsSync(file)) throw new Error(`找不到配置文件: ${file}`);
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function csvCell(value) {
  const text = value == null ? '' : String(value);
  return `"${text.replace(/"/g, '""')}"`;
}

function writeCsv(file, rows) {
  const headers = ['课程', '文件名', '上课时间', '教师', '教室', '视角', '时长秒', '视频大小MB', '课程实录页面URL', '视频直链'];
  const lines = [headers.map(csvCell).join(','), ...rows.map((row) => headers.map((header) => csvCell(row[header])).join(','))];
  fs.writeFileSync(file, '\ufeff' + lines.join('\r\n') + '\r\n', 'utf8');
}

function safeName(name) {
  return String(name).replace(/[<>:"/\\|?*]/g, '_').trim();
}

function main() {
  const cfg = loadConfig(configArg());
  const sourceRoot = path.join(process.cwd(), cfg.paths.manifestsDir);
  const outDir = path.join(process.cwd(), cfg.paths.urlsDir);
  ensureDir(outDir);
  ensureDir(path.join(outDir, 'by_course'));

  const rows = [];
  const courses = fs
    .readdirSync(sourceRoot, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'));

  for (const course of courses) {
    const manifestPath = path.join(sourceRoot, course, 'media-manifest.json');
    if (!fs.existsSync(manifestPath)) continue;
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    const courseRows = manifest
      .filter((item) => item.status === 'ok')
      .map((item) => ({
        课程: course,
        文件名: item.base || '',
        上课时间: item.startTime || '',
        教师: item.teacherNames || '',
        教室: item.classroomName || '',
        视角: item.selected?.viewName || item.selected?.viewType || '',
        时长秒: item.selected?.fileLength || '',
        视频大小MB: item.selected?.videoFileSize ? Math.round((item.selected.videoFileSize / 1024 / 1024) * 100) / 100 : '',
        课程实录页面URL: item.aiClassroomUrl || '',
        视频直链: item.selected?.url || '',
      }));
    rows.push(...courseRows);
    const courseDir = path.join(outDir, 'by_course', safeName(course));
    ensureDir(courseDir);
    writeCsv(path.join(courseDir, 'fresh_media_urls.csv'), courseRows);
    fs.writeFileSync(path.join(courseDir, 'fresh_media_urls.txt'), courseRows.map((row) => row['视频直链']).join('\n') + '\n', 'utf8');
    fs.writeFileSync(
      path.join(courseDir, 'filename_url.tsv'),
      courseRows.map((row) => `${row['文件名']}\t${row['上课时间']}\t${row['视频直链']}`).join('\n') + '\n',
      'utf8',
    );
  }

  writeCsv(path.join(outDir, 'all_fresh_media_urls.csv'), rows);
  fs.writeFileSync(path.join(outDir, 'all_fresh_media_urls.txt'), rows.map((row) => row['视频直链']).join('\n') + '\n', 'utf8');
  fs.writeFileSync(
    path.join(outDir, 'all_fresh_media_urls.tsv'),
    rows.map((row) => `${row['课程']}\t${row['文件名']}\t${row['上课时间']}\t${row['视频大小MB']}\t${row['视频直链']}`).join('\n') + '\n',
    'utf8',
  );

  const summary = courses.map((course) => {
    const courseRows = rows.filter((row) => row['课程'] === course);
    return {
      课程: course,
      数量: courseRows.length,
      唯一直链数: new Set(courseRows.map((row) => row['视频直链'])).size,
    };
  });
  console.log(`已输出到: ${outDir}`);
  console.log(JSON.stringify(summary, null, 2));
}

function configArg() {
  const arg = process.argv.find((item) => item.startsWith('--config='));
  return arg ? arg.slice('--config='.length) : null;
}

main();
