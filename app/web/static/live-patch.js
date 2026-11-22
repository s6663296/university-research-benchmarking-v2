// 輪詢區塊（[data-live-patch]）就地更新：只改內容有變的 [data-live] 元素，不整塊替換，
// 表格的搜尋、分頁、捲動位置、輸入法組字與選取的文字都不會被打斷。
// [data-live] 的組合跟上次不同（例如多了錯誤訊息）或輪詢已結束時，照常由 htmx 整塊替換。
(function () {
  'use strict';

  const signature = node => [node.textContent, ...Array.from(node.querySelectorAll('[class]:not(mark)'), el => el.className)].join('\u0000');
  const parts = root => new Map(Array.from(root.querySelectorAll('[data-live]'), node => [node.dataset.live, node]));

  document.addEventListener('htmx:beforeSwap', event => {
    const { target, serverResponse, shouldSwap } = event.detail;
    if (!shouldSwap || !target.hasAttribute('data-live-patch')) return;
    const fresh = new DOMParser().parseFromString(serverResponse, 'text/html').getElementById(target.id);
    if (!fresh?.hasAttribute('data-live-patch')) return;
    const now = parts(target), next = parts(fresh);
    if (now.size !== next.size || [...next.keys()].some(key => !now.has(key))) return;
    const tables = new Set();
    next.forEach((node, key) => {
      const old = now.get(key);
      if (signature(old) === signature(node)) return;
      old.replaceChildren(...node.childNodes);
      tables.add(old.closest('.table-scroll'));
    });
    tables.forEach(table => table?.dispatchEvent(new Event('table:change', { bubbles: true })));
    event.detail.shouldSwap = false;
  });
}());
