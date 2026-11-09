// 簡報目錄與備註：輸入目錄名稱時即時顯示連動後的頁面標題。
document.querySelectorAll('[data-report-page]').forEach((page) => {
  const input = page.querySelector('[data-toc-name]');
  const preview = page.querySelector('[data-title-preview]');
  if (!input || !preview) return;
  const update = () => {
    const name = input.value.trim();
    preview.textContent = !name || name === input.dataset.defaultName
      ? input.dataset.defaultTitle
      : input.dataset.titlePrefix + name;
  };
  input.addEventListener('input', update);
  update();
});
