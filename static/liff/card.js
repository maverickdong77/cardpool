// Card Detail Page

const API_BASE = window.location.origin;
const LIFF_ID = '2009794241-FASgMi67';

let cardData = null;
let priceChart = null;
let allEbayPrices = [];
let allSnkrPrices = [];
let currentRangeDays = 30;
let currentChartType = 'line'; // 'line' | 'candle' | 'bar'
let lastSyncHistory = null;

// 股票配色（紅漲綠跌，台股風格）
const COLOR_UP = '#e53935';      // 漲（紅）
const COLOR_DOWN = '#16a34a';    // 跌（綠）
const COLOR_EBAY = '#0064D2';
const COLOR_SNKR = '#FF6B00';
const COLOR_VOLUME = 'rgba(148, 163, 184, 0.45)';

const loading = document.getElementById('loading');
const cardContent = document.getElementById('cardContent');

document.addEventListener('DOMContentLoaded', async () => {
    try {
        await liff.init({ liffId: LIFF_ID });
    } catch (err) {
        console.warn('LIFF init failed:', err.message);
    }

    document.getElementById('backBtn').addEventListener('click', () => {
        window.history.back();
    });

    document.querySelectorAll('#chartRangeTabs .range-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#chartRangeTabs .range-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            const v = btn.getAttribute('data-range');
            currentRangeDays = v === 'all' ? null : parseInt(v, 10);
            renderChart(allEbayPrices, allSnkrPrices);
            updatePeriodStats(allEbayPrices, allSnkrPrices);
            updateHeaderPrice(allEbayPrices, allSnkrPrices);
        });
    });

    document.querySelectorAll('#chartTypeTabs .ctype-tab').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('#chartTypeTabs .ctype-tab').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentChartType = btn.getAttribute('data-type');
            renderChart(allEbayPrices, allSnkrPrices);
        });
    });

    loadCardData();
});

function loadCardData() {
    const savedData = localStorage.getItem('selectedCard');
    const params = new URLSearchParams(window.location.search);
    const setId = params.get('set');
    const cardNumber = params.get('num') || params.get('number');

    if (savedData) {
        const data = JSON.parse(savedData);
        if (setId && cardNumber) {
            fetchPricesFromDB(setId, cardNumber, data);
        } else {
            displayCardData(data);
        }
        return;
    }

    const query = params.get('q');
    if (setId && cardNumber) {
        fetchPricesFromDB(setId, cardNumber, { query });
    } else if (query) {
        fetchCardData(query);
    } else {
        showError('找不到卡片資料');
    }
}

async function fetchPricesFromDB(setId, cardNumber, existingData = {}) {
    try {
        const response = await fetch(`${API_BASE}/api/prices/${encodeURIComponent(setId)}/${encodeURIComponent(cardNumber)}`, {
            headers: { 'ngrok-skip-browser-warning': '1' }
        });
        const data = await response.json();

        const ebayPrices = (data.prices || []).filter(p => p.source === 'ebay').map(p => ({
            price_twd: p.price_twd,
            price_usd: p.price_usd,
            listing_title: p.listing_title,
            listing_url: p.listing_url,
            sale_date: p.sale_date || p.created_at
        }));

        const snkrPrices = (data.prices || []).filter(p => p.source === 'snkrdunk').map(p => ({
            price_twd: p.price_twd,
            price_jpy: p.price_jpy,
            listing_title: p.listing_title,
            listing_url: p.listing_url,
            sale_date: p.sale_date || p.created_at
        }));

        const meta = data.card || {};
        const cardName = meta.name_zh
            || existingData.officialName
            || meta.name
            || existingData.query
            || (data.prices && data.prices[0] && data.prices[0].card_name)
            || `#${cardNumber}`;
        const setDisplay = existingData.officialSet || meta.set_name || setId;
        const officialImage = existingData.officialImage || meta.image_url;

        lastSyncHistory = data.sync_history || null;

        displayCardData({
            ...existingData,
            officialName: cardName,
            nameEn: meta.name,
            nameJp: meta.name_jp,
            officialSet: setDisplay,
            officialImage: officialImage,
            cardNumber: cardNumber,
            results: ebayPrices,
            snkrdunk: snkrPrices,
            stats: data.stats,
            sync_history: data.sync_history
        });
    } catch (error) {
        console.error('Fetch prices failed:', error);
        if (existingData.query) {
            fetchCardData(existingData.query);
        } else {
            showError('載入價格失敗');
        }
    }
}

async function fetchCardData(query) {
    try {
        const response = await fetch(`${API_BASE}/api/search/name/${encodeURIComponent(query)}`, {
            headers: { 'ngrok-skip-browser-warning': '1' }
        });
        const data = await response.json();

        if (data.ebay && data.ebay.length > 0) {
            displayCardData({
                query: query,
                results: data.ebay,
                snkrdunk: data.snkrdunk || []
            });
        } else {
            showError('找不到相關資料');
        }
    } catch (error) {
        console.error('Fetch failed:', error);
        showError('載入失敗');
    }
}

