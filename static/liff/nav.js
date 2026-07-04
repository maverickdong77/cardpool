/* 共用底部導航列 — 在所有頁面引入此檔即可 */
(function () {
  const path = location.pathname;

  // 注入 CSS
  const style = document.createElement('style');
  style.textContent = `
    body { padding-bottom: 68px; }
    .cp-nav {
      position: fixed; bottom: 0; left: 50%; transform: translateX(-50%);
      width: 100%; max-width: 500px;
      background: #fff;
      border-top: 1px solid #eee;
      display: flex;
      box-shadow: 0 -4px 16px rgba(0,0,0,.07);
      z-index: 999;
    }
    .cp-nav-btn {
      flex: 1; display: flex; flex-direction: column; align-items: center;
      justify-content: center; gap: 4px;
      padding: 10px 4px 12px;
      border: none; background: #fff; cursor: pointer;
      color: #bbb; font-size: .68rem; font-weight: 600;
      transition: color .15s;
      text-decoration: none;
    }
    .cp-nav-btn .cp-nav-icon { font-size: 1.4rem; line-height: 1; }
    .cp-nav-btn.active { color: #be9682; }
    .cp-nav-btn:hover { color: #333; }

    /* Modal 遮罩 */
    .cp-modal-overlay {
      display: none; position: fixed; inset: 0;
      background: rgba(0,0,0,.45); z-index: 1000;
      align-items: flex-end; justify-content: center;
    }
    .cp-modal-overlay.open { display: flex; }
    .cp-modal-sheet {
      background: #fff; width: 100%; max-width: 500px;
      border-radius: 20px 20px 0 0;
      padding: 0 0 32px;
      max-height: 80vh; overflow-y: auto;
    }
    .cp-modal-handle {
      width: 40px; height: 4px; background: #e0e0e0;
      border-radius: 2px; margin: 12px auto 0;
    }
    .cp-modal-header {
      padding: 16px 20px 12px;
      border-bottom: 1px solid #f0f0f0;
      display: flex; align-items: center; justify-content: space-between;
    }
    .cp-modal-header h2 { font-size: 1rem; font-weight: 700; }
    .cp-modal-close {
      background: none; border: none; font-size: 1.3rem; cursor: pointer; color: #aaa;
    }
    .cp-modal-body { padding: 20px; font-size: .9rem; color: #555; line-height: 1.8; }
    .cp-modal-body h3 { font-size: .95rem; font-weight: 700; color: #333; margin: 16px 0 6px; }
    .cp-modal-body p { margin-bottom: 10px; }
    .cp-placeholder { color: #bbb; font-style: italic; }
  `;
  document.head.appendChild(style);

  // 建立底部導航
  const nav = document.createElement('nav');
  nav.className = 'cp-nav';
  nav.innerHTML = `
    <a class="cp-nav-btn ${path === '/' || path.endsWith('index.html') ? 'active' : ''}" href="/">
      <span class="cp-nav-icon">🏠</span>首頁
    </a>
    <a class="cp-nav-btn ${path.includes('settings') ? 'active' : ''}" href="/static/liff/settings.html">
      <span class="cp-nav-icon">⚙️</span>設定
    </a>
    <button class="cp-nav-btn" onclick="cpOpenModal('about')">
      <span class="cp-nav-icon">ℹ️</span>關於我們
    </button>
    <button class="cp-nav-btn" onclick="cpOpenModal('terms')">
      <span class="cp-nav-icon">📄</span>申明
    </button>
  `;
  document.body.appendChild(nav);

  // 關於我們 modal
  const aboutModal = document.createElement('div');
  aboutModal.className = 'cp-modal-overlay';
  aboutModal.id = 'cp-modal-about';
  aboutModal.innerHTML = `
    <div class="cp-modal-sheet">
      <div class="cp-modal-handle"></div>
      <div class="cp-modal-header">
        <h2>關於我們</h2>
        <button class="cp-modal-close" onclick="cpCloseModal('about')">✕</button>
      </div>
      <div class="cp-modal-body">
        <p class="cp-placeholder">（內容撰寫中，敬請期待）</p>
      </div>
    </div>
  `;
  document.body.appendChild(aboutModal);

  // 申明 modal
  const termsModal = document.createElement('div');
  termsModal.className = 'cp-modal-overlay';
  termsModal.id = 'cp-modal-terms';
  termsModal.innerHTML = `
    <div class="cp-modal-sheet">
      <div class="cp-modal-handle"></div>
      <div class="cp-modal-header">
        <h2>法律申明</h2>
        <button class="cp-modal-close" onclick="cpCloseModal('terms')">✕</button>
      </div>
      <div class="cp-modal-body">
        <p class="cp-placeholder">（法律條款撰寫中，敬請期待）</p>
      </div>
    </div>
  `;
  document.body.appendChild(termsModal);

  // 點遮罩關閉
  [aboutModal, termsModal].forEach(m => {
    m.addEventListener('click', e => { if (e.target === m) m.classList.remove('open'); });
  });

  window.cpOpenModal = function (key) {
    document.getElementById('cp-modal-' + key).classList.add('open');
  };
  window.cpCloseModal = function (key) {
    document.getElementById('cp-modal-' + key).classList.remove('open');
  };
})();
