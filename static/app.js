'use strict';

const form = document.getElementById('check-form');
const sizesList = document.getElementById('sizes-list');
const patternInput = document.getElementById('pattern');
const resultSection = document.getElementById('result');
const summaryBox = document.getElementById('summary');
const sizesResults = document.getElementById('sizes-results');
const moveMessage = document.getElementById('move-message');
const yarnInputs = {
  k: document.getElementById('yarn-k'),
  p: document.getElementById('yarn-p'),
  yo: document.getElementById('yarn-yo'),
  k2tog: document.getElementById('yarn-k2tog'),
  waste_percent: document.getElementById('yarn-waste'),
  grams_per_ball: document.getElementById('yarn-ball'),
};

const EXAMPLE_SIZES = [
  { name: 'S', cast_on: 12 },
  { name: 'M', cast_on: 16 },
  { name: 'L', cast_on: 20 },
];

const EXAMPLE_PATTERN = [
  '(k2tog yo)*{6|8|10}',
  'p*{12|16|20}',
  '(k2tog)*{6|8|10}',
  '(k2tog)*{3|4|5}',
  'k*{3|4|5}',
].join('\n');

function addSizeRow(name, castOn) {
  const row = document.createElement('div');
  row.className = 'size-row';

  const nameInput = document.createElement('input');
  nameInput.type = 'text';
  nameInput.className = 'size-name';
  nameInput.placeholder = '尺码名';
  nameInput.value = name;
  nameInput.required = true;

  const castOnInput = document.createElement('input');
  castOnInput.type = 'number';
  castOnInput.className = 'size-cast-on';
  castOnInput.min = '1';
  castOnInput.step = '1';
  castOnInput.placeholder = '起针数';
  castOnInput.value = castOn === '' ? '' : String(castOn);
  castOnInput.required = true;

  const upBtn = document.createElement('button');
  upBtn.type = 'button';
  upBtn.className = 'move-size move-up';
  upBtn.textContent = '↑';
  upBtn.title = '上移该尺码（指令中的按尺码倍数同步重排）';
  upBtn.addEventListener('click', () => moveSize(row, 'up'));

  const downBtn = document.createElement('button');
  downBtn.type = 'button';
  downBtn.className = 'move-size move-down';
  downBtn.textContent = '↓';
  downBtn.title = '下移该尺码（指令中的按尺码倍数同步重排）';
  downBtn.addEventListener('click', () => moveSize(row, 'down'));

  const removeBtn = document.createElement('button');
  removeBtn.type = 'button';
  removeBtn.className = 'remove-size';
  removeBtn.textContent = '×';
  removeBtn.title = '删除该尺码';
  removeBtn.addEventListener('click', () => {
    row.remove();
    updateRowButtons();
    hideMoveMessage();
  });

  row.appendChild(nameInput);
  row.appendChild(castOnInput);
  row.appendChild(upBtn);
  row.appendChild(downBtn);
  row.appendChild(removeBtn);
  sizesList.appendChild(row);
  updateRowButtons();
}

function updateRowButtons() {
  const rows = sizesList.querySelectorAll('.size-row');
  rows.forEach((row, i) => {
    row.querySelector('.remove-size').disabled = rows.length === 1;
    row.querySelector('.move-up').disabled = i === 0;
    row.querySelector('.move-down').disabled = i === rows.length - 1;
  });
}

function readSizes() {
  return Array.from(sizesList.querySelectorAll('.size-row')).map((row) => ({
    name: row.querySelector('.size-name').value,
    cast_on: row.querySelector('.size-cast-on').value,
  }));
}

function readYarn() {
  return {
    k: yarnInputs.k.value,
    p: yarnInputs.p.value,
    yo: yarnInputs.yo.value,
    k2tog: yarnInputs.k2tog.value,
    waste_percent: yarnInputs.waste_percent.value,
    grams_per_ball: yarnInputs.grams_per_ball.value,
  };
}