function displayCardData(data) {
    cardData = data;
    const ebayResults = data.results || [];
    const snkrdunkResults = data.snkrdunk || [];

    loading.classList.add('hidden');
    cardContent.classList.remove('hidden');

    const imageUrl = data.officialImage || ebayResults[0]?.image_url || 'https://via.placeholder.com/100x140?text=No+Image';
    document.getElementById('cardImage').src = imageUrl;

    const title = data.officialName || data.query || ebayResults[0]?.listing_title?.split('\n')[0] || '未知卡片';
    document.getElementById('cardTitle').textContent = title.substring(0, 60);
    const subtitleParts = [];

    const altNames = [];
    if (data.nameEn && data.nameEn !== title) altNames.push(data.nameEn);
    if (data.nameJp && data.nameJp !== title) altNames.push(data.nameJp);
    if (altNames.length) subtitleParts.push(altNames.join(' / '));

    const params = new URLSearchParams(window.location.search);
    const setId = params.get('set') || '';
    let cardLang = '';
    if (setId.startsWith('jp-')) cardLang = 'jp';
    else if (setId.startsWith('en-')) cardLang = 'en';

    let langTag = '';
    if (cardLang === 'jp') langTag = '【日文版】';
    else if (cardLang === 'en') langTag = '【英文版】';
    if (langTag) subtitleParts.push(langTag);

    if (data.officialSet) subtitleParts.push(data.officialSet);
    if (data.cardNumber) subtitleParts.push(`#${data.cardNumber}`);
    document.getElementById('cardSubtitle').textContent = subtitleParts.join(' · ');

    // 設定 eBay badge：JP 卡 → 日版；EN 卡 → 英版
    const ebayBadge = document.getElementById('ebayLangBadge');
    if (ebayBadge) {
        if (cardLang === 'jp') {
            ebayBadge.textContent = '日版';
            ebayBadge.className = 'lang-badge jp';
        } else if (cardLang === 'en') {
            ebayBadge.textContent = '英版';
            ebayBadge.className = 'lang-badge en';
        } else {
            ebayBadge.style.display = 'none';
        }
    }

    // 英卡隱藏 SNKRDUNK 區塊（SNKRDUNK 只賣日版）
    const snkrSection = document.getElementById('snkrdunkSection');
    if (snkrSection) {
        if (cardLang === 'en') {
            snkrSection.style.display = 'none';
        } else {
            snkrSection.style.display = '';
        }
    }

    displaySourceData('snkr', snkrdunkResults, 'snkrdunkSection');
    displaySourceData('ebay', ebayResults, 'ebaySection');

    allEbayPrices = ebayResults;
    allSnkrPrices = snkrdunkResults;
    updatePeriodStats(allEbayPrices, allSnkrPrices);
    updateLanguageCompare(data, cardLang);
    renderChart(allEbayPrices, allSnkrPrices);
    updateHeaderPrice(allEbayPrices, allSnkrPrices);
    updateSyncPrompt(data, cardLang);
}

// 圖表標題列：最新價格 + 漲跌
function updateHeaderPrice(ebayResults, snkrResults) {
    const all = [...ebayResults, ...snkrResults]
        .filter(r => r.price_twd > 0 && r.sale_date)
        .sort((a, b) => new Date(a.sale_date) - new Date(b.sale_date));

    const priceEl = document.getElementById('chartCurrentPrice');
    const changeEl = document.getElementById('chartChange');
    if (!priceEl || !changeEl) return;

    if (all.length === 0) {
        priceEl.textContent = '-';
        changeEl.textContent = '';
        changeEl.className = 'chart-change';
        return;
    }

    const latest = all[all.length - 1].price_twd;

    // 找對應區間起點
    let baseline = all[0].price_twd;
    if (currentRangeDays) {
        const cutoff = Date.now() - currentRangeDays * 24 * 3600 * 1000;
        const inRange = all.filter(r => new Date(r.sale_date).getTime() >= cutoff);
        if (inRange.length > 0) baseline = inRange[0].price_twd;
    }

    priceEl.textContent = `NT$ ${Math.round(latest).toLocaleString()}`;

    const diff = latest - baseline;
    const pct = baseline > 0 ? (diff / baseline * 100) : 0;
    if (Math.abs(diff) < 0.5) {
        changeEl.textContent = '— 0.0%';
        changeEl.className = 'chart-change flat';
    } else {
        const arrow = diff > 0 ? '▲' : '▼';
        const sign = diff > 0 ? '+' : '';
        changeEl.textContent = `${arrow} ${sign}${Math.round(diff).toLocaleString()} (${sign}${pct.toFixed(1)}%)`;
        changeEl.className = `chart-change ${diff > 0 ? 'up' : 'down'}`;
    }
}

