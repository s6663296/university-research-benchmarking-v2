// 逐格來源說明：格子裡有 <template class="ev-body"> 的就是可查的格子。
// 停留或取得焦點顯示說明框；點擊格子或 Enter 打開明細抽屜（或前往指標表）；Esc 關閉。
// 觸控：第一次點顯示說明框，再點一次格子或點說明框才進入明細。
(function () {
  'use strict';

  const SHOW_DELAY = 150;
  const HIDE_DELAY = 200;
  let tip, drawer, drawerBody, current = null, showTimer = 0, hideTimer = 0, lastPointer = 'mouse';

  function build() {
    tip = document.createElement('div');
    tip.id = 'ev-tip';
    tip.className = 'ev-tip';
    tip.setAttribute('role', 'tooltip');
    tip.hidden = true;
    document.body.append(tip);

    drawer = document.createElement('aside');
    drawer.className = 'ev-drawer';
    drawer.setAttribute('aria-label', '來源明細');
    drawer.hidden = true;
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'secondary ev-close';
    close.textContent = '關閉';
    close.addEventListener('click', closeDrawer);
    drawerBody = document.createElement('div');
    drawerBody.id = 'ev-drawer-body';
    drawer.append(close, drawerBody);
    document.body.append(drawer);

    tip.addEventListener('mouseenter', () => clearTimeout(hideTimer));
    tip.addEventListener('mouseleave', scheduleHide);
    tip.addEventListener('click', () => { if (current) follow(hrefOf(current)); });
  }

  function enhance(root) {
    root.querySelectorAll('template.ev-body').forEach(template => {
      const cell = template.parentElement;
      if (!cell || cell.classList.contains('ev')) return;
      cell.classList.add('ev');
      if (!template.dataset.href) cell.classList.add('ev-nodrill');
      cell.tabIndex = 0;
    });
  }

  function cellOf(target) {
    const cell = target instanceof Element ? target.closest('.ev') : null;
    return cell && cell.querySelector(':scope > template.ev-body') ? cell : null;
  }

  function show(cell) {
    clearTimeout(hideTimer);
    if (current === cell && !tip.hidden) return;
    if (current) current.removeAttribute('aria-describedby');
    current = cell;
    tip.replaceChildren(cell.querySelector(':scope > template.ev-body').content.cloneNode(true));
    tip.hidden = false;
    cell.setAttribute('aria-describedby', 'ev-tip');
    place(cell);
  }

  function hide() {
    clearTimeout(showTimer);
    tip.hidden = true;
    if (current) current.removeAttribute('aria-describedby');
    current = null;
  }

  function scheduleHide() {
    clearTimeout(showTimer);
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hide, HIDE_DELAY);
  }

  // 預設放在格子下方，空間不夠改放上方；水平方向限制在畫面內
  function place(cell) {
    const rect = cell.getBoundingClientRect();
    const margin = 8;
    tip.style.left = '0px';
    tip.style.top = '0px';
    const width = tip.offsetWidth, height = tip.offsetHeight;
    let top = rect.bottom + 4;
    if (top + height > window.innerHeight - margin && rect.top - height - 4 > margin) top = rect.top - height - 4;
    const left = Math.max(margin, Math.min(rect.left, window.innerWidth - width - margin));
    tip.style.left = `${left + window.scrollX}px`;
    tip.style.top = `${top + window.scrollY}px`;
  }

  function hrefOf(cell) {
    return cell.querySelector(':scope > template.ev-body').dataset.href || '';
  }

  function follow(href) {
    if (!href) return;
    if (href.includes('/evidence?')) openDrawer(href);
    else window.location.href = href;
  }

  function openDrawer(url) {
    hide();
    drawer.hidden = false;
    drawerBody.textContent = '載入中…';
    htmx.ajax('GET', url, { target: '#ev-drawer-body', swap: 'innerHTML' }).then(() => {
      drawer.querySelector('.ev-close').focus();
    });
  }

  function closeDrawer() {
    drawer.hidden = true;
    drawerBody.replaceChildren();
  }

  document.addEventListener('DOMContentLoaded', () => {
    build();
    enhance(document.body);

    document.addEventListener('pointerdown', event => { lastPointer = event.pointerType || 'mouse'; }, true);
    document.addEventListener('mouseover', event => {
      const cell = cellOf(event.target);
      if (!cell) return;
      clearTimeout(hideTimer);
      clearTimeout(showTimer);
      showTimer = setTimeout(() => show(cell), SHOW_DELAY);
    });
    document.addEventListener('mouseout', event => {
      const cell = cellOf(event.target);
      if (cell && !cell.contains(event.relatedTarget) && !tip.contains(event.relatedTarget)) scheduleHide();
    });
    document.addEventListener('focusin', event => {
      const cell = cellOf(event.target);
      if (cell && event.target === cell) show(cell);
    });
    document.addEventListener('focusout', event => {
      if (cellOf(event.target) && !tip.contains(event.relatedTarget)) scheduleHide();
    });
    document.addEventListener('click', event => {
      const cell = cellOf(event.target);
      if (!cell || event.target.closest('a, button, form')) return;
      if (lastPointer === 'touch' && (current !== cell || tip.hidden)) { show(cell); return; }
      follow(hrefOf(cell));
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        if (!tip.hidden) hide();
        else if (!drawer.hidden) closeDrawer();
        return;
      }
      const cell = cellOf(event.target);
      if (event.key === 'Enter' && cell && event.target === cell) {
        event.preventDefault();
        follow(hrefOf(cell));
      }
    });
    window.addEventListener('scroll', () => { if (current) place(current); }, { passive: true });
  });

  document.addEventListener('htmx:load', event => enhance(event.detail.elt));
}());
