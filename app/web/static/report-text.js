// 簡報目錄與備註：左側清單一次編輯一項；文字裡的變數以藍色標籤顯示，送出時仍是 {變數}。
(() => {
  const form = document.querySelector('form.report-text');
  if (!form) return;

  const variables = JSON.parse(document.getElementById('report-variables').textContent);
  const pattern = new RegExp(`\\{(${Object.keys(variables).join('|')})\\}`, 'g');
  const ZWSP = '​';  // 讓游標能停在標籤前方，送出時會移除
  const fillSamples = (text) => text.replace(pattern, (_, name) => variables[name].sample);

  function createToken(name) {
    const token = document.createElement('span');
    token.className = 'var-token';
    token.contentEditable = 'false';
    token.dataset.variable = name;
    token.textContent = variables[name].sample;
    token.title = `${variables[name].label}（依各系所自動帶入）`;
    return token;
  }

  function parse(text) {
    const fragment = document.createDocumentFragment();
    let last = 0;
    for (const match of text.matchAll(pattern)) {
      if (match.index > last) fragment.append(text.slice(last, match.index));
      fragment.append(createToken(match[1]));
      last = match.index + match[0].length;
    }
    if (last < text.length) fragment.append(text.slice(last));
    return fragment;
  }

  function serialize(root) {
    let text = '';
    root.childNodes.forEach((node) => {
      if (node.nodeType === Node.TEXT_NODE) text += node.data.replaceAll(ZWSP, '');
      else if (node.dataset?.variable) text += `{${node.dataset.variable}}`;
      else if (node.nodeName === 'BR') text += '\n';
      else text += serialize(node);
    });
    return text;
  }

  // 結尾固定保留一個換行，游標才能停在最後一個標籤或空行之後
  function normalize(editor) {
    if (editor.lastChild?.nodeName === 'BR') editor.lastChild.remove();
    editor.querySelectorAll('.var-token').forEach((token) => {
      const before = token.previousSibling;
      if (!(before?.nodeType === Node.TEXT_NODE && before.data.length)) token.before(ZWSP);
    });
    const end = editor.lastChild;
    if (!(end?.nodeType === Node.TEXT_NODE && end.data.endsWith('\n'))) editor.append('\n');
  }

  const isBlank = (node) => node?.nodeType === Node.TEXT_NODE && !node.data.replaceAll(ZWSP, '');

  // 游標緊鄰的標籤（Backspace／Delete 要整個刪除）
  function tokenBesideCaret(backwards) {
    const selection = getSelection();
    if (!selection.isCollapsed) return null;
    let node = selection.anchorNode;
    const offset = selection.anchorOffset;
    const step = backwards ? 'previousSibling' : 'nextSibling';
    if (node.nodeType === Node.TEXT_NODE) {
      const rest = backwards ? node.data.slice(0, offset) : node.data.slice(offset);
      if (rest.replaceAll(ZWSP, '')) return null;
      node = node[step];
    } else {
      node = node.childNodes[backwards ? offset - 1 : offset];
    }
    while (isBlank(node)) node = node[step];
    return node?.dataset?.variable ? node : null;
  }

  function enhance(control, names) {
    const multiline = control.tagName === 'TEXTAREA';
    const field = control.closest('.field');
    const label = field.querySelector('label');
    const editor = document.createElement('div');
    editor.className = multiline ? 'variable-editor multiline' : 'variable-editor';
    editor.contentEditable = 'true';
    editor.spellcheck = false;
    editor.setAttribute('role', 'textbox');
    if (multiline) editor.setAttribute('aria-multiline', 'true');
    label.id = `${control.id}-label`;
    editor.setAttribute('aria-labelledby', label.id);
    editor.append(parse(control.value));
    normalize(editor);
    control.hidden = true;
    control.after(editor);

    let previous = control.value;
    let savedRange = null;

    function caretBeforeEnd() {
      const range = document.createRange();
      range.setStart(editor, Math.max(editor.childNodes.length - 1, 0));
      range.collapse(true);
      const selection = getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
    }

    function sync() {
      normalize(editor);
      const value = serialize(editor).replace(/\n$/, '');
      const limit = control.maxLength;
      if (limit > 0 && value.length > limit && value.length > previous.length) {  // 超過字數上限：退回上一次的內容
        editor.replaceChildren(parse(previous));
        normalize(editor);
        caretBeforeEnd();
        return;
      }
      previous = value;
      control.value = value;
      field.querySelector('.field-error')?.remove();
      control.dispatchEvent(new Event('input', { bubbles: true }));
    }

    function insert(text) {
      const selection = getSelection();
      if (!selection.rangeCount || !editor.contains(selection.anchorNode)) caretBeforeEnd();
      const range = selection.getRangeAt(0);
      range.deleteContents();
      const fragment = parse(text);
      const last = fragment.lastChild;
      if (!last) return sync();
      range.insertNode(fragment);
      range.setStartAfter(last);
      range.collapse(true);
      selection.removeAllRanges();
      selection.addRange(range);
      sync();
    }

    function copySelection(event) {
      const selection = getSelection();
      if (selection.isCollapsed) return false;
      event.preventDefault();
      event.clipboardData.setData('text/plain', serialize(selection.getRangeAt(0).cloneContents()));  // 複製時保留變數
      return true;
    }

    editor.addEventListener('input', (event) => { if (!event.isComposing) sync(); });
    editor.addEventListener('compositionend', sync);
    editor.addEventListener('beforeinput', (event) => { if (event.inputType.startsWith('format')) event.preventDefault(); });
    editor.addEventListener('keydown', (event) => {
      if (event.isComposing || event.keyCode === 229) return;  // 中文輸入法選字中
      if (event.key === 'Enter') {
        event.preventDefault();
        if (multiline) insert('\n');
      } else if (event.key === 'Backspace' || event.key === 'Delete') {
        const token = tokenBesideCaret(event.key === 'Backspace');
        if (token) {
          event.preventDefault();
          token.remove();
          sync();
        }
      }
    });
    editor.addEventListener('paste', (event) => {
      event.preventDefault();
      const text = event.clipboardData.getData('text/plain').replace(/\r\n?/g, '\n');
      insert(multiline ? text : text.replace(/\n+/g, ' '));
    });
    editor.addEventListener('copy', copySelection);
    editor.addEventListener('cut', (event) => {
      if (copySelection(event)) {
        getSelection().deleteFromDocument();
        sync();
      }
    });
    editor.addEventListener('drop', (event) => event.preventDefault());
    editor.addEventListener('dragstart', (event) => event.preventDefault());
    label.addEventListener('click', (event) => {
      event.preventDefault();
      editor.focus();
    });
    document.addEventListener('selectionchange', () => {
      const selection = getSelection();
      if (selection.rangeCount && editor.contains(selection.anchorNode)) savedRange = selection.getRangeAt(0).cloneRange();
    });

    const menu = document.createElement('select');
    menu.className = 'variable-insert';
    menu.setAttribute('aria-label', `在「${label.textContent}」插入自動資料`);
    menu.add(new Option('＋ 插入自動資料', ''));
    names.forEach((name) => menu.add(new Option(`${variables[name].label}（${variables[name].sample}）`, name)));
    menu.addEventListener('change', () => {
      const name = menu.value;
      menu.value = '';
      if (!name) return;
      editor.focus();
      if (savedRange) {
        const selection = getSelection();
        selection.removeAllRanges();
        selection.addRange(savedRange);
      }
      insert(`{${name}}`);
    });
    const head = document.createElement('div');
    head.className = 'field-head';
    field.insertBefore(head, label);
    head.append(label, menu);
    return editor;
  }

  // ---- 文字欄位 ----
  // 區塊 id 加了 page- 前綴：網址的 #項目 不對應任何元素，瀏覽器載入時就不會自動捲走上方選單
  const pages = new Map([...form.querySelectorAll('[data-report-page]')].map((page) => [page.dataset.reportPage, page]));
  const editors = new Map();
  pages.forEach((page) => {
    const names = page.dataset.variables.split(',');
    page.querySelectorAll('[data-variable-text]').forEach((control) => editors.set(control, enhance(control, names)));
    const input = page.querySelector('[data-toc-name]');
    const preview = page.querySelector('[data-title-preview]');
    if (!preview) return;
    const update = () => {
      const name = input.value.trim();
      preview.textContent = fillSamples(!name || name === input.dataset.defaultName
        ? input.dataset.defaultTitle
        : input.dataset.titlePrefix + name);
    };
    input.addEventListener('input', update);
    update();
  });

  // ---- 左側清單 ----
  const links = [...form.querySelectorAll('[data-report-link]')];
  function activate(key) {
    pages.forEach((page, id) => { page.hidden = id !== key; });
    links.forEach((link) => {
      if (link.dataset.reportLink === key) link.setAttribute('aria-current', 'true');
      else link.removeAttribute('aria-current');
    });
    form.elements.active_key.value = key;
    history.replaceState(null, '', `#${key}`);
  }
  links.forEach((link) => link.addEventListener('click', (event) => {
    event.preventDefault();
    activate(link.dataset.reportLink);
  }));
  const fromUrl = decodeURIComponent(location.hash.slice(1));
  activate(pages.has(fromUrl) ? fromUrl : pages.keys().next().value);

  // ---- 送出：原生的必填提示無法指向隱藏欄位，改由這裡切到該項目並提示 ----
  form.noValidate = true;
  form.addEventListener('submit', (event) => {
    if (event.submitter?.formNoValidate) return;
    const empty = [...form.querySelectorAll('[data-toc-name]')].find((control) => !control.value.trim());
    if (!empty) return;
    event.preventDefault();
    activate(empty.closest('[data-report-page]').dataset.reportPage);
    const editor = editors.get(empty);
    if (!editor.parentElement.querySelector('.field-error')) {
      const error = document.createElement('p');
      error.className = 'field-error';
      error.setAttribute('role', 'alert');
      error.textContent = '目錄名稱不可空白';
      editor.after(error);
    }
    editor.focus();
  });
})();