// 沒有任何 PSA10 紀錄時顯示「立即查詢」/「冷門卡」面板
function updateSyncPrompt(data, cardLang) {
    const sect = document.getElementById('syncPrompt');
    if (!sect) return;
    const ebayResults = data.results || [];
    const snkrResults = data.snkrdunk || [];
    const totalRecords = ebayResults.length + snkrResults.length;

    if (totalRecords > 0) {
        sect.classList.add('hidden');
        return;
    }

    const params = new URLSearchParams(window.location.search);
    const setId = params.get('set');
    const cardNumber = params.get('num') || params.get('number');
    if (!setId || !cardNumber) {
        sect.classList.add('hidden');
        return;
    }

    const hist = data.sync_history || lastSyncHistory || { attempt_count: 0, zero_hit_count: 0, total_hits: 0 };
    const attempts = hist.attempt_count || 0;
    const zeroHits = hist.zero_hit_count || 0;
    const lastAttempt = hist.last_attempt;

    const iconEl = document.getElementById('syncPromptIcon');
    const titleEl = document.getElementById('syncPromptTitle');
    const descEl = document.getElementById('syncPromptDesc');
    const btn = document.getElementById('syncBtn');
    const meta = document.getElementById('syncPromptMeta');

    sect.classList.remove('hidden');
    sect.classList.remove('cold');

    if (zeroHits >= 2) {
        sect.classList.add('cold');
        iconEl.textContent = '❄️';
        titleEl.textContent = '冷門卡（市場無流通）';
        descEl.textContent = `本卡已嘗試掃描 ${attempts} 次，皆無 PSA 10 成交紀錄。可能是市場稀少、未鑑定卡或編碼罕見。`;
        btn.textContent = '⚡ 仍要重新查詢';
    } else if (attempts > 0) {
        iconEl.textContent = '🔍';
        titleEl.textContent = '上次掃描無成交紀錄';
        descEl.textContent = '可能 SNKRDUNK / eBay 暫時沒有 PSA 10 紀錄。可再試一次抓最新資料。';
        btn.textContent = '⚡ 重新查詢';
    } else {
        iconEl.textContent = '🆕';
        titleEl.textContent = '尚未掃描過此卡';
        descEl.textContent = '點擊下方按鈕立即從 SNKRDUNK 與 eBay 抓取最新 PSA 10 成交紀錄（約需 10-30 秒）。';
        btn.textContent = '⚡ 立即查詢';
    }

    if (lastAttempt) {
        try {
            const d = new Date(lastAttempt.replace(' ', 'T') + 'Z');
            meta.textContent = `上次掃描：${d.toLocaleString('zh-TW')}`;
            meta.classList.remove('hidden');
        } catch (e) {
            meta.classList.add('hidden');
        }
    } else {
        meta.classList.add('hidden');
    }

    // 重新綁定（避免重複 listener）
    const newBtn = btn.cloneNode(true);
    btn.parentNode.replaceChild(newBtn, btn);
    newBtn.addEventListener('click', () => triggerSync(setId, cardNumber, newBtn));
}

