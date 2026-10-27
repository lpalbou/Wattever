/* Small shared table behavior: fit the panel and copy spreadsheet-friendly TSV. */
const Tables = (() => {
  const copyIcon = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2"/><path d="M12 7V4a1 1 0 0 0-1-1H4a1 1 0 0 0-1 1v7a1 1 0 0 0 1 1h3"/></svg>';
  function toTSV(table) {
    const clean = cell => (cell.innerText || cell.textContent || '').replace(/\s+/g, ' ').trim();
    return Array.from(table.querySelectorAll('thead tr, tbody tr'))
      .map(row => Array.from(row.cells).map(clean).join('\t')).join('\n');
  }
  async function copy(table) {
    const text = toTSV(table);
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) throw Error('Clipboard unavailable');
      await navigator.clipboard.writeText(text);
    } catch (error) {
      const previous = document.activeElement;
      const area = document.createElement('textarea');
      area.value = text;
      area.readOnly = true;
      area.style.cssText = 'position:fixed;left:-9999px;top:0';
      document.body.appendChild(area);
      area.select();
      const copied = document.execCommand('copy');
      area.remove();
      if (previous) previous.focus({preventScroll: true});
      if (!copied) throw error;
    }
  }
  function enhance(root = document) {
    root.querySelectorAll('table').forEach(table => {
      table.classList.add('fit-table');
      if (!table.parentElement.classList.contains('table-component')) {
        const wrapper = document.createElement('div');
        wrapper.className = 'table-component';
        const tools = document.createElement('div');
        tools.className = 'table-tools';
        const button = document.createElement('button');
        button.className = 'copy-table btn-sm';
        button.type = 'button';
        button.title = 'Copy table to clipboard';
        button.setAttribute('aria-label', button.title);
        button.innerHTML = copyIcon + '<span>Copy</span>';
        const feedback = document.createElement('span');
        feedback.className = 'copy-feedback';
        feedback.setAttribute('role', 'status');
        button.addEventListener('click', async () => {
          try { await copy(table); feedback.textContent = 'Copied'; }
          catch (error) { feedback.textContent = 'Could not copy'; }
          setTimeout(() => { feedback.textContent = ''; }, 2500);
        });
        tools.append(feedback, button);
        table.before(wrapper);
        wrapper.append(tools, table);
      }
      const headers = Array.from(table.querySelectorAll('thead th')).map(cell => cell.textContent.trim());
      table.querySelectorAll('tbody tr').forEach(row => {
        Array.from(row.cells).forEach((cell, index) => { cell.dataset.label = headers[index] || ''; });
      });
    });
  }
  function init() {
    enhance();
    new MutationObserver(() => enhance()).observe(document.body, {childList: true, subtree: true});
  }
  return {init, enhance, toTSV, copy};
})();