document.getElementById('add-size').addEventListener('click', () => {
  addSizeRow('', '');
  hideMoveMessage();
});

function showMoveMessage(text, ok) {
  moveMessage.textContent = text;
  moveMessage.className = 'move-message ' + (ok ? 'ok' : 'error');
  moveMessage.hidden = false;
}

function hideMoveMessage() {
  moveMessage.hidden = true;
  moveMessage.textContent = '';
}

async function moveSize(row, direction) {
  const rows = Array.from(sizesList.querySelectorAll('.size-row'));
  const index = rows.indexOf(row);
  if (index < 0) {
    return;
  }
  hideMoveMessage();

  let data;
  try {
    const res = await fetch('/api/move-size', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sizes: readSizes(),
        pattern: patternInput.value,
        index,
        direction,
      }),
    });
    data = await res.json();
  } catch (err) {
    showMoveMessage('移动请求失败：' + err.message, false);
    return;
  }

  if (!data.ok) {
    // 整次移动不生效：尺码列表与指令原文都不改动
    showMoveMessage(formatMoveError(data.error), false);
    updateRowButtons();
    return;
  }

  sizesList.replaceChildren();
  for (const s of data.sizes) {
    addSizeRow(s.name, s.cast_on);
  }
  patternInput.value = data.pattern;
  const dirText = direction === 'up' ? '上移' : '下移';
  showMoveMessage(
    '已' + dirText + '尺码，指令中的 *{...} 倍数项已按新顺序同步重排。', true);
  form.requestSubmit();  // 移动后自动重新校验：各尺码针数与移动前一致
}

function formatMoveError(err) {
  if (!err) {
    return '移动失败：未知错误';
  }
  if (err.type === 'reorder') {
    const where = '第 ' + err.line + ' 行第 ' + err.char + ' 个字符';
    return '移动未生效：' + where + ' 的按尺码倍数不合法——' + err.message;
  }
  return '移动未生效：' + err.message;
}

document.getElementById('fill-example').addEventListener('click', () => {
  sizesList.replaceChildren();
  for (const s of EXAMPLE_SIZES) {
    addSizeRow(s.name, s.cast_on);
  }
  patternInput.value = EXAMPLE_PATTERN;
  hideMoveMessage();
});

patternInput.addEventListener('input', hideMoveMessage);

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  summaryBox.textContent = '校验中…';
  summaryBox.className = '';
  resultSection.hidden = false;
  sizesResults.replaceChildren();

  let data;
  try {
    const res = await fetch('/api/validate-sizes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sizes: readSizes(),
        pattern: patternInput.value,
        yarn: readYarn(),
      }),
    });
    data = await res.json();
  } catch (err) {
    summaryBox.textContent = '请求失败：' + err.message;
    summaryBox.className = 'error';
    return;
  }
  render(data);
});

function render(data) {
  sizesResults.replaceChildren();

  // 尺码列表本身不合法：没有分尺码结果
  if (!Array.isArray(data.sizes) || data.sizes.length === 0) {
    summaryBox.textContent = '校验未通过：' + (data.error ? data.error.message : '未知错误');
    summaryBox.className = 'error';
    return;
  }

  const failed = data.sizes.filter((s) => !s.ok);
  if (failed.length === 0) {
    summaryBox.textContent = '校验通过：全部 ' + data.sizes.length + ' 个尺码的针数均正确。';
    summaryBox.className = 'ok';
  } else {
    summaryBox.textContent = '校验未通过：' + failed.length + ' / ' + data.sizes.length +
      ' 个尺码存在问题（' + failed.map((s) => s.name).join('、') +
      '），其余尺码已继续推算。';
    summaryBox.className = 'error';
  }

  const suggestion = data.suggestion || null;
  const sizeNames = data.sizes.map((s) => s.name);
  for (const size of data.sizes) {
    sizesResults.appendChild(renderSizeCard(size, suggestion, sizeNames));
  }
}