async function triggerSync(setId, cardNumber, btn) {
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = '⏳ 抓取中（約 10-30 秒）...';
    try {
        const r = await fetch(`${API_BASE}/api/prices/sync/${encodeURIComponent(setId)}/${encodeURIComponent(cardNumber)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'ngrok-skip-browser-warning': '1' }
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        const found = (data.ebay_count || 0) + (data.snkrdunk_count || 0);
        btn.textContent = found > 0
            ? `✓ 抓到 ${found} 筆，重新載入中...`
            : '⚪ 無新增紀錄，重新載入中...';
        // 等 0.6s 讓 commit 完成再 reload
        setTimeout(() => location.reload(), 600);
    } catch (e) {
        console.error('sync failed:', e);
        btn.disabled = false;
        btn.textContent = original;
        alert('查詢失敗：' + (e.message || '未知錯誤'));
    }
}

// 跨版本價格比較：本卡 + 對應語言 sibling 卡的價格
async function updateLanguageCompare(data, cardLang) {
    const sect = document.getElementById('compareSection');
    if (!sect) return;
    if (cardLang !== 'jp' && cardLang !== 'en') {
        sect.classList.add('hidden');
        return;
    }

    const stats = data.stats || {};
    // 本卡的均價（依 cardLang 取對應端的 stat）
    let selfAvg = null;
    if (cardLang === 'jp') {
        // 日卡：優先用 SNKR，fallback eBay JP
        selfAvg = stats.snkrdunk_jp?.avg ?? stats.snkrdunk?.avg ?? stats.ebay_jp?.avg ?? stats.ebay?.avg ?? null;
    } else {
        selfAvg = stats.ebay_en?.avg ?? stats.ebay?.avg ?? null;
    }

    if (!selfAvg) {
        sect.classList.add('hidden');
        return;
    }

    // 抓對應語言 sibling 的價格
    const params = new URLSearchParams(window.location.search);
    const setId = params.get('set');
    const cardNumber = params.get('num') || params.get('number');
    if (!setId || !cardNumber) { sect.classList.add('hidden'); return; }

    let siblingAvg = null;
    let siblingMeta = null;
    try {
        const r = await fetch(`${API_BASE}/api/prices/sibling/${encodeURIComponent(setId)}/${encodeURIComponent(cardNumber)}`);
        if (r.ok) {
            const sib = await r.json();
            if (sib.stats?.avg) {
                siblingAvg = sib.stats.avg;
                siblingMeta = sib.sibling;
            }
        }
    } catch (e) {
        console.warn('sibling fetch failed:', e);
    }

    if (!siblingAvg) {
        sect.classList.add('hidden');
        return;
    }

    const jpAvg = cardLang === 'jp' ? selfAvg : siblingAvg;
    const enAvg = cardLang === 'en' ? selfAvg : siblingAvg;
    sect.classList.remove('hidden');
    const jpBlock = document.querySelector('.compare-block.jp');
    const enBlock = document.querySelector('.compare-block.en');
    document.getElementById('cmpJpAvg').textContent = `NT$ ${Math.round(jpAvg).toLocaleString()}`;
    document.getElementById('cmpEnAvg').textContent = `NT$ ${Math.round(enAvg).toLocaleString()}`;

    // 對方語言版本（不是當前看的這版）→ 可點擊跳到 sibling 的詳情頁
    const targetBlock = cardLang === 'jp' ? enBlock : jpBlock;
    const selfBlock = cardLang === 'jp' ? jpBlock : enBlock;

    // 清掉舊狀態（避免重複綁 event）
    [jpBlock, enBlock].forEach(b => {
        if (!b) return;
        b.classList.remove('clickable');
        const old = b.querySelector('.link-hint');
        if (old) old.remove();
        // 透過複製節點移除舊 listener
        const clone = b.cloneNode(true);
        b.parentNode.replaceChild(clone, b);
    });

    // 重新拿（節點被替換了）
    const jpBlock2 = document.querySelector('.compare-block.jp');
    const enBlock2 = document.querySelector('.compare-block.en');
    const target2 = cardLang === 'jp' ? enBlock2 : jpBlock2;

    if (target2 && siblingMeta?.set_id && siblingMeta?.card_number) {
        target2.classList.add('clickable');
        const hint = document.createElement('span');
        hint.className = 'link-hint';
        hint.textContent = `→ ${siblingMeta.set_name || siblingMeta.set_id} #${siblingMeta.card_number}（點擊查看）`;
        target2.appendChild(hint);
        target2.addEventListener('click', () => {
            const url = `/static/liff/card.html?set=${encodeURIComponent(siblingMeta.set_id)}&num=${encodeURIComponent(siblingMeta.card_number)}`;
            // 清掉前一張的快取，避免新頁面誤用
            try { localStorage.removeItem('selectedCard'); } catch (e) {}
            window.location.href = url;
        });
    }

    const diff = Math.abs(jpAvg - enAvg);
    const ratio = (diff / Math.min(jpAvg, enAvg) * 100).toFixed(0);
    const higher = jpAvg > enAvg ? '日版' : '英版';
    document.getElementById('cmpDiffNote').textContent =
        `${higher}比另一版本高 NT$${Math.round(diff).toLocaleString()}（約 ${ratio}%）`;
}

function displaySourceData(prefix, results, sectionId) {
    const section = document.getElementById(sectionId);

    if (results.length === 0) {
        section.querySelector('.record-list').innerHTML = '<div class="no-data">暫無成交記錄</div>';
        return;
    }

    const prices = results.map(r => r.price_twd).filter(p => p > 0);

    if (prices.length > 0) {
        const sorted = [...results].sort((a, b) => new Date(b.sale_date) - new Date(a.sale_date));
        const latest = sorted[0]?.price_twd || prices[0];
        const high = Math.max(...prices);
        const low = Math.min(...prices);

        document.getElementById(`${prefix}Latest`).textContent = `NT$ ${Math.round(latest).toLocaleString()}`;
        document.getElementById(`${prefix}High`).textContent = `NT$ ${Math.round(high).toLocaleString()}`;
        document.getElementById(`${prefix}Low`).textContent = `NT$ ${Math.round(low).toLocaleString()}`;
    }

    const recordsList = section.querySelector('.record-list');
    recordsList.innerHTML = '';

    const sortedRecords = [...results].sort((a, b) => new Date(b.sale_date) - new Date(a.sale_date));
    sortedRecords.slice(0, 5).forEach(record => {
        const fullTitle = (record.listing_title || '').split('\n')[0];
        const psaMatch = fullTitle.match(/PSA\s*(\d+(?:\.\d+)?)/i);
        const psaTag = psaMatch ? `<span class="psa-tag">PSA ${psaMatch[1]}</span>` : '';
        const title = fullTitle.substring(0, 36);
        const price = Math.round(record.price_twd || 0);
        const date = record.sale_date ? new Date(record.sale_date).toLocaleDateString('zh-TW') : '';

        // 判斷是否為 ebay /itm/ listing → 加 flag 按鈕
        const itemIdMatch = (record.listing_url || '').match(/\/itm\/(\d+)/);
        const isEbay = !!itemIdMatch;
        const flagBtnHtml = isEbay
            ? `<button class="flag-btn" data-item="${itemIdMatch[1]}" title="回報此筆非 PSA 10">⚠回報</button>`
            : '';

        const recordEl = document.createElement('div');
        recordEl.className = 'record-item';
        recordEl.innerHTML = `
            ${psaTag}
            <span class="record-title">${title}</span>
            <span class="record-price">$${price.toLocaleString()}</span>
            <span class="record-date">${date}</span>
            ${flagBtnHtml}
        `;

        if (record.listing_url) {
            // 整列可點 → 開新分頁；但 flag-btn 自己 stopPropagation
            const titleEl = recordEl.querySelector('.record-title');
            const priceEl = recordEl.querySelector('.record-price');
            [titleEl, priceEl].forEach(el => {
                if (!el) return;
                el.style.cursor = 'pointer';
                el.addEventListener('click', () => {
                    window.open(record.listing_url, '_blank');
                });
            });
        }

        const flagBtn = recordEl.querySelector('.flag-btn');
        if (flagBtn) {
            flagBtn.addEventListener('click', async (ev) => {
                ev.stopPropagation();
                const itemId = flagBtn.dataset.item;
                if (!confirm(`確定回報這筆是非 PSA 10？\nitem_id: ${itemId}\n回報後會立即從本卡刪除並加入永久 blocklist。`)) return;
                flagBtn.disabled = true;
                flagBtn.textContent = '處理中...';
                try {
                    const r = await fetch(`${API_BASE}/api/admin/blocklist/${itemId}`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ reason: 'user flagged from UI' }),
                    });
                    const data = await r.json();
                    flagBtn.textContent = `✓ 已刪 ${data.deleted_rows}`;
                    setTimeout(() => location.reload(), 800);
                } catch (e) {
                    flagBtn.textContent = '失敗';
                    flagBtn.disabled = false;
                    alert('回報失敗：' + e.message);
                }
            });
        }

        recordsList.appendChild(recordEl);
    });
}

