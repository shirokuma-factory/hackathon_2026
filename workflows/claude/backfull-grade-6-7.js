// 第 6〜8 部の採点者（各 2 人）を同時に動かした Workflow の台本（2026-11-06〜07 に使用）。
// 前の点を知らない新しいエージェントに役を演じさせ、決まった形（JSON スキーマ）で点と指摘を返させる。
// リポジトリに入れるにあたり、PC 固有のパスを「<リポジトリ>」「<作業用の一時フォルダ>」に置き換えた。
export const meta = {
  name: 'backfull-grade-6-7',
  description: 'Rubric part 6 (two), part 7 personas (two), part 8 design (two)',
  phases: [{ title: 'Grade', detail: 'part 6 x2 and part 7 personas x2' }],
}

const SCORE = {
  type: 'object',
  properties: {
    total: { type: 'integer' },
    axes: { type: 'array', items: { type: 'object', properties: { name: { type: 'string' }, score: { type: 'integer' }, max: { type: 'integer' } }, required: ['name', 'score', 'max'] } },
    deductions: { type: 'integer' },
    p0: { type: 'integer' }, p1: { type: 'integer' }, p2: { type: 'integer' },
    fixes: { type: 'array', items: { type: 'object', properties: { id: { type: 'string' }, severity: { type: 'string' }, kind: { type: 'string' }, where: { type: 'string' }, what: { type: 'string' }, fix: { type: 'string' } }, required: ['id', 'severity', 'kind', 'where', 'what', 'fix'] } },
    file: { type: 'string' },
    cleanup_ok: { type: 'boolean' },
  },
  required: ['total', 'axes', 'deductions', 'p0', 'p1', 'p2', 'fixes', 'file', 'cleanup_ok'],
}

const base = (tag, angle) => `You are a strict, independent grader. Repo: <リポジトリ> (Windows, Git Bash, \`uv run\`). Do NOT modify code or docs except your output file; do NOT commit or push. Do NOT read earlier grading files in docs/reviews/ whose names contain 思想と公開版, 論理の筋, 採点の推移, 洗い出し or 道具の採点 (no anchoring). You MAY read the external reviews docs/reviews/2026-11-06_総合レビューと100点の受入条件.md, docs/reviews/2026-11-06_implementation-audit.md, docs/reviews/2026-11-06_independent-design-audit.md as the rubric references. Ignore docs/video/, tools/video/, tests/test_video_record.py.

Rubric: "第 6 部" at the end of docs/plan/2026-11-04_解決の筋書き_採点基準.md (6 axes, 100 points, deductions). Read 第 5 部 too for output format. Vision: docs/plan/2026-11-03_最新版のまとめ_v4.md §10, docs/plan/2026-11-06_物流の人が使える道具にする設計.md. App: SPEC.md (§15, §20–§46 at the end (§45–§46 are tonight's UI changes) are newest), README.md, docs/guide/, web/kit/, examples/.

Run at most one headless Edge at a time and close it before starting the next (the PC overloads otherwise). You must verify by running things: (1) python snippets against src/shipcompare with boundary inputs (0, exact limits, multi-day dates, departed trips, full-width text, broken save files); (2) headless Edge via tools/capture_screens.py helpers (EDGE_CLEAN_FLAGS, blank_targets, new_profile/release_profile) against tools/serve_web.py --public on a free port started and stopped inside your own script's finally block — check the map app views at 1920x1080 and 400x860, the own-fleet CSV import using examples/own/*.csv (count /api/ requests), web/waiting.html?example=1, web/compare_lite.html, examples/kit/index.html (serve the repo root with python http.server on a free port, stop it in finally); collect console errors/warnings; open the images with Read. Scripts and images go under <作業用の一時フォルダ>/grade6_${args.r6}${tag}\\.

Your emphasis (still score all axes): ${angle}

Write your report in Japanese (です・ます, plain, no metaphors) to docs/reviews/2026-11-06_道具の採点_${args.r6}${tag}.md with: 確かめたこと（実測）, 観点ごとの点と理由, 減点, 合計, 5 役それぞれの納得, P0/P1/P2 の表（区分: 実データ/文言/試験, file:line）, 100 点に届くために残っている直し（全部、順に）, 確信度. Then return the structured result (file = that path; cleanup_ok = true only if you confirmed no server/Edge you started is still running).`


