(function () {
  'use strict';

  function paginate(rows, query, page, size) {
    const term = query.trim().toLocaleLowerCase();
    const matches = [];
    rows.forEach((text, index) => {
      const fields = Array.isArray(text) ? text : [text];
      if (!term || fields.some(field => highlightParts(field, term).some(part => part.match))) matches.push(index);
    });
    const pages = Math.ceil(matches.length / size);
    const current = Math.max(0, Math.min(page, Math.max(0, pages - 1)));
    return { indices: matches.slice(current * size, (current + 1) * size), page: current, pages, total: matches.length };
  }

  function highlightParts(text, query) {
    const term = query.trim();
    if (!term) return [{ text, match: false }];
    const pattern = new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'gi');
    const parts = [];
    let last = 0;
    for (const found of text.matchAll(pattern)) {
      if (found.index > last) parts.push({ text: text.slice(last, found.index), match: false });
      parts.push({ text: found[0], match: true });
      last = found.index + found[0].length;
    }
    if (!parts.length && /^[\u3400-\u9fff]{2,}$/u.test(term)) {
      const positions = [];
      let from = 0;
      for (const character of term) {
        const index = text.indexOf(character, from);
        if (index < 0) return [{ text, match: false }];
        positions.push(index);
        from = index + character.length;
      }
      for (const index of positions) {
        if (index > last) parts.push({ text: text.slice(last, index), match: false });
        parts.push({ text: text[index], match: true });
        last = index + 1;
      }
    }
    if (last < text.length) parts.push({ text: text.slice(last), match: false });
    return parts;
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { paginate, highlightParts };
  if (typeof document === 'undefined') return;

  const saved = new Map();
  const controllers = new WeakMap();
  const pageSize = 20;

  function highlight(row, query) {
    const term = query.trim().toLocaleLowerCase();
    row.querySelectorAll('option').forEach(option => {
      option.classList.toggle('table-option-match', Boolean(term) && highlightParts(option.textContent, term).some(part => part.match));
    });
    if (term) {
      row.querySelectorAll('select[multiple], select[size]').forEach(select => {
        const match = select.querySelector('option.table-option-match');
        if (!match || select.size === 1) return;
        const optionRect = match.getBoundingClientRect();
        const selectRect = select.getBoundingClientRect();
        select.scrollTop += optionRect.top - selectRect.top - (select.clientHeight - optionRect.height) / 2;
      });
    }
    row.querySelectorAll('mark.table-match').forEach(mark => {
      const parent = mark.parentNode;
      mark.replaceWith(document.createTextNode(mark.textContent));
      parent.normalize();
    });
    if (!term) return;
    const walker = document.createTreeWalker(row, NodeFilter.SHOW_TEXT, {
      acceptNode: node => node.parentElement.closest('select, textarea, script, style')
        ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT,
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);
    nodes.forEach(node => {
      const parts = highlightParts(node.data, query);
      if (!parts.some(part => part.match)) return;
      const fragment = document.createDocumentFragment();
      parts.forEach(part => {
        if (part.match) {
          const mark = document.createElement('mark');
          mark.className = 'table-match';
          mark.textContent = part.text;
          fragment.append(mark);
        } else fragment.append(document.createTextNode(part.text));
      });
      node.replaceWith(fragment);
    });
  }

  function setup(container, state) {
    if (controllers.has(container)) return;
    const table = container.querySelector('table');
    if (!table) return;
    const rows = Array.from(table.rows);
    // Most tables have one header row; the notes table uses row headers instead.
    if (rows.length && Array.from(rows[0].cells).every(cell => cell.tagName === 'TH')) rows.shift();

    const toolbar = document.createElement('div');
    toolbar.className = 'table-toolbar';
    const search = document.createElement('input');
    search.type = 'search';
    search.placeholder = '搜尋表格';
    search.setAttribute('aria-label', '搜尋表格');
    const status = document.createElement('span');
    status.className = 'table-page-status';
    status.setAttribute('aria-live', 'polite');
    const previous = document.createElement('button');
    previous.type = 'button';
    previous.className = 'secondary';
    previous.textContent = '‹';
    previous.setAttribute('aria-label', '上一頁');
    const next = document.createElement('button');
    next.type = 'button';
    next.className = 'secondary';
    next.textContent = '›';
    next.setAttribute('aria-label', '下一頁');
    toolbar.append(search, status, previous, next);
    const view = document.createElement('div');
    view.className = 'table-view';
    container.before(view);
    view.append(toolbar, container);

    let page = state?.page || 0;
    search.value = state?.query || '';
    function render() {
      const result = paginate(rows.map(row => Array.from(row.cells, cell => {
        const clone = cell.cloneNode(true);
        clone.querySelectorAll('option').forEach(option => option.before(document.createTextNode('\n')));
        return clone.textContent;
      }).flatMap(text => text.split('\n'))), search.value, page, pageSize);
      page = result.page;
      const visible = new Set(result.indices);
      rows.forEach((row, index) => {
        row.hidden = !visible.has(index);
        if (!row.hidden || row.querySelector('mark.table-match, option.table-option-match')) highlight(row, row.hidden ? '' : search.value);
      });
      status.textContent = result.total ? `${page + 1} / ${result.pages} 頁・${result.total} 筆` : '無符合資料';
      previous.disabled = page === 0;
      next.disabled = page + 1 >= result.pages;
    }
    search.addEventListener('input', () => { page = 0; render(); });
    previous.addEventListener('click', () => { page--; render(); });
    next.addEventListener('click', () => { page++; render(); });
    controllers.set(container, { snapshot: () => ({ query: search.value, page, focused: document.activeElement === search, start: search.selectionStart, end: search.selectionEnd }) });
    render();
    if (state?.focused) {
      search.focus();
      search.setSelectionRange(state.start, state.end);
    }
  }

  function initialize(root, state) {
    if (!(root instanceof Element)) return;
    const containers = [];
    if (root.matches('.table-scroll')) containers.push(root);
    containers.push(...root.querySelectorAll('.table-scroll'));
    containers.forEach((container, index) => setup(container, state?.[index]));
  }

  document.addEventListener('DOMContentLoaded', () => initialize(document.body));
  document.addEventListener('htmx:beforeSwap', event => {
    const target = event.detail.target;
    if (!target?.id) return;
    const containers = target.matches('.table-scroll') ? [target] : Array.from(target.querySelectorAll('.table-scroll'));
    if (containers.length) saved.set(target.id, containers.map(container => controllers.get(container)?.snapshot()));
  });
  document.addEventListener('htmx:load', event => {
    const root = event.detail.elt;
    const state = root.id ? saved.get(root.id) : undefined;
    initialize(root, state);
    if (root.id) saved.delete(root.id);
  });
}());