function filterByRange(records, days) {
    if (!days) return records;
    const cutoff = Date.now() - days * 24 * 60 * 60 * 1000;
    return records.filter(r => {
        const t = r.sale_date ? new Date(r.sale_date).getTime() : 0;
        return t && t >= cutoff;
    });
}

function groupByDay(records) {
    const map = new Map();
    records.forEach(r => {
        if (!r.sale_date || !r.price_twd) return;
        const d = new Date(r.sale_date);
        if (isNaN(d)) return;
        const key = d.toISOString().slice(0, 10);
        if (!map.has(key)) map.set(key, []);
        map.get(key).push(r.price_twd);
    });
    return [...map.entries()]
        .map(([date, arr]) => ({
            date,
            avg: arr.reduce((s, x) => s + x, 0) / arr.length,
            count: arr.length
        }))
        .sort((a, b) => a.date.localeCompare(b.date));
}

function updatePeriodStats(ebayResults, snkrResults) {
    const all = [...ebayResults, ...snkrResults];
    const filtered = filterByRange(all, currentRangeDays).filter(r => r.price_twd > 0);
    const target = document.getElementById('periodStats');
    if (!target) return;
    if (filtered.length === 0) {
        target.innerHTML = '<div class="no-data" style="grid-column:1/-1">所選區間沒有成交資料</div>';
        return;
    }
    const prices = filtered.map(r => r.price_twd);
    const avg = prices.reduce((s, x) => s + x, 0) / prices.length;
    const high = Math.max(...prices);
    const low = Math.min(...prices);
    target.innerHTML = `
        <div class="stat"><span class="label">筆數</span><span class="value">${filtered.length}</span></div>
        <div class="stat"><span class="label">均價</span><span class="value avg">NT$ ${Math.round(avg).toLocaleString()}</span></div>
        <div class="stat"><span class="label">最高</span><span class="value high">NT$ ${Math.round(high).toLocaleString()}</span></div>
        <div class="stat"><span class="label">最低</span><span class="value low">NT$ ${Math.round(low).toLocaleString()}</span></div>
    `;
}

// ===== 圖表渲染（折線/K線/長條 三種模式）=====
function renderChart(ebayResults, snkrResults) {
    const canvas = document.getElementById('priceChart');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (priceChart) { priceChart.destroy(); priceChart = null; }

    const ebayFiltered = filterByRange(ebayResults, currentRangeDays);
    const snkrFiltered = filterByRange(snkrResults, currentRangeDays);

    if (ebayFiltered.length + snkrFiltered.length === 0) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.font = '14px -apple-system, sans-serif';
        ctx.fillStyle = '#999';
        ctx.textAlign = 'center';
        ctx.fillText('所選區間沒有資料', canvas.width / 2, canvas.height / 2);
        renderLegend([]);
        return;
    }

    if (currentChartType === 'line') {
        renderLineChart(ctx, ebayFiltered, snkrFiltered);
    } else if (currentChartType === 'candle') {
        renderCandleChart(ctx, ebayFiltered, snkrFiltered);
    } else if (currentChartType === 'bar') {
        renderBarChart(ctx, ebayFiltered, snkrFiltered);
    }
}

