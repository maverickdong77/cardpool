// Cardpool 查價機器人

const API_BASE = (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')
  ? `${window.location.protocol}//${window.location.hostname}:8000`
  : 'https://cardpool.onrender.com';
let selectedCard = null;
let currentMode = 'search';
let currentSetId = null;
let searchLang = ''; // '', 'jp', 'en'

// DOM Elements
const searchInput = document.getElementById('searchInput');
const searchBtn = document.getElementById('searchBtn');
const searchSection = document.getElementById('searchSection');
const setGrid = document.getElementById('setGrid');
const setHeader = document.getElementById('setHeader');
const cardGrid = document.getElementById('cardGrid');
const loading = document.getElementById('loading');
const emptyState = document.getElementById('emptyState');
const cardModal = document.getElementById('cardModal');
const confirmModal = document.getElementById('confirmModal');

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    bindEvents();
    showEmptyState();
});

function bindEvents() {
    // 語言切換
    document.querySelectorAll('.lang-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.lang-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            currentMode = tab.dataset.lang;

            if (currentMode === 'jp' || currentMode === 'en') {
                showCategories(currentMode);
            } else {
                showSearchMode();
            }
        });
    });

    // 搜尋按鈕
    searchBtn.addEventListener('click', handleSearch);

    // Enter 搜尋
    searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') handleSearch();
    });

    // 快速搜尋按鈕
    document.querySelectorAll('.quick-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            searchInput.value = btn.dataset.search;
            handleSearch();
        });
    });

    // 搜尋語言過濾
    document.querySelectorAll('.search-lang-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.search-lang-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            searchLang = btn.dataset.searchLang || '';
            // 若已有搜尋字串，立即重新搜尋
            if (searchInput.value.trim()) handleSearch();
        });
    });

    // 返回系列
    document.getElementById('backToSets').addEventListener('click', () => {
        currentSetId = null;
        const lang = (currentMode === 'jp' || currentMode === 'en') ? currentMode : 'jp';
        showCategories(lang);
    });

    // Modal 關閉
    document.querySelector('.modal-close').addEventListener('click', closeCardModal);
    cardModal.querySelector('.modal-overlay').addEventListener('click', closeCardModal);

    // 查詢價格按鈕
    document.getElementById('viewDataBtn').addEventListener('click', handleViewData);

    // 確認按鈕
    document.getElementById('confirmBtn').addEventListener('click', closeConfirmModal);
    confirmModal.querySelector('.modal-overlay').addEventListener('click', closeConfirmModal);
}

function showSearchMode() {
    searchSection.classList.remove('hidden');
    setGrid.classList.add('hidden');
    setHeader.classList.add('hidden');
    cardGrid.classList.add('hidden');
    emptyState.classList.remove('hidden');
    emptyState.querySelector('p').textContent = '輸入關鍵字搜尋卡片';
    emptyState.querySelector('.hint').textContent = '支援中文、英文、日文名稱，或卡片編號 (如: 皮卡丘 #227)';
}

async function showCategories(language) {
    searchSection.classList.add('hidden');
    setHeader.classList.add('hidden');
    cardGrid.classList.add('hidden');
    emptyState.classList.add('hidden');

    showLoading();

    try {
        const response = await fetch(`${API_BASE}/api/cardlist/categories?language=${language}`);
        const data = await response.json();

        hideLoading();

        if (data.categories && data.categories.length > 0) {
            renderCategories(data.categories);
        } else {
            emptyState.classList.remove('hidden');
            emptyState.querySelector('p').textContent = '沒有系列資料';
        }
    } catch (error) {
        console.error('Load categories failed:', error);
        hideLoading();
        emptyState.classList.remove('hidden');
        emptyState.querySelector('p').textContent = '載入失敗';
    }
}

function renderCategories(categories) {
    setGrid.innerHTML = '';
    setGrid.classList.remove('hidden');

    categories.forEach(cat => {
        if (cat.count === 0) return; // 跳過空分類

        const catEl = document.createElement('div');
        catEl.className = 'category-item';

        const logoHtml = cat.logo ? `<img src="${cat.logo}" alt="${cat.name}" class="category-logo" onerror="this.style.display='none'">` : '';

        catEl.innerHTML = `
            <div class="category-header">
                ${logoHtml}
                <span class="category-name">${cat.name_zh} (${cat.name})</span>
                <span class="category-count">${cat.count} 個系列</span>
                <span class="category-arrow">▼</span>
            </div>
            <div class="category-sets hidden"></div>
        `;

        const header = catEl.querySelector('.category-header');
        const setsContainer = catEl.querySelector('.category-sets');
        const arrow = catEl.querySelector('.category-arrow');

        // 點擊展開/收合
        header.addEventListener('click', () => {
            const isOpen = !setsContainer.classList.contains('hidden');
            if (isOpen) {
                setsContainer.classList.add('hidden');
                arrow.textContent = '▼';
            } else {
                setsContainer.classList.remove('hidden');
                arrow.textContent = '▲';
                // 渲染系列（如果還沒渲染）
                if (setsContainer.children.length === 0) {
                    renderSetsInCategory(setsContainer, cat.sets);
                }
            }
        });

        setGrid.appendChild(catEl);
    });
}