function renderSizeCard(size, suggestion, sizeNames) {
  const card = document.createElement('div');
  card.className = 'size-card';

  const title = document.createElement('h2');
  title.textContent = size.name + '（起针 ' + size.cast_on + '）';
  card.appendChild(title);

  const status = document.createElement('div');
  status.className = 'size-status ' + (size.ok ? 'ok' : 'error');
  if (size.ok) {
    status.textContent = '通过：' + size.rows.length + ' 行有效，最终 ' + size.final + ' 针。';
  } else {
    status.textContent = '未通过：' + size.rows.length + ' 行有效，止于第 ' +
      size.error.line + ' 行，最后有效针数 ' + size.final + ' 针。';
  }
  card.appendChild(status);

  if (size.error) {
    const errBox = document.createElement('div');
    errBox.className = 'error size-error';
    errBox.textContent = formatError(size.error);
    card.appendChild(errBox);
    // 针数差额行附有修正建议时，在错误行处展示并支持一键替换
    if (suggestion && size.error.type === 'count' && size.error.line === suggestion.line) {
      card.appendChild(buildSuggestionBox(suggestion, sizeNames));
    }
  }

  if (size.yarn) {
    card.appendChild(buildYarnBox(size.yarn));
  }

  if (size.rows.length > 0) {
    card.appendChild(buildRowsTable(size.rows, Boolean(size.yarn)));
  }
  return card;
}

function formatGrams(value) {
  return Number(value).toLocaleString('zh-CN', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  });
}

function buildYarnBox(yarn) {
  const box = document.createElement('div');
  box.className = 'yarn-box';

  const title = document.createElement('div');
  title.className = 'yarn-title';
  title.textContent = '线材用量';
  box.appendChild(title);

  const lines = [
    '原始总量：' + formatGrams(yarn.raw_total) + ' g',
    '含损耗（+' + yarn.waste_percent + '%）：' + formatGrams(yarn.with_waste) + ' g',
    '线团数（每团 ' + formatGrams(yarn.grams_per_ball) + ' g，向上取整）：' + yarn.balls + ' 团',
  ];
  for (const text of lines) {
    const div = document.createElement('div');
    div.className = 'yarn-line';
    div.textContent = text;
    box.appendChild(div);
  }

  if (yarn.partial) {
    const tag = document.createElement('div');
    tag.className = 'yarn-partial';
    tag.textContent = '部分估算：该尺码在出错行停止，未包含后续行用量';
    box.appendChild(tag);
  }
  return box;
}

function formatError(err) {
  if (err.type === 'parse') {
    return '第 ' + err.line + ' 行指令无法解析：' + err.message;
  }
  if (err.type === 'count') {
    const kind = err.diff > 0 ? '针数不足' : '针数超出';
    return '第 ' + err.line + ' 行' + kind + '（差额 ' + Math.abs(err.diff) + ' 针）：' + err.message;
  }
  return err.message;
}

function buildSuggestionBox(sug, sizeNames) {
  const box = document.createElement('div');
  box.className = 'suggestion-box';

  const title = document.createElement('div');
  title.className = 'suggestion-title';
  title.textContent = '倍数修正建议（第 ' + sug.line + ' 行，第 ' + sug.char + ' 个字符起）';
  box.appendChild(title);

  const lines = [
    '将 ' + sug.old + ' 改为 ' + sug.new,
    '替换后：' + sug.instruction,
    '各尺码产出：' + sug.produces
      .map((p, i) => (sizeNames[i] || '尺码' + (i + 1)) + ' ' + p + ' 针')
      .join('，'),
  ];
  for (const text of lines) {
    const div = document.createElement('div');
    div.className = 'suggestion-text';
    div.textContent = text;
    box.appendChild(div);
  }

  const applyBtn = document.createElement('button');
  applyBtn.type = 'button';
  applyBtn.className = 'small apply-suggestion';
  applyBtn.textContent = '应用并重新校验';
  applyBtn.addEventListener('click', () => applySuggestion(sug));
  box.appendChild(applyBtn);
  return box;
}