function renderLegend(items) {
    const el = document.getElementById('chartLegend');
    if (!el) return;
    el.innerHTML = items.map(it =>
        `<span class="lg-item"><span class="lg-dot" style="background:${it.color}"></span>${it.label}</span>`
    ).join('');
}

function commonChartOptions(yLabel = '價格 (NT$)') {
    return {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
            legend: { display: false },
            tooltip: {
                backgroundColor: 'rgba(20,20,20,0.92)',
                titleColor: '#fff',
                bodyColor: '#fff',
                borderColor: 'rgba(255,255,255,0.1)',
                borderWidth: 1,
                padding: 10,
                cornerRadius: 6,
                titleFont: { size: 11, weight: '600' },
                bodyFont: { size: 12 }
            }
        },
        scales: {
            x: {
                grid: { color: 'rgba(0,0,0,0.04)', drawTicks: false },
                border: { display: false },
                ticks: {
                    maxRotation: 0, autoSkip: true, maxTicksLimit: 8,
                    color: '#94a3b8', font: { size: 10 }
                }
            },
            y: {
                type: 'linear', position: 'left',
                grid: { color: 'rgba(0,0,0,0.04)', drawTicks: false },
                border: { display: false },
                ticks: {
                    color: '#94a3b8', font: { size: 10 },
                    callback: v => v >= 1000 ? '$' + (v / 1000).toFixed(1) + 'k' : '$' + v
                },
                title: { display: true, text: yLabel, font: { size: 10 }, color: '#94a3b8' }
            },
            yCount: {
                type: 'linear', position: 'right',
                grid: { display: false },
                border: { display: false },
                beginAtZero: true,
                ticks: {
                    color: '#94a3b8', font: { size: 10 },
                    precision: 0,
                    callback: v => Number.isInteger(v) ? v : ''
                },
                title: { display: true, text: '量', font: { size: 10 }, color: '#94a3b8' }
            }
        }
    };
}

// --- 趨勢線：移動平均（簡單算術 SMA）---
function movingAverage(series, window) {
    return series.map((_, i) => {
        // 從 [max(0, i-window+1), i] 取非 null 值
        const start = Math.max(0, i - window + 1);
        const slice = series.slice(start, i + 1).filter(v => v != null);
        if (slice.length < Math.min(window / 2, 3)) return null;
        return Math.round(slice.reduce((s, x) => s + x, 0) / slice.length);
    });
}

