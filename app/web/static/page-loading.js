// 整頁換頁（點連結、送出表單）時，在畫面中央顯示轉圈，跟瀏覽器分頁上的轉圈同步。
// htmx 請求、開新分頁、下載（a[download]）與同頁錨點不會顯示。
(function () {
  'use strict';

  const DELAY_MS = 200;
  let timer = null;

  const overlay = () => document.getElementById('page-loading');

  function show() {
    clearTimeout(timer);
    timer = setTimeout(() => overlay()?.classList.add('visible'), DELAY_MS);
  }

  function hide() {
    clearTimeout(timer);
    overlay()?.classList.remove('visible');
  }

  document.addEventListener('click', event => {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href]');
    if (!link || link.hasAttribute('download')) return;
    if (link.target && link.target !== '_self') return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin) return;
    if (url.hash && url.pathname === location.pathname && url.search === location.search) return;
    show();
  });

  document.addEventListener('submit', event => {
    if (event.defaultPrevented) return;
    const form = event.target;
    const target = event.submitter?.getAttribute('formtarget') || form.target;
    if (target && target !== '_self') return;
    show();
  });

  // 從上一頁快取（bfcache）回來時，畫面會停在離開前的樣子，要收起來。
  window.addEventListener('pageshow', hide);
}());