function renderSetsInCategory(container, sets) {
    sets.forEach(set => {
        const setEl = document.createElement('div');
        setEl.className = 'set-item-small';

        const logoUrl = set.logo_url || '';

        // 顯示格式：日文名 (中文翻譯)，如果沒有日文名就顯示英文名
        let displayName = set.name;
        if (set.name_jp && set.name_zh) {
            displayName = `${set.name_jp} (${set.name_zh})`;
        } else if (set.name_jp) {
            displayName = set.name_jp;
        }

        setEl.innerHTML = `
            ${logoUrl ? `<img src="${logoUrl}" alt="${set.name}" onerror="this.style.display='none'">` : ''}
            <div class="set-info">
                <div class="set-name">${displayName}</div>
                <div class="set-count">${set.total_cards || 0} 張</div>
            </div>
        `;

        setEl.addEventListener('click', (e) => {
            e.stopPropagation();
            loadSetCards(set);
        });

        container.appendChild(setEl);
    });
}

async function loadSetCards(set) {
    currentSetId = set.set_id;

    setGrid.classList.add('hidden');
    setHeader.classList.remove('hidden');
    document.getElementById('setTitle').textContent = set.name;

    showLoading();

    try {
        const response = await fetch(`${API_BASE}/api/cardlist/sets/${encodeURIComponent(set.set_id)}`);
        const data = await response.json();

        hideLoading();

        if (data.cards && data.cards.length > 0) {
            renderCards(data.cards);
        } else {
            emptyState.classList.remove('hidden');
            emptyState.querySelector('p').textContent = '沒有卡片資料';
        }
    } catch (error) {
        console.error('Load cards failed:', error);
        hideLoading();
        emptyState.classList.remove('hidden');
        emptyState.querySelector('p').textContent = '載入失敗';
    }
}

async function handleSearch() {
    const query = searchInput.value.trim();
    if (!query) return;

    showLoading();

    try {
        let url = `${API_BASE}/api/cardlist/search?q=${encodeURIComponent(query)}&limit=300`;
        if (searchLang) url += `&language=${searchLang}`;
        const response = await fetch(url);
        const data = await response.json();

        hideLoading();

        if (data.cards && data.cards.length > 0) {
            setGrid.classList.add('hidden');
            renderCards(data.cards);
        } else {
            showEmptyState();
            emptyState.querySelector('p').textContent = `找不到「${query}」的卡片`;
        }
    } catch (error) {
        console.error('Search failed:', error);
        hideLoading();
        showEmptyState();
        emptyState.querySelector('p').textContent = '搜尋失敗，請稍後再試';
    }
}

function renderCards(cards) {
    cardGrid.innerHTML = '';
    emptyState.classList.add('hidden');
    cardGrid.classList.remove('hidden');

    cards.forEach((card) => {
        const cardEl = document.createElement('div');
        cardEl.className = 'card-item';

        let imageUrl = card.image_url || '';
        imageUrl = imageUrl.replace('.thumb.png', '.png');
        if (!imageUrl || imageUrl.includes('placeholder')) {
            imageUrl = 'https://via.placeholder.com/200x280?text=No+Image';
        }

        // 從 set_id 前綴判斷語言：jp- → 日, en- → 英
        const sid = (card.set_id || '');
        let badgeHtml = '';
        if (sid.startsWith('jp-')) {
            badgeHtml = '<span class="card-lang-badge lang-jp">日</span>';
        } else if (sid.startsWith('en-')) {
            badgeHtml = '<span class="card-lang-badge lang-en">英</span>';
        }

        cardEl.innerHTML = `${badgeHtml}<img src="${imageUrl}" alt="${card.name}" loading="lazy" onerror="this.src='https://via.placeholder.com/200x280?text=No+Image'">`;
        cardEl.addEventListener('click', () => openCardModal(card));
        cardGrid.appendChild(cardEl);
    });
}

function openCardModal(card) {
    let imageUrl = card.image_url || '';
    imageUrl = imageUrl.replace('.thumb.png', '.png');
    if (!imageUrl || imageUrl.includes('placeholder')) {
        imageUrl = 'https://via.placeholder.com/200x280?text=No+Image';
    }

    selectedCard = {
        name: card.name,
        set_id: card.set_id,
        set_name: card.set_name || card.set_id,
        image_url: imageUrl,
        card_number: card.card_number,
    };

    document.getElementById('modalImage').src = imageUrl;
    let title = card.name_zh || card.name || '';
    const sub = [];
    if (card.name_zh && card.name) sub.push(card.name);
    if (card.name_jp) sub.push(card.name_jp);
    document.getElementById('modalTitle').textContent = title;
    const subParts = sub.length ? sub.join(' / ') + '　' : '';
    document.getElementById('modalSubtitle').textContent = `${subParts}${card.set_name || card.set_id} #${card.card_number}`;
    document.getElementById('modalPrice').textContent = '點擊查詢 eBay 成交價';
    document.getElementById('modalPrice').style.color = '#666';

    cardModal.classList.remove('hidden');
}

