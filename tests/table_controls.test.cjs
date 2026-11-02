const { test } = require('node:test');
const assert = require('node:assert/strict');
const { paginate, highlightParts } = require('../app/web/static/table-controls.js');

test('searches all rows and paginates matched results', () => {
  const rows = Array.from({ length: 53 }, (_, i) => `Unit ${i + 1}`);
  assert.deepEqual(paginate(rows, '', 1, 20), { indices: Array.from({ length: 20 }, (_, i) => i + 20), page: 1, pages: 3, total: 53 });
  assert.deepEqual(paginate(rows, 'UNIT 5', 0, 20), { indices: [4, 49, 50, 51, 52], page: 0, pages: 1, total: 5 });
});

test('clamps stale pages and handles an empty search result', () => {
  assert.deepEqual(paginate(['alpha', 'beta'], 'none', 4, 20), { indices: [], page: 0, pages: 0, total: 0 });
  assert.deepEqual(paginate(['alpha', 'beta'], '', 4, 1), { indices: [1], page: 1, pages: 2, total: 2 });
});

test('finds abbreviations in order and highlights their characters', () => {
  assert.deepEqual(paginate(['資訊管理學系', '管理資訊學系', '資管系', '資訊工程學系'], '資管', 0, 20),
    { indices: [0, 2], page: 0, pages: 1, total: 2 });
  assert.deepEqual(highlightParts('資訊管理學系', '資管'), [
    { text: '資', match: true }, { text: '訊', match: false },
    { text: '管', match: true }, { text: '理學系', match: false },
  ]);
  assert.deepEqual(paginate(['AxxB', 'AB'], 'AB', 0, 20).indices, [1]);
  assert.deepEqual(paginate([['資訊', '管理'], ['資訊管理']], '資管', 0, 20).indices, [1]);
});

test('finds all literal matches while preserving the original text', () => {
  assert.deepEqual(highlightParts('SciVal scival (A+B) A+B', 'SCIVAL'), [
    { text: 'SciVal', match: true }, { text: ' ', match: false },
    { text: 'scival', match: true }, { text: ' (A+B) A+B', match: false },
  ]);
  assert.deepEqual(highlightParts('A+B and AAB', 'A+B'), [
    { text: 'A+B', match: true }, { text: ' and AAB', match: false },
  ]);
  assert.deepEqual(highlightParts('資料', '  '), [{ text: '資料', match: false }]);
});