const base7 = (tag, who) => `You are a strict, independent grader who role-plays real users. Repo: <リポジトリ> (Windows, Git Bash, \`uv run\`). Do NOT modify code or docs except your output file; do NOT commit or push. Do NOT read earlier grading files in docs/reviews/ (no anchoring), except you MAY read docs/reviews/2026-11-06_総合レビューと100点の受入条件.md.

Rubric: "第 7 部: 実務の効き目" at the end of docs/plan/2026-11-04_解決の筋書き_採点基準.md (6 axes, 100 points, deductions). App: README.md, SPEC.md, docs/guide/, docs/plan/2026-11-06_物流の人が使える道具にする設計.md, docs/plan/2026-11-03_課題の出典.md.

Role-play these people in depth: ${who}. For each, first write a concrete working day and their current procedure with steps and minutes (mark as 仮定). Then actually use the app as that person: start tools/serve_web.py --public on a free port inside your own python script and stop it in finally; drive headless Edge with tools/capture_screens.py helpers (EDGE_CLEAN_FLAGS, blank_targets, new_profile/release_profile); run at most ONE Edge at a time and close it before the next. Use the map app (#v=market, find, compare, own), the own-fleet import with examples/own/*.csv and also a file you create in the shape that person really has (e.g. a messy Excel-exported 配車表 CSV, a handwritten-style waiting log), web/waiting.html, web/compare_lite.html. Count your real operation steps and note where you got stuck, what jargon you did not understand, and what number you would not trust. Open screenshots with Read. Scripts and images go under <作業用の一時フォルダ>/grade7_${args.r7}${tag}\\.

The app is now public: https://backfull-demo-720575644376.asia-northeast1.run.app/ (map app), /own_lite.html (own-fleet single page, also web/own_lite.html via file://), /driver.html, /waiting.html, /compare_lite.html. NOTE: the public Cloud Run build is OLDER than the local working tree (redeploy is pending the author's decision). Judge features and behaviour on the local server (tools/serve_web.py --public and web/*.html); judge only access/sharing by the public URLs, and do not deduct for features that differ only because the public build is old (state such differences once in your report).

Also answer bluntly: would this person use it next week, and if not, what single thing would make them use it; is this really the tool that reduces their work, or a different tool is needed.

Write your report in Japanese (です・ます, plain, no metaphors) to docs/reviews/2026-11-06_実務の効き目_${args.r7}${tag}.md with: 人ごとの 1 日と今の手順（仮定）, ツールでの手順（実測の操作の数）, 判断が変わるか, 使うか, 観点ごとの点と理由, 減点, 合計, 指摘の表（区分: 実データ/文言/足りない機能, file:line or 画面）, 95 点に届くための直し（効き目の大きい順）, 確信度. Then return the structured result (file = that path; cleanup_ok = true only if no server/Edge you started is still running).`

const base8 = (tag, who) => `You are a strict, independent design grader. Repo: <リポジトリ> (Windows, Git Bash, \`uv run\`). Do NOT modify code or docs except your output file; do NOT commit or push. Do NOT read earlier grading files in docs/reviews/ (no anchoring).

Rubric: "第 8 部: 画面の出来と印象" at the end of docs/plan/2026-11-04_解決の筋書き_採点基準.md (7 axes, 100 points, deductions). Context: docs/plan/2026-11-06_交渉文と最初の画面の判断.md, docs/plan/2026-11-06_依頼_最初の画面と操作.md, docs/plan/2026-11-06_依頼_空車分析と荷主提案.md, README.md.

Act as: ${who}. Start tools/serve_web.py (and --public) on a free port inside your own python script and stop it in finally; drive headless Edge with tools/capture_screens.py helpers or tests/conftest.py edge_page (EDGE_CLEAN_FLAGS, new_profile/release_profile); at most ONE Edge at a time. Go through: plain / (first screen), each role and purpose path, the Japan map (guided and full), the own view guided steps and negotiation draft, the trip diagram, compare, find (架空のデモ), web/own_lite.html, web/waiting.html, web/driver.html — at 1920x1080 and 360x800. Measure first-viewport visible text characters (walk text nodes whose parent is visible and intersects the viewport; exclude the map canvas and .maplibregl-ctrl-attrib), primary buttons and visible controls on every guided screen. Check motion (record durations of transitions/flyTo if observable), prefers-reduced-motion behaviour, keyboard path, console errors, behaviour with map data requests failing. Save screenshots under <作業用の一時フォルダ>/grade8_${args.r8}${tag}\\ and open them with Read — judge what you actually saw.

Write your report in Japanese (です・ます, plain, no metaphors) to docs/reviews/2026-11-07_画面の出来_${args.r8}${tag}.md with: 確かめたこと（実測の表: 画面ごとの文字数・押せる所・主なボタン）, 観点ごとの点と理由, 減点, 合計, 指摘の表（区分: 見た目/誘導/文章/地図/素材/動作, file:line or 画面）, 95 点に届くための直し（効き目の大きい順）, 確信度. Then return the structured result (file = that path; cleanup_ok = true only if no server/Edge you started is still running).`

const results = await parallel([
  () => agent(base8('a', '初めて開く審査員（デザインと印象に厳しい。素人の作りか、記憶に残るかを見る）'), { label: 'design-judge', phase: 'Grade', schema: SCORE, model: 'opus' }),
  () => agent(base8('b', '中小の運送会社の配車担当と中小荷主の出荷担当（初めて使い、誘導だけで目的の答えにたどり着けるか、読みにくくないかを見る）'), { label: 'design-user', phase: 'Grade', schema: SCORE, model: 'opus' }),
  () => agent(base('a', 'correctness of calculations and bookings under boundary inputs, consistency of the same value across screens, and data never leaving the browser.'), { label: 'grader-a', phase: 'Grade', schema: SCORE, model: 'opus' }),
  () => agent(base('b', 'usefulness for dispatchers/operations managers with their own data (own-fleet import, cost, insights), reusability of web/kit with docs and schemas, accessibility and 400px usability.'), { label: 'grader-b', phase: 'Grade', schema: SCORE, model: 'opus' }),
  () => agent(base7('a', '中小の運送会社の配車担当、同じ会社の社長兼運行管理者、大型のドライバー'), { label: 'persona-carrier', phase: 'Grade', schema: SCORE, model: 'opus' }),
  () => agent(base7('b', '中小荷主の出荷担当、荷待ちの協議の書面を受け取る荷主の物流担当、懐疑的な審査員'), { label: 'persona-shipper', phase: 'Grade', schema: SCORE, model: 'opus' }),
])
return results