function applySuggestion(sug) {
  const lines = patternInput.value.split('\n');
  if (sug.line < 1 || sug.line > lines.length) {
    return;
  }
  lines[sug.line - 1] = sug.instruction;
  patternInput.value = lines.join('\n');
  form.requestSubmit();  // 替换后自动重新校验
}

function buildRowsTable(rows, withYarn) {
  const table = document.createElement('table');
  const thead = document.createElement('thead');
  const headRow = document.createElement('tr');
  const labels = ['行号', '指令', '入针', '消耗', '产出', '出针'];
  if (withYarn) {
    labels.push('累计克重');
  }
  for (const label of labels) {
    const th = document.createElement('th');
    th.textContent = label;
    headRow.appendChild(th);
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = document.createElement('tbody');
  for (const row of rows) {
    const tr = document.createElement('tr');
    const values = [row.line, row.instruction, row.in, row.consume, row.produce, row.out];
    if (withYarn) {
      values.push(formatGrams(row.weight) + ' g');
    }
    for (const value of values) {
      const td = document.createElement('td');
      td.textContent = value;
      tr.appendChild(td);
    }
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  return table;
}

// 初始填入示例尺码，便于直接体验
for (const s of EXAMPLE_SIZES) {
  addSizeRow(s.name, s.cast_on);
}

// ---------------------------------------------------------------------------
// 单行针位预览
// ---------------------------------------------------------------------------

const previewLineSelect = document.getElementById('preview-line');
const previewSizeSelect = document.getElementById('preview-size');
const previewStartInput = document.getElementById('preview-start');
const previewGoBtn = document.getElementById('preview-go');
const previewMessage = document.getElementById('preview-message');
const previewResult = document.getElementById('preview-result');
const previewMeta = document.getElementById('preview-meta');
const previewPageInfo = document.getElementById('preview-page-info');
const previewTableBody = document.querySelector('#preview-table tbody');
const previewFirstBtn = document.getElementById('preview-first');
const previewPrevBtn = document.getElementById('preview-prev');
const previewNextBtn = document.getElementById('preview-next');
const previewLastBtn = document.getElementById('preview-last');
const previewJumpInput = document.getElementById('preview-jump');
const previewJumpBtn = document.getElementById('preview-jump-btn');
const PREVIEW_LIMIT = 50;

// 最近一次预览结果：缓存 total/行/尺码，翻页与跳首尾时复用
let previewState = null;

function fillPreviewLineSelect() {
  const previous = previewLineSelect.value;
  previewLineSelect.replaceChildren();
  const lines = patternInput.value.split('\n');
  let found = false;
  lines.forEach((raw, i) => {
    const lineno = i + 1;
    const text = raw.trim();
    const option = document.createElement('option');
    option.value = String(lineno);
    if (text) {
      const snippet = text.length > 30 ? text.slice(0, 30) + '…' : text;
      option.textContent = '第 ' + lineno + ' 行：' + snippet;
    } else {
      option.textContent = '第 ' + lineno + ' 行（空行）';
      option.disabled = true;
    }
    previewLineSelect.appendChild(option);
    if (String(lineno) === previous) {
      option.selected = true;
      found = true;
    }
  });
  if (!found) {
    const firstUsable = Array.from(previewLineSelect.options).find((o) => !o.disabled);
    if (firstUsable) {
      firstUsable.selected = true;
    }
  }
}

function fillPreviewSizeSelect() {
  const previous = previewSizeSelect.value;
  previewSizeSelect.replaceChildren();
  const sizes = readSizes();
  let found = false;
  sizes.forEach((s, i) => {
    const option = document.createElement('option');
    option.value = String(i);
    option.textContent = s.name + '（起针 ' + (s.cast_on || '?') + '）';
    previewSizeSelect.appendChild(option);
    if (String(i) === previous) {
      option.selected = true;
      found = true;
    }
  });
  if (!found && previewSizeSelect.options.length > 0) {
    previewSizeSelect.options[0].selected = true;
  }
}

function showPreviewMessage(text) {
  previewMessage.textContent = text;
  previewMessage.className = 'move-message error';
  previewMessage.hidden = false;
}

function hidePreviewMessage() {
  previewMessage.hidden = true;
  previewMessage.textContent = '';
}

function currentPreviewStart() {
  const value = Number(previewStartInput.value);
  return Number.isInteger(value) && value >= 1 ? value : 1;
}

async function requestPreview(start) {
  fillPreviewLineSelect();
  fillPreviewSizeSelect();
  hidePreviewMessage();

  const lineOpt = previewLineSelect.selectedOptions[0];
  const sizeOpt = previewSizeSelect.selectedOptions[0];
  if (!lineOpt) {
    showPreviewMessage('请先在上方填写至少一行图解指令。');
    previewResult.hidden = true;
    return;
  }
  if (!sizeOpt) {
    showPreviewMessage('请先在上方填写至少一个有效尺码（名称与正整数起针数）。');
    previewResult.hidden = true;
    return;
  }

  let data;
  try {
    const res = await fetch('/api/preview-row', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sizes: readSizes(),
        pattern: patternInput.value,
        line: Number(lineOpt.value),
        size_index: Number(sizeOpt.value),
        start: start,
      }),
    });
    data = await res.json();
  } catch (err) {
    showPreviewMessage('预览请求失败：' + err.message);
    return;
  }

  if (!data.ok) {
    // 非法行返回现有语法错误与原文位置，不显示部分预览
    previewResult.hidden = true;
    showPreviewMessage(formatPreviewError(data.error));
    if (data.error && data.error.type === 'start' && data.error.total !== undefined) {
      // 把起始序号修正为本行最后一个针法，便于直接再点“预览”看尾段
      previewStartInput.value = String(data.error.total);
      previewState = null;
    }
    return;
  }

  previewStartInput.value = String(data.start);
  previewState = {
    line: data.line,
    sizeIndex: data.size_index,
    total: data.total,
    data: data,
  };
  renderPreview(data);
}

