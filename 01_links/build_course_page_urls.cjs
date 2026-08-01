'use strict';
/**
 * 01_links/build_course_page_urls.cjs
 *
 * 从媒体清单（media_manifests/<课程>/media-manifest.json）生成「课程实录页面 URL」
 * 清单，便于人工或后续脚本逐课打开实录页。
 *
 * 输出：
 *   course_page_urls/all_course_record_urls.csv / .txt / .tsv
 *   course_page_urls/by_course/<课程>/课程实录页面_urls.csv
 *
 * 用法：node build_course_page_urls.cjs --config config.json
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

function safeName(name) {
  return String(name).replace(/[<>:"/\\|?*]/g, '_').trim();
}

function csvCell(value) {
  const text = value == null ? '' : String(value);
  return `"${text.replace(/"/g, '""')}"`;
}

function writeUtf8(file, text) {
  fs.writeFileSync(file, text, 'utf8');
}

function writeCsv(file, rows, headers = ['课程', '文件名', '上课时间', '教师', '教室', '课程实录页面URL']) {
  const lines = [headers.map(csvCell).join(','), ...rows.map((row) => headers.map((h) => csvCell(row[h])).join(','))];
  fs.writeFileSync(file, '\ufeff' + lines.join('\r\n') + '\r\n', 'utf8');
}

function main() {
  const cfg = loadConfig(configArg());
  const sourceDir = path.join(process.cwd(), cfg.paths.manifestsDir);
  const outDir = path.join(process.cwd(), 'course_page_urls');
  const byCourseDir = path.join(outDir, 'by_course');
  ensureDir(byCourseDir);

  const allRows = [];
  const courses = fs
    .readdirSync(sourceDir, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'));

  for (const course of courses) {
    const manifestPath = path.join(sourceDir, course, 'media-manifest.json');
    if (!fs.existsSync(manifestPath)) continue;
    const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
    const rows = manifest
      .filter((item) => item.status === 'ok' && item.aiClassroomUrl)
      .map((item) => ({
        课程: course,
        文件名: item.base || '',
        上课时间: item.startTime || '',
        教师: item.teacherNames || '',
        教室: item.classroomName || '',
        课程实录页面URL: item.aiClassroomUrl || '',
      }));

    allRows.push(...rows);
    const courseOut = path.join(byCourseDir, safeName(course));
    ensureDir(courseOut);
    writeCsv(path.join(courseOut, '课程实录页面_urls.csv'), rows);
    writeUtf8(path.join(courseOut, 'urls.txt'), rows.map((row) => row['课程实录页面URL']).join('\n') + '\n');
    writeUtf8(
      path.join(courseOut, '文件名_课程实录页面URL.tsv'),
      rows.map((row) => `${row['文件名']}\t${row['上课时间']}\t${row['课程实录页面URL']}`).join('\n') + '\n',
    );
  }

  writeCsv(path.join(outDir, 'all_course_record_urls.csv'), allRows);
  writeUtf8(path.join(outDir, 'all_course_record_urls.txt'), allRows.map((row) => row['课程实录页面URL']).join('\n') + '\n');
  writeUtf8(
    path.join(outDir, 'all_course_record_urls.tsv'),
    allRows
      .map((row) => `${row['课程']}\t${row['文件名']}\t${row['上课时间']}\t${row['教师']}\t${row['教室']}\t${row['课程实录页面URL']}`)
      .join('\n') + '\n',
  );

  const summary = courses.map((course) => ({ 课程: course, 数量: allRows.filter((row) => row['课程'] === course).length }));
  writeCsv(path.join(outDir, 'summary.csv'), summary, ['课程', '数量']);

  console.log(`已输出到: ${outDir}`);
  for (const row of summary) console.log(`${row['课程']}: ${row['数量']}`);
  console.log(`总数: ${allRows.length}`);
}

function configArg() {
  const arg = process.argv.find((item) => item.startsWith('--config='));
  return arg ? arg.slice('--config='.length) : null;
}

main();
