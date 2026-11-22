// 壓縮檔下載（a[data-export]）：在背景產生，按鈕旁顯示進度條，完成後才下載；失敗時顯示原因。
(function () {
  'use strict';

  const POLL_MS = 500;

  function status(link) {
    let box = link.parentElement.querySelector('.export-status');
    if (!box) {
      box = document.createElement('span');
      box.className = 'export-status';
      box.setAttribute('role', 'status');
      box.innerHTML = '<progress max="1" value="0"></progress><span class="export-count"></span>';
      link.after(box);
    }
    return box;
  }

  function show(box, state) {
    box.classList.remove('error');
    const bar = box.querySelector('progress');
    const count = box.querySelector('.export-count');
    if (bar && state.total) {
      bar.max = state.total;
      bar.value = state.done;
    }
    if (count) count.textContent = state.total ? `${state.done}／${state.total} 份` : '準備中…';
  }

  function fail(box, message) {
    box.classList.add('error');
    box.textContent = message;
  }

  async function detail(resp) {
    try { return (await resp.json()).detail || `HTTP ${resp.status}`; } catch { return `HTTP ${resp.status}`; }
  }

  async function run(link) {
    const url = link.dataset.export;
    link.classList.add('busy');
    link.setAttribute('aria-disabled', 'true');
    const box = status(link);
    show(box, { done: 0, total: 0 });
    try {
      let resp = await fetch(url, { method: 'POST' });
      while (true) {
        if (!resp.ok) return fail(box, await detail(resp));
        const state = await resp.json();
        if (state.error) return fail(box, state.error);
        show(box, state);
        if (state.ready) break;
        await new Promise(r => setTimeout(r, POLL_MS));
        resp = await fetch(url);
      }
      box.querySelector('.export-count').textContent = '完成，開始下載';
      location.href = url + '/file';   // 回應是附件，瀏覽器直接存檔，不會離開頁面
      setTimeout(() => box.remove(), 3000);
    } catch (e) {
      fail(box, '無法連線到系統，請確認程式仍在執行');
    } finally {
      link.classList.remove('busy');
      link.removeAttribute('aria-disabled');
    }
  }

  document.addEventListener('click', event => {
    const link = event.target.closest('a[data-export]');
    if (!link || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (!link.classList.contains('busy')) run(link);
  });
}());