function closeCardModal() {
    cardModal.classList.add('hidden');
    selectedCard = null;
}

async function handleViewData() {
    if (!selectedCard) return;

    // 先捕獲欄位（closeCardModal 會將 selectedCard 設為 null）
    const setId = selectedCard.set_id || selectedCard.set_name;
    const setDisplay = selectedCard.set_name || setId;
    const cardNumber = selectedCard.card_number;
    const cardName = selectedCard.name;
    const cardImage = selectedCard.image_url;

    closeCardModal();

    // 顯示載入狀態
    showLoading();
    loading.querySelector('p').textContent = '正在載入價格資料...';

    try {
        // 優先從資料庫取得已同步的價格
        const priceResponse = await fetch(`${API_BASE}/api/prices/${encodeURIComponent(setId)}/${encodeURIComponent(cardNumber)}`);

        if (priceResponse.ok) {
            const priceData = await priceResponse.json();

            // 如果有價格資料，直接顯示
            if (priceData.prices && priceData.prices.length > 0) {
                hideLoading();

                // 轉換格式以適應 card.html
                const ebayPrices = priceData.prices.filter(p => p.source === 'ebay').map(p => ({
                    listing_title: p.listing_title,
                    price_usd: p.price_usd,
                    price_twd: p.price_twd,
                    listing_url: p.listing_url,
                    sale_date: p.sale_date,
                    source: 'ebay'
                }));

                const snkrPrices = priceData.prices.filter(p => p.source === 'snkrdunk').map(p => ({
                    listing_title: p.listing_title,
                    price_jpy: p.price_jpy,
                    price_twd: p.price_twd,
                    listing_url: p.listing_url,
                    sale_date: p.sale_date,
                    source: 'snkrdunk'
                }));

                localStorage.setItem('selectedCard', JSON.stringify({
                    query: cardName,
                    officialImage: cardImage,
                    officialName: cardName,
                    officialSet: setDisplay,
                    results: ebayPrices,
                    snkrdunk: snkrPrices,
                    stats: priceData.stats
                }));

                window.location.href = `/static/liff/card.html?q=${encodeURIComponent(cardName)}&set=${encodeURIComponent(setId)}&num=${encodeURIComponent(cardNumber)}`;
                return;
            }
        }

        // 如果沒有快取資料，即時搜尋並存檔（下次秒載）
        loading.querySelector('p').textContent = '第一次查詢，約需 10-30 秒（之後會加快）...';

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 90000);

        // 使用 sync 端點（會寫入 DB 作為快取）
        const response = await fetch(
            `${API_BASE}/api/prices/sync/${encodeURIComponent(setId)}/${encodeURIComponent(cardNumber)}`,
            { method: 'POST', signal: controller.signal }
        );
        clearTimeout(timeoutId);

        const syncData = await response.json();

        // sync 完成後再讀一次 cache，結構跟 cache 分支相同
        const refetched = await fetch(`${API_BASE}/api/prices/${encodeURIComponent(setId)}/${encodeURIComponent(cardNumber)}`);
        const priceData = refetched.ok ? await refetched.json() : { prices: [], stats: {} };

        const ebayPrices = priceData.prices.filter(p => p.source === 'ebay');
        const snkrPrices = priceData.prices.filter(p => p.source === 'snkrdunk');

        hideLoading();

        localStorage.setItem('selectedCard', JSON.stringify({
            query: cardName,
            officialImage: cardImage,
            officialName: cardName,
            officialSet: setDisplay,
            results: ebayPrices,
            snkrdunk: snkrPrices,
            stats: priceData.stats
        }));

        window.location.href = `/static/liff/card.html?q=${encodeURIComponent(cardName)}&set=${encodeURIComponent(setId)}&num=${encodeURIComponent(cardNumber)}`;

    } catch (error) {
        hideLoading();
        if (error.name === 'AbortError') {
            alert('搜尋超時，請稍後再試');
        } else {
            alert('搜尋失敗：' + error.message);
        }
    }
}

function closeConfirmModal() {
    confirmModal.classList.add('hidden');
}

function showLoading() {
    loading.classList.remove('hidden');
    cardGrid.classList.add('hidden');
    setGrid.classList.add('hidden');
    emptyState.classList.add('hidden');
}

function hideLoading() {
    loading.classList.add('hidden');
}

function showEmptyState() {
    emptyState.classList.remove('hidden');
    cardGrid.classList.add('hidden');
    setGrid.classList.add('hidden');
    loading.classList.add('hidden');
}