// --- 折線圖（保留兩個來源獨立呈現）---
function renderLineChart(ctx, ebayResults, snkrResults) {
    const ebayByDay = groupByDay(ebayResults);
    const snkrByDay = groupByDay(snkrResults);
    const dateSet = new Set([...ebayByDay.map(d => d.date), ...snkrByDay.map(d => d.date)]);
    const labels = [...dateSet].sort();
    const ebayMap = new Map(ebayByDay.map(d => [d.date, d]));
    const snkrMap = new Map(snkrByDay.map(d => [d.date, d]));

    const ebaySeries = labels.map(d => ebayMap.has(d) ? Math.round(ebayMap.get(d).avg) : null);
    const snkrSeries = labels.map(d => snkrMap.has(d) ? Math.round(snkrMap.get(d).avg) : null);
    const countSeries = labels.map(d =>
        (ebayMap.get(d)?.count || 0) + (snkrMap.get(d)?.count || 0)
    );

    // 合併兩來源 → 單一日均（作 MA 基底；插值處理 null）
    const combinedSeries = labels.map(d => {
        const e = ebayMap.get(d);
        const s = snkrMap.get(d);
        const vals = [];
        if (e) vals.push(e.avg);
        if (s) vals.push(s.avg);
        return vals.length ? Math.round(vals.reduce((x,y)=>x+y,0) / vals.length) : null;
    });
    // 7 / 30 日 MA
    const ma7  = movingAverage(combinedSeries, 7);
    const ma30 = movingAverage(combinedSeries, 30);

    const displayLabels = labels.map(d => d.slice(5));

    // 漸層填色（最頂端為主色，往下淡出）
    const gradient = ctx.createLinearGradient(0, 0, 0, 280);
    gradient.addColorStop(0, 'rgba(229, 57, 53, 0.18)');
    gradient.addColorStop(1, 'rgba(229, 57, 53, 0.00)');

    const datasets = [
        {
            type: 'bar',
            label: '量',
            data: countSeries,
            backgroundColor: COLOR_VOLUME,
            yAxisID: 'yCount',
            order: 3,
            barPercentage: 0.6,
            categoryPercentage: 0.9,
        }
    ];

    if (snkrSeries.some(v => v != null)) {
        datasets.push({
            type: 'line',
            label: 'SNKRDUNK',
            data: snkrSeries,
            borderColor: COLOR_SNKR,
            backgroundColor: 'rgba(255,107,0,0.12)',
            borderWidth: 2.2,
            fill: { target: 'origin', above: 'rgba(255,107,0,0.05)' },
            tension: 0.3,
            pointRadius: 0,
            pointHoverRadius: 5,
            spanGaps: true,
            yAxisID: 'y',
            order: 1,
        });
    }
    if (ebaySeries.some(v => v != null)) {
        datasets.push({
            type: 'line',
            label: 'eBay',
            data: ebaySeries,
            borderColor: COLOR_EBAY,
            backgroundColor: 'rgba(0,100,210,0.10)',
            borderWidth: 2.2,
            fill: false,
            tension: 0.3,
            pointRadius: 0,
            pointHoverRadius: 5,
            spanGaps: true,
            yAxisID: 'y',
            order: 2,
        });
    }

    // MA7 / MA30 趨勢線（虛線）— 至少要有 5 個點才畫
    if (combinedSeries.filter(v => v != null).length >= 5) {
        datasets.push({
            type: 'line',
            label: 'MA7',
            data: ma7,
            borderColor: '#a855f7',
            borderWidth: 1.6,
            borderDash: [4, 4],
            fill: false,
            tension: 0.3,
            pointRadius: 0,
            spanGaps: true,
            yAxisID: 'y',
            order: 0,
        });
    }
    if (combinedSeries.filter(v => v != null).length >= 12) {
        datasets.push({
            type: 'line',
            label: 'MA30',
            data: ma30,
            borderColor: '#0891b2',
            borderWidth: 1.6,
            borderDash: [2, 4],
            fill: false,
            tension: 0.3,
            pointRadius: 0,
            spanGaps: true,
            yAxisID: 'y',
            order: 0,
        });
    }

    const opts = commonChartOptions();
    opts.plugins.tooltip.callbacks = {
        title: (items) => labels[items[0]?.dataIndex] || '',
        label: (ctx) => {
            const v = ctx.raw;
            if (v == null) return `${ctx.dataset.label}: -`;
            if (ctx.dataset.label === '量') return `量: ${v} 筆`;
            return `${ctx.dataset.label}: NT$ ${Math.round(v).toLocaleString()}`;
        }
    };

    priceChart = new Chart(ctx, {
        type: 'bar',
        data: { labels: displayLabels, datasets },
        options: opts
    });

    const legendItems = [];
    if (snkrSeries.some(v => v != null)) legendItems.push({ color: COLOR_SNKR, label: 'SNKRDUNK 日均' });
    if (ebaySeries.some(v => v != null)) legendItems.push({ color: COLOR_EBAY, label: 'eBay 日均' });
    if (combinedSeries.filter(v => v != null).length >= 5)
        legendItems.push({ color: '#a855f7', label: 'MA7（7 日均線）' });
    if (combinedSeries.filter(v => v != null).length >= 12)
        legendItems.push({ color: '#0891b2', label: 'MA30（30 日均線）' });
    legendItems.push({ color: COLOR_VOLUME, label: '成交量' });
    renderLegend(legendItems);
}