function formatPreviewError(err) {
  if (!err) {
    return '预览失败：未知错误';
  }
  if (err.type === 'parse') {
    return '第 ' + err.line + ' 行指令无法解析（第 ' + (err.char || '?') +
      ' 个字符）：' + err.message;
  }
  if (err.type === 'start') {
    return err.message + '，请把起始序号改到 1~' + err.total + ' 之间。';
  }
  if (err.type === 'line') {
    return '无法预览该行：' + err.message;
  }
  if (err.type === 'size_index') {
    return '尺码选择无效：' + err.message;
  }
  return '预览失败：' + err.message;
}

function renderPreview(data) {
  previewResult.hidden = false;
  hidePreviewMessage();

  previewMeta.textContent =
    '第 ' + data.line + ' 行 ' + data.instruction +
    '　｜　尺码 ' + data.size +
    '　｜　实际针法共 ' + data.total.toLocaleString('zh-CN') + ' 个' +
    '　｜　整行消耗 ' + data.consume.toLocaleString('zh-CN') +
    ' 针，产出 ' + data.produce.toLocaleString('zh-CN') + ' 针';

  const first = data.items.length ? data.items[0].seq : data.start;
  const last = data.items.length ? data.items[data.items.length - 1].seq : data.start - 1;
  const lastPageStart = data.total - ((data.total - 1) % data.limit);
  previewPageInfo.textContent =
    '显示第 ' + first.toLocaleString('zh-CN') + '–' + last.toLocaleString('zh-CN') +
    ' 个（共 ' + data.total.toLocaleString('zh-CN') + ' 个）';

  previewFirstBtn.disabled = data.start <= 1;
  previewPrevBtn.disabled = data.start <= 1 || data.prev_start === data.start;
  previewNextBtn.disabled = data.next_start === null;
  previewLastBtn.disabled = data.next_start === null && data.start === lastPageStart;
  previewJumpInput.max = String(data.total);

  previewTableBody.replaceChildren();
  for (const item of data.items) {
    const tr = document.createElement('tr');
    tr.className = item.stitch === 'yo' ? 'preview-yo' : '';

    const seqTd = document.createElement('td');
    seqTd.textContent = item.seq.toLocaleString('zh-CN');

    const stitchTd = document.createElement('td');
    stitchTd.textContent = item.stitch;
    stitchTd.className = 'preview-stitch';

    const inTd = document.createElement('td');
    const insertTd = document.createElement('td');
    if (item.in_from === null) {
      // 零入针针法：标明插在第几针之后/最前
      inTd.textContent = '—（零入针）';
      inTd.className = 'preview-zero';
      insertTd.textContent =
        item.consume_before === 0
          ? '行首（第 1 针之前）'
          : '第 ' + item.consume_before.toLocaleString('zh-CN') + ' 针之后';
    } else {
      inTd.textContent = item.in_from === item.in_to
        ? '第 ' + item.in_from.toLocaleString('zh-CN') + ' 针'
        : '第 ' + item.in_from.toLocaleString('zh-CN') + '–' +
          item.in_to.toLocaleString('zh-CN') + ' 针';
      insertTd.textContent = '—';
    }

    const outTd = document.createElement('td');
    outTd.textContent = '第 ' + item.out.toLocaleString('zh-CN') + ' 针';

    tr.appendChild(seqTd);
    tr.appendChild(stitchTd);
    tr.appendChild(inTd);
    tr.appendChild(insertTd);
    tr.appendChild(outTd);
    previewTableBody.appendChild(tr);
  }
}

