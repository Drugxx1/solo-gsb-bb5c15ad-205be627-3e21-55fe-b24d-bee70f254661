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