// --- K 線圖（OHLC）---
function renderCandleChart(ctx, ebayResults, snkrResults) {
    // 全部來源混合，避免單來源候選太少
    const all = [...ebayResults, ...snkrResults]
        .filter(r => r.price_twd > 0 && r.sale_date)
        .map(r => ({ t: new Date(r.sale_date).getTime(), p: r.price_twd }))
        .sort((a, b) => a.t - b.t);

    if (all.length === 0) { renderLegend([]); return; }

    // 動態決定聚合單位：< 14 天用「日」、< 90 天用「日」、否則「週」
    const days = currentRangeDays || ((all[all.length - 1].t - all[0].t) / 86400000 + 1);
    const useWeek = days > 90;

    const buckets = new Map(); // key -> {t, prices: []}
    for (const row of all) {
        const d = new Date(row.t);
        let key;
        if (useWeek) {
            // ISO 週一作為週起點
            const tmp = new Date(d);
            const dow = (tmp.getDay() + 6) % 7;  // 週一=0
            tmp.setDate(tmp.getDate() - dow);
            tmp.setHours(0, 0, 0, 0);
            key = tmp.toISOString().slice(0, 10);
        } else {
            key = d.toISOString().slice(0, 10);
        }
        if (!buckets.has(key)) buckets.set(key, { t: key, prices: [] });
        buckets.get(key).prices.push(row.p);
    }

    const sortedKeys = [...buckets.keys()].sort();
    const ohlcData = sortedKeys.map(k => {
        const arr = buckets.get(k).prices; // 已依 t 排序進來
        const o = arr[0];
        const c = arr[arr.length - 1];
        const h = Math.max(...arr);
        const l = Math.min(...arr);
        return { x: new Date(k).getTime(), o, h, l, c, v: arr.length };
    });

    const volumeData = ohlcData.map(d => ({ x: d.x, y: d.v }));

    const opts = commonChartOptions();
    opts.scales.x = {
        type: 'time',
        time: { unit: useWeek ? 'week' : 'day', tooltipFormat: 'yyyy-MM-dd' },
        grid: { color: 'rgba(0,0,0,0.04)', drawTicks: false },
        border: { display: false },
        ticks: {
            color: '#94a3b8', font: { size: 10 },
            maxRotation: 0, autoSkip: true, maxTicksLimit: 7
        }
    };
    opts.plugins.tooltip.callbacks = {
        title: (items) => {
            const idx = items[0]?.dataIndex;
            const k = sortedKeys[idx];
            return useWeek ? `${k} 起 (週)` : k;
        },
        label: (ctx) => {
            if (ctx.dataset.label === '量') return `量: ${ctx.raw.y} 筆`;
            const r = ctx.raw;
            const fmt = v => `NT$ ${Math.round(v).toLocaleString()}`;
            return [
                `開: ${fmt(r.o)}`,
                `高: ${fmt(r.h)}`,
                `低: ${fmt(r.l)}`,
                `收: ${fmt(r.c)}`
            ];
        }
    };

    priceChart = new Chart(ctx, {
        type: 'candlestick',
        data: {
            datasets: [
                {
                    label: '量',
                    type: 'bar',
                    data: volumeData,
                    backgroundColor: COLOR_VOLUME,
                    yAxisID: 'yCount',
                    order: 3,
                    barPercentage: 0.6,
                    categoryPercentage: 0.9,
                },
                {
                    label: 'OHLC',
                    type: 'candlestick',
                    data: ohlcData,
                    yAxisID: 'y',
                    order: 1,
                    // chartjs-chart-financial v0.2 是用 pixel 比較：
                    // close pixel < open pixel = close 價格 > open 價格 = 漲 → backgroundColors.up
                    // close pixel > open pixel = close 價格 < open 價格 = 跌 → backgroundColors.down
                    backgroundColors: { up: COLOR_UP, down: COLOR_DOWN, unchanged: '#94a3b8' },
                    borderColors:     { up: COLOR_UP, down: COLOR_DOWN, unchanged: '#94a3b8' },
                    borderWidth: 1.4,
                }
            ]
        },
        options: opts
    });

    renderLegend([
        { color: COLOR_UP, label: '漲（收 ≥ 開）' },
        { color: COLOR_DOWN, label: '跌（收 < 開）' },
        { color: COLOR_VOLUME, label: '成交量' },
        { color: 'transparent', label: useWeek ? '單位：週' : '單位：日' }
    ]);
}

// --- 長條圖（日均合併）---
function renderBarChart(ctx, ebayResults, snkrResults) {
    const all = [...ebayResults, ...snkrResults];
    const byDay = groupByDay(all);
    if (byDay.length === 0) { renderLegend([]); return; }

    const labels = byDay.map(d => d.date);
    const displayLabels = labels.map(d => d.slice(5));
    const avgSeries = byDay.map(d => Math.round(d.avg));
    const countSeries = byDay.map(d => d.count);

    // 依漲跌上色（與前一根比）
    const colors = avgSeries.map((v, i) => {
        if (i === 0) return COLOR_UP;
        return v >= avgSeries[i - 1] ? COLOR_UP : COLOR_DOWN;
    });

    const opts = commonChartOptions();
    opts.plugins.tooltip.callbacks = {
        title: (items) => labels[items[0]?.dataIndex] || '',
        label: (ctx) => {
            if (ctx.dataset.label === '量') return `量: ${ctx.raw} 筆`;
            return `日均: NT$ ${Math.round(ctx.raw).toLocaleString()}`;
        }
    };

    priceChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: displayLabels,
            datasets: [
                {
                    label: '量',
                    data: countSeries,
                    backgroundColor: COLOR_VOLUME,
                    yAxisID: 'yCount',
                    order: 2,
                    barPercentage: 0.4,
                    categoryPercentage: 0.9,
                },
                {
                    label: '日均',
                    data: avgSeries,
                    backgroundColor: colors,
                    borderRadius: 2,
                    yAxisID: 'y',
                    order: 1,
                    barPercentage: 0.7,
                    categoryPercentage: 0.85,
                }
            ]
        },
        options: opts
    });

    renderLegend([
        { color: COLOR_UP, label: '高於前一日' },
        { color: COLOR_DOWN, label: '低於前一日' },
        { color: COLOR_VOLUME, label: '成交量' }
    ]);
}

function showError(message) {
    loading.innerHTML = `<p class="no-data">${message}</p>`;
}