previewGoBtn.addEventListener('click', () => requestPreview(currentPreviewStart()));
previewStartInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    requestPreview(currentPreviewStart());
  }
});
previewFirstBtn.addEventListener('click', () => requestPreview(1));
previewPrevBtn.addEventListener('click', () => {
  if (previewState && previewState.data) {
    requestPreview(previewState.data.prev_start);
  }
});
previewNextBtn.addEventListener('click', () => {
  if (previewState && previewState.data && previewState.data.next_start !== null) {
    requestPreview(previewState.data.next_start);
  }
});
previewLastBtn.addEventListener('click', () => {
  if (previewState) {
    requestPreview(previewState.total - ((previewState.total - 1) % PREVIEW_LIMIT));
  }
});
previewJumpBtn.addEventListener('click', () => {
  const value = Number(previewJumpInput.value);
  if (Number.isInteger(value) && value >= 1) {
    requestPreview(value);
  }
});
previewJumpInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    previewJumpBtn.click();
  }
});
previewLineSelect.addEventListener('focus', fillPreviewLineSelect);
previewSizeSelect.addEventListener('focus', fillPreviewSizeSelect);
patternInput.addEventListener('input', () => {
  fillPreviewLineSelect();
  previewResult.hidden = true;
  hidePreviewMessage();
  previewState = null;
});
sizesList.addEventListener('change', fillPreviewSizeSelect);
sizesList.addEventListener('input', fillPreviewSizeSelect);

// 切换行或尺码后，上一页/尾页等基于旧行 total 的翻页状态作废
function resetPreviewState() {
  previewState = null;
  previewStartInput.value = '1';
  previewResult.hidden = true;
  hidePreviewMessage();
}
previewLineSelect.addEventListener('change', resetPreviewState);
previewSizeSelect.addEventListener('change', resetPreviewState);

// 初始填充行与尺码列表
fillPreviewLineSelect();
fillPreviewSizeSelect();
