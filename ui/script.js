const bubbleRegionBtn = document.querySelector('#bubbleRegionBtn');
const safeModeToggle = document.querySelector('#safeModeToggle');
const fiveStarToggle = document.querySelector('#fiveStarToggle');
const wildcardToggle = document.querySelector('#wildcardToggle');

let hasBubbleRegion = false;
let isSelectingRegion = false; // 框選視窗開啟中: 不能再框選，也不能開始任務

let currentCookingLogIndex = 0;
let currentSnowLogIndex = 0;
let currentFishLogIndex = 0;
let currentFishAltLogIndex = 0;

// 定時輪詢後端。關閉視窗前後端會先呼叫 markClosing()，之後就不再發出新的呼叫，
// 避免視窗銷毀的瞬間還有呼叫在途中，結果要回傳給已釋放的 WebView2 而噴錯
let isClosing = false;
window.markClosing = () => {
  isClosing = true;
};
const poll = (fn, ms) =>
  setInterval(() => {
    if (!isClosing) fn();
  }, ms);

// 取得 pywebview API
const getApi = () => {
  return window.pywebview.api;
};

// 設定座標顯示與樣式
const setCoordStatus = ({ selector, text, isSuccess }) => {
  const el = document.querySelector(selector);
  el.innerText = text;
  el.classList.toggle('text-danger', !isSuccess);
};

// 提示視窗: 顯示在腳本視窗正中央(原生 alert 的位置由系統決定，改用視窗內的對話框)。
// title 是對話框的標題(例如「無法開始」)，message 是內文。
// 回傳 Promise，按「確認」得到 true，按「取消」或 Esc 得到 false
// 內文裡用 **文字** 標示粗體、[+文字] 標示綠色、[-文字] 標示紅色(只用 textContent 組出節點，不解析 HTML)
const setRichText = (el, text) => {
  text.split(/(\*\*.+?\*\*|\[[+-].+?\])/).forEach((part) => {
    if (!part) return;
    let node = null;
    if (part.startsWith('**') && part.endsWith('**') && part.length > 4) {
      node = document.createElement('strong');
      node.textContent = part.slice(2, -2);
    } else if (/^\[[+-].+\]$/.test(part)) {
      node = document.createElement('strong');
      node.className = part[1] === '+' ? 'text-on' : 'text-off';
      node.textContent = part.slice(2, -1);
    }
    el.appendChild(node || document.createTextNode(part));
  });
};

// choices: [{ value, label }]。有給時顯示單選清單，確認回傳選到的 value，取消回傳 null
const showDialog = (title, message, { confirm = false, image = '', danger = false, okText = '確認', choices = null } = {}) =>
  new Promise((resolve) => {
    const dialog = document.querySelector('#dialog');
    const okBtn = document.querySelector('#dialogOk');
    const cancelBtn = document.querySelector('#dialogCancel');
    document.querySelector('#dialogTitle').textContent = title;
    // 內文每一行一段；以「．」開頭的行變成清單項目(用真正的 li，圓點才能放大)
    const msgEl = document.querySelector('#dialogMsg');
    msgEl.replaceChildren();
    let list = null;
    message.split('\n').forEach((line) => {
      if (!line.trim()) {
        list = null;
        // 空行保留成一整行高的空白(開頭、結尾、連續的空行不產生)
        if (msgEl.lastElementChild && !msgEl.lastElementChild.classList.contains('dialog-gap')) {
          const gap = document.createElement('div');
          gap.className = 'dialog-gap';
          msgEl.appendChild(gap);
        }
        return;
      }
      if (line.startsWith('．')) {
        if (!list) {
          list = document.createElement('ul');
          list.className = 'dialog-list';
          msgEl.appendChild(list);
        }
        const item = document.createElement('li');
        setRichText(item, line.slice(1));
        list.appendChild(item);
      } else {
        list = null;
        const para = document.createElement('p');
        setRichText(para, line);
        msgEl.appendChild(para);
      }
    });
    const imgEl = document.querySelector('#dialogImage');
    imgEl.hidden = !image;
    if (image) imgEl.src = image;
    okBtn.textContent = okText;
    // 危險操作(例如重置所有設定): 標題與確認鈕用紅色警告
    okBtn.classList.toggle('danger', danger);
    okBtn.classList.toggle('primary', !danger);
    document.querySelector('#dialogTitle').classList.toggle('danger', danger);
    cancelBtn.hidden = !confirm;

    const choicesEl = document.querySelector('#dialogChoices');
    choicesEl.replaceChildren();
    choicesEl.hidden = !choices;
    let picked = choices ? choices[0].value : null;
    (choices || []).forEach((c, i) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'dialog-choice' + (i === 0 ? ' selected' : '');
      b.textContent = c.label;
      b.onclick = () => {
        picked = c.value;
        choicesEl.querySelectorAll('.dialog-choice').forEach((x) => x.classList.toggle('selected', x === b));
      };
      choicesEl.appendChild(b);
    });

    const close = (result) => {
      dialog.hidden = true;
      document.removeEventListener('keydown', onKey);
      resolve(choices ? (result ? picked : null) : result);
    };
    const onKey = (e) => {
      if (e.key === 'Escape') close(false);
    };
    okBtn.onclick = () => close(true);
    cancelBtn.onclick = () => close(false);
    document.addEventListener('keydown', onKey);
    dialog.hidden = false;
    okBtn.focus();
  });

// 連線模擬器(ADB)失敗時，用提示窗說明原因與要檢查的地方
const alertAdbFailed = (detail) =>
  showDialog(
    '無法連線模擬器（ADB）',
    [
      detail,
      '',
      '請確認：',
      '．模擬器已經開啟',
      '．模擬器設定中已開啟 ADB（Android 偵錯橋）',
      '．「設定」的 ADB 路徑與裝置位址正確',
    ].join('\n'),
  );

// 開始任務失敗: ADB 連線問題用排查提示窗，其他錯誤直接顯示原因
const alertStartError = (res) => (res.code === 'adbFailed' ? alertAdbFailed(res.error) : showDialog('無法開始', res.error));

// 開始任務: 偵測到的解析度不在支援範圍時，後端先不啟動，這裡問使用者要不要繼續；確認才帶 confirmed=true 再送一次，取消就不執行
const startTask = async (start) => {
  let res = await start(false);
  if (res.code === 'resolutionWarning') {
    const ok = await showDialog('解析度不在支援範圍', `${res.warning}\n\n要繼續執行嗎？`, { confirm: true, okText: '繼續執行' });
    if (!ok) return;
    res = await start(true);
  }
  if (!res.ok) alertStartError(res);
};

// 處理區域選取
// 取消或失敗時維持原本已選好的範圍(後端也不會動它)；一般失敗的原因短暫顯示在座標標籤上，ADB 連線失敗則跳提示窗
const selectRegionHandler = async ({ apiMethod, param, selector, format, onSuccess }) => {
  if (isSelectingRegion) return;
  isSelectingRegion = true;
  const regionButtons = [bubbleRegionBtn];
  regionButtons.forEach((btn) => btn && (btn.disabled = true));
  try {
    const api = getApi();
    const res = param ? await api[apiMethod](param) : await api[apiMethod]();

    if (res && res.status === 'success') {
      setCoordStatus({ selector, text: format(res.selection), isSuccess: true });
      if (onSuccess) onSuccess(true);
      return;
    }

    if (res && res.status === 'failed' && res.code === 'adbFailed') {
      alertAdbFailed(res.message);
    } else if (res && res.status === 'failed' && res.message) {
      const el = document.querySelector(selector);
      const previous = { text: el.innerText, isSuccess: !el.classList.contains('text-danger') };
      setCoordStatus({ selector, text: res.message, isSuccess: false });
      setTimeout(() => {
        // 這段時間內若又重新選取或換了模式，就不要蓋掉新的狀態
        if (el.innerText === res.message) setCoordStatus({ selector, ...previous });
      }, 3000);
    }
  } finally {
    isSelectingRegion = false;
    // 立刻解鎖(執行中的任務會在下一次輪詢時再把按鈕鎖回去)
    regionButtons.forEach((btn) => btn && (btn.disabled = false));
  }
};

// 選取料理泡泡區域
const selectBubbleRegion = (e) =>
  selectRegionHandler({
    apiMethod: 'apiSelectRegion',
    param: 'bubbleRegionCoord',
    selector: '#bubbleRegionCoord',
    format: ({ x, y, w, h }) => `座標: ${x}, ${y}, ${x + w}, ${y + h}`,
    onSuccess: (ok) => (hasBubbleRegion = ok),
  });

// 綁定事件: 座標選取按鈕
document.addEventListener('DOMContentLoaded', () => {
  if (bubbleRegionBtn) bubbleRegionBtn.addEventListener('click', selectBubbleRegion);
});

// 背景定時的項目和前景定時一模一樣，直接複製前景那一整塊，把 id、設定名稱的 fishing 換成 fishingAlt
const buildAltOptions = () => {
  const clone = document.querySelector('#fishMainOptions').cloneNode(true);
  const alt = (name) => name.replace(/^fishing/, 'fishingAlt');
  clone.removeAttribute('id');
  clone.querySelectorAll('[id]').forEach((el) => (el.id = alt(el.id)));
  clone.querySelectorAll('[for]').forEach((el) => el.setAttribute('for', alt(el.getAttribute('for'))));
  clone.querySelectorAll('[data-key]').forEach((el) => (el.dataset.key = alt(el.dataset.key)));
  clone.querySelectorAll('[data-reset]').forEach((el) => (el.dataset.reset = alt(el.dataset.reset)));
  document.querySelector('#altOptions').appendChild(clone);
};
buildAltOptions();

// 採集植物(無工具)不用吃食物、不用維修盒，只有砍木頭這類用工具的功能才有這些設定。
// 採集植物、砍木頭: 兩個功能的設定、日誌與開始/停止按鈕各自獨立(設定名稱以 plant / wood 開頭)，只是版面一樣，所以用同一個函式產生
const GATHER_KINDS = [
  { prefix: 'plant', name: '採集（無工具）', countLabel: '每輪連按次數' },
  { prefix: 'wood', name: '採集（有工具）', countLabel: '每輪連按次數', tool: '斧頭' },
];
const capitalize = (text) => text[0].toUpperCase() + text.slice(1);

const resetIcon = (key) =>
  `<button type="button" class="reset-default" data-reset="${key}" aria-label="還原預設值"><svg class="icon" aria-hidden="true"><use href="#i-rotate-ccw"/></svg></button>`;

const buildGatherBlock = ({ prefix: p, name, countLabel, tool }) => {
  const P = capitalize(p);
  // 次數欄位放在上方「功能」同一行(共用列)，隨功能切換顯示
  document.querySelector(`#gather${P}Count`).innerHTML = `
    <label for="${p}Count" class="label">${countLabel}</label>
    <div class="count-field">
      <input type="number" min="1" step="1" class="setting input qty" id="${p}Count" data-key="${p}Count" />
      <span class="muted fs-12">次</span>
      ${resetIcon(`${p}Count`)}
    </div>`;
  document.querySelector(`#gather${P}`).innerHTML = `
    <div class="row">
      <div class="col">
        <label for="${p}ControlMode" class="label">操作模式（料理、雪雕、採集共用該設定）</label>
        <select id="${p}ControlMode" class="setting input" data-key="controlMode">
          <option value="screen">前景（遊戲須在最上層）</option>
          <option value="adb">背景（模擬器 ADB，可疊視窗）</option>
        </select>
        <span class="fs-12 text-warn adb-warn" data-mode-for="${p}ControlMode" hidden>模擬器容易卡頓，導致操作失敗或不正確，請斟酌使用。</span>
      </div>
    </div>
    <div class="adb-details" data-adb-details="${p}" data-mode-for="${p}ControlMode" hidden></div>

    <div class="check no-icon aligned">
      <span class="label-wrap">
        <label for="${p}IntervalSeconds">定時時間（重生基準）</label>
        <button type="button" class="help-btn" data-help="human-delay" aria-label="說明"><svg class="icon" aria-hidden="true"><use href="#i-circle-help"/></svg></button>
      </span>
      <input type="number" min="10" step="1" class="setting input qty" id="${p}IntervalSeconds" data-key="${p}IntervalSeconds" />
      <span class="muted fs-12">秒</span>
      ${resetIcon(`${p}IntervalSeconds`)}
    </div>
    <div class="check no-icon aligned">
      <span class="label-wrap">
        <label for="${p}NoPickupMinutes">多久沒採到東西就停止</label>
        <button type="button" class="help-btn" data-help="no-pickup" aria-label="說明"><svg class="icon" aria-hidden="true"><use href="#i-circle-help"/></svg></button>
      </span>
      <input type="number" min="0" step="1" class="setting input qty" id="${p}NoPickupMinutes" data-key="${p}NoPickupMinutes" />
      <span class="muted fs-12">分鐘</span>
      ${resetIcon(`${p}NoPickupMinutes`)}
      <span class="muted fs-12">（0＝不停止）</span>
    </div>

    <hr />

    ${
      tool
        ? `<div class="check mb-8">
      <input type="checkbox" class="setting-check" id="${p}RepairEnabled" data-key="${p}RepairEnabled" />
      <span class="item-icon"><img data-tpl="iconRepairBox" alt="" hidden /></span>
      <label for="${p}RepairEnabled">丟維修盒</label>
      <span class="muted fs-12">＊ 工具耐久耗盡時會丟</span>
    </div>
    <div class="check sub no-icon mb-8">
      <input type="checkbox" class="setting-check" id="${p}UseRepair" data-key="${p}UseRepair" />
      <label for="${p}UseRepair">定時丟，每</label>
      <input type="number" min="0.5" step="0.5" class="setting input qty" id="${p}RepairMinutes" data-key="${p}RepairMinutes" />
      <span class="muted fs-12">分鐘</span>
      ${resetIcon(`${p}RepairMinutes`)}
    </div>
    <div class="check sub no-icon mb-8">
      <input type="checkbox" class="setting-check" id="${p}RepairLeave" data-key="${p}RepairLeave" />
      <label for="${p}RepairLeave">離開現在的位置後再丟</label>
      <span class="muted fs-12">＊ 前景會按 S、模擬器會拉搖桿往下</span>
    </div>`
        : ''
    }
    ${
      tool
        ? `<hr />
    <div class="check no-icon mb-8">
      <input type="checkbox" class="setting-check" id="${p}UseFood" data-key="${p}UseFood" />
      <label for="${p}UseFood">吃食物</label>
      <span class="muted fs-12">體力不足時吃，吃到飽食度</span>
      <input type="number" min="1" max="100" step="1" class="setting input qty" id="${p}FoodUntil" data-key="${p}FoodUntil" />
      <span class="muted fs-12">%</span>
      ${resetIcon(`${p}FoodUntil`)}
    </div>
    <div class="check sub no-icon mb-16">
      <input type="checkbox" class="setting-check" id="${p}FoodLock" data-key="${p}FoodLock" />
      <label for="${p}FoodLock" title="第一次搜尋時開啟遊戲的「下次開啟背包保持該結果」，之後開背包就直接停在這份食物">鎖定食物，背包保持搜尋結果</label>
    </div>
    <div class="row mb-8">
      <div class="col">
        <label for="${p}FoodName" class="label">食物名稱（背包搜尋用）</label>
        <input type="text" class="setting input" id="${p}FoodName" placeholder="例如：沙拉" data-key="${p}FoodName" />
        <span class="fs-12 text-danger food-warning" hidden>請輸入要吃的食物名稱</span>
      </div>
      <div class="col">
        <label for="${p}FoodStars" class="label" title="同名食物有不同星級，搜尋結果會分成多格">食物星級</label>
        <select id="${p}FoodStars" class="setting input" data-key="${p}FoodStars">
          <option value="1">1 星</option>
          <option value="2">2 星</option>
          <option value="3">3 星</option>
          <option value="4">4 星</option>
          <option value="5">5 星</option>
          <option value="14">0-4 星</option>
          <option value="0">不限（0-5 星）</option>
        </select>
      </div>
    </div>
    <div class="check sub no-icon mb-16">
      <input type="checkbox" class="setting-check" id="${p}FoodKeepLast" data-key="${p}FoodKeepLast" />
      <label for="${p}FoodKeepLast">保留最後一次不吃</label>
    </div>

    <hr />`
        : ''
    }

    <div class="run-head">
      <div class="run-title">
        <h4 class="title">運行</h4>
        <span class="run-badge" data-run-badge="${p}"></span>
      </div>
    </div>
    <textarea id="logContainer${P}" class="log input" readonly></textarea>
    <div class="run-actions">
      <p class="run-last" data-run-last="${p}"></p>
      <button type="button" id="stop${P}Btn" class="btn danger w-120">停止</button>
      <button type="button" id="start${P}Btn" class="btn primary w-120">開始</button>
    </div>`;
};
GATHER_KINDS.forEach(buildGatherBlock);

// 操作模式選了模擬器(ADB)時，在選單下方多一個可摺疊的「模擬器連線設定」(和背景定時那一組一樣，各處共用同一份設定)。
// 位置由 data-adb-details 的佔位元素決定，值是用來區分 id 的名稱
const fillAdbDetails = () => {
  document.querySelectorAll('[data-adb-details]').forEach((box) => {
    const id = box.dataset.adbDetails;
    box.innerHTML = `
      <details class="group">
        <summary>模擬器連線設定（與設定頁共用）</summary>
        <div class="row">
          <div class="col">
            <label for="adbPreset_${id}" class="label">模擬器</label>
            <select id="adbPreset_${id}" class="input adb-preset" data-input="adbDevice_${id}">
              <option value="127.0.0.1:5555">BlueStacks / 雷電 LDPlayer（5555）</option>
              <option value="127.0.0.1:16384">MuMu 12（16384）</option>
              <option value="127.0.0.1:7555">MuMu 6（7555）</option>
              <option value="127.0.0.1:62001">夜神 Nox（62001）</option>
              <option value="">其他（自行填寫）</option>
            </select>
            <span class="muted fs-12 adb-hint" data-for="adbPreset_${id}" hidden>建議設定：請在模擬器設定中開啟 ADB。</span>
          </div>
        </div>
        <div class="row">
          <div class="col">
            <div class="label-row">
              <label for="adbDevice_${id}" class="label">ADB 裝置位址</label>
              <button type="button" class="text-btn adb-detect" data-input="adbDevice_${id}">自動偵測</button>
            </div>
            <input type="text" class="setting input" id="adbDevice_${id}" placeholder="127.0.0.1:5555" data-key="adbDevice" />
          </div>
        </div>
        <div class="row">
          <div class="col">
            <label for="adbPath_${id}" class="label">ADB 路徑</label>
            <input type="text" class="setting input" id="adbPath_${id}" placeholder="例如 C:\\platform-tools\\adb.exe" data-key="adbPath" />
          </div>
        </div>
      </details>`;
  });
};
fillAdbDetails();

// 「目前菜品」的範例圖: 遊戲食譜介面的實際截圖，紅框是要框選的菜名
const DISH_EXAMPLE = 'dish-example.png';
const BUBBLE_EXAMPLE = 'bubble-example.png';
// 設定旁的「?」說明鈕: 點一下用提示視窗說明
const HELP_TEXTS = {
  'no-pickup': {
    title: '多久沒採到東西就停止',
    message: '倒數圖示和獲得物品氣泡都超過這個時間沒出現才停止。\n可避免角色位置不小心改變，出現空揮工具等情況。',
  },
  'bubble-example': {
    title: '泡泡區域範圍',
    message: '站在鍋子前，框選所有鍋子上方的泡泡圖示（紅框處）。',
    image: BUBBLE_EXAMPLE,
  },
  'dish-example': {
    title: '記錄菜名',
    message: '在食譜畫面中，框選紅框處的菜名，每次打開都會檢查。',
    image: DISH_EXAMPLE,
  },
  'human-delay': {
    title: '定時時間與每輪多等一小段',
    message: [
      '大部分為 2 分鐘重生，每輪會再多等一小段，像真人晚幾秒才回來。',
      '',
      '．每輪在定時時間之後，隨機再多等 0.5～4.5 秒（平均約 2.5 秒）',
      '．約每 8 輪有 1 輪會分心，再多等 4～15 秒',
      '．時間到後還有 0.3～1.2 秒的反應時間才按第一下',
      '',
      '這樣不會每次都剛好隔一樣久，看起來像真人晚幾秒才發現、動手。',
    ].join('\n'),
  },
};

// 各功能的「注意事項」: 點按鈕用提示視窗顯示
const NOTICE_TEXTS = {
  cooking: {
    title: '料理注意事項',
    message: [
      '．請務必關閉遊戲內**「玩法設定 → 開始烹飪鏡頭自動轉動」**，否則畫面會自行轉動，造成點擊錯位。',
      '．建議只煮簡單的菜品，倒數時間越長越穩定。',
      '．倒數時間短的菜品，建議一次只煮 1～2 份。',
      '．本腳本建議**一次最多煮 3 份**，尚未測試更多鍋子的情況。',
    ].join('\n'),
  },
  snowCarving: {
    title: '雪雕注意事項',
    message: '．雪雕後方的背景建議越單純越好，背景太雜容易誤判而點錯位置。',
  },
  fishing: {
    title: '釣魚注意事項',
    message: '．請確保**遊戲視角**（非人物視角）**正對魚尾**且**可見**（如下圖）。',
    image: 'fishing-example.png',
  },
};
document.addEventListener('click', (e) => {
  const notice = e.target.closest('.notice-btn');
  if (notice) {
    const text = NOTICE_TEXTS[notice.dataset.notice];
    return void showDialog(text.title, text.message, { image: text.image || '' });
  }
  const btn = e.target.closest('.help-btn');
  const help = btn && HELP_TEXTS[btn.dataset.help];
  if (help) showDialog(help.title, help.message, { image: help.image || '' });
});

// 沒有開啟浮動日誌就不顯示位置選單
const syncFloatLogRow = () => {
  document.querySelector('#floatLogPositionRow').hidden = !document.querySelector('#floatLogEnabled').checked;
};
document.querySelector('#floatLogEnabled').addEventListener('change', syncFloatLogRow);

// 運行時間: 已執行多久、上一次執行的時間
const pad2 = (n) => String(n).padStart(2, '0');
const formatClock = (sec) => `${Math.floor(sec / 3600)}:${pad2(Math.floor((sec % 3600) / 60))}:${pad2(sec % 60)}`;
const formatSpan = (sec) => {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h) return `${h} 小時 ${m} 分`;
  if (m) return `${m} 分 ${sec % 60} 秒`;
  return `${sec} 秒`;
};
const formatLastRun = (last) => {
  if (!last) return '';
  const d = new Date(last.start * 1000);
  return `上次執行 ${pad2(d.getMonth() + 1)}/${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())} · 共 ${formatSpan(last.seconds)}`;
};
// 標題旁的狀態徽章(未運行 / 運行中與已執行多久)，與標題下方的「上次執行」
const renderRunTimes = (runs) => {
  document.querySelectorAll('[data-run-badge]').forEach((el) => {
    const info = runs && runs[el.dataset.runBadge];
    if (!info) return;
    el.classList.toggle('on', !!info.running);
    el.textContent = info.running ? `運行中 ${formatClock(info.elapsed)}` : '未運行';
  });
  document.querySelectorAll('[data-run-last]').forEach((el) => {
    const info = runs && runs[el.dataset.runLast];
    // 運行中不顯示(上方的徽章已經顯示運行時間)，停止後才顯示上次執行
    if (info) el.textContent = formatLastRun(info.last);
  });
};

// 選了哪一個採集功能就只顯示那一個的設定
const syncGatherKind = () => {
  const kind = document.querySelector('#gatherKind').value;
  document.querySelectorAll('[data-gather]').forEach((el) => (el.hidden = el.dataset.gather !== kind));
};
document.querySelector('#gatherKind').addEventListener('change', syncGatherKind);

// 勾了「吃食物」卻沒填食物名稱就不能開始，並在輸入框下方提示。前景或背景定時沒啟用時不檢查
const FOOD_GROUPS = [
  { prefix: 'fishing', label: '前景定時', isActive: () => isChecked('fishingEnabled') },
  { prefix: 'fishingAlt', label: '背景定時', isActive: () => isChecked('fishingAltEnabled') },
  ...GATHER_KINDS.filter(({ tool }) => tool).map(({ prefix, name }) => ({ prefix, label: name, isActive: () => document.querySelector('#gatherKind').value === prefix })),
];
// 回傳缺少食物名稱的組別名稱（前景定時 / 背景定時），同時更新輸入框的紅框與警告
const getMissingFoodGroups = () =>
  FOOD_GROUPS.filter(({ prefix, isActive }) => {
    const nameInput = document.querySelector(`#${prefix}FoodName`);
    const active = isActive();
    const missing = active && document.querySelector(`#${prefix}UseFood`).checked && !nameInput.value.trim();
    nameInput.classList.toggle('invalid', missing);
    nameInput.closest('.col').querySelector('.food-warning').hidden = !missing;
    return missing;
  }).map(({ label }) => label);

// 開始前的檢查: 回傳「為什麼不能開始」的簡短原因，空陣列代表可以開始。
// 條件與後端 validateConfig / startFishing 的檢查一致，只是提早用彈跳窗說明
const isChecked = (id) => document.querySelector(`#${id}`).checked;
const isBlank = (id) => !document.querySelector(`#${id}`).value.trim();
const isPositiveInt = (id) => {
  const value = document.querySelector(`#${id}`).value.trim();
  return /^\d+$/.test(value) && Number(value) >= 1;
};

// 這個分頁選了背景(模擬器)模式時，需要設定 ADB 路徑與裝置位址
const getAdbBlockers = (modeSelectId) => {
  const reasons = [];
  if (document.querySelector(`#${modeSelectId}`).value !== 'adb') return reasons;
  if (isBlank('adbPath')) reasons.push('未設定 ADB 路徑');
  if (isBlank('adbDevice')) reasons.push('未設定 ADB 裝置位址');
  return reasons;
};

const getCookBlockers = () => {
  const reasons = [];
  if (!hasBubbleRegion) reasons.push('未選取泡泡區域範圍座標');
  if (!isPositiveInt('expectedCookQty')) reasons.push('未設定料理數量');
  if (safeModeToggle.checked && !isPositiveInt('safeModeQty')) reasons.push('未設定安全模式份數');
  return [...reasons, ...getAdbBlockers('cookingControlMode')];
};

const getSnowBlockers = () => getAdbBlockers('snowControlMode');

const getGatherBlockers = (prefix) => {
  const kind = GATHER_KINDS.find((k) => k.prefix === prefix);
  const missingFood = getMissingFoodGroups(); // 同時更新食物名稱輸入框的紅框
  const reasons = [];
  if (!isPositiveInt(`${prefix}Count`)) reasons.push(`未設定${kind.countLabel}`);
  if (!(Number(document.querySelector(`#${prefix}IntervalSeconds`).value) >= 10)) reasons.push('定時時間至少 10 秒');
  const noPickup = document.querySelector(`#${prefix}NoPickupMinutes`).value.trim();
  if (noPickup === '' || !(Number(noPickup) >= 0)) reasons.push('未設定多久沒採到東西就停止');
  else if (Number(noPickup) > 0 && Number(noPickup) * 60 < Number(document.querySelector(`#${prefix}IntervalSeconds`).value) * 2) {
    reasons.push('多久沒採到東西就停止，要大於定時時間的 2 倍');
  }
  if (missingFood.includes(kind.name)) reasons.push('未設定食物名稱');
  return [...reasons, ...getAdbBlockers(`${prefix}ControlMode`)];
};

const getFishBlockers = () => {
  const useMain = isChecked('fishingEnabled');
  const useAlt = isChecked('fishingAltEnabled');
  const missingFood = getMissingFoodGroups(); // 同時更新食物名稱輸入框的紅框
  if (!useMain && !useAlt) return ['未啟用前景定時或背景定時'];
  const reasons = [];
  if (missingFood.length) reasons.push(`${missingFood.join('、')}未設定食物名稱`);
  if (useAlt) {
    if (isBlank('adbPathAlt')) reasons.push('背景定時未設定 ADB 路徑');
    if (isBlank('adbDeviceAlt')) reasons.push('背景定時未設定 ADB 裝置位址');
    if (!['AutoCast', 'UseBait', 'UseFood', 'UseRepair'].some((key) => isChecked(`fishingAlt${key}`))) {
      reasons.push('背景定時未勾選任何項目');
    }
  }
  return reasons;
};

// 按「開始」時若有原因擋下，用彈跳窗列出(一行一個原因)；回傳 true 表示被擋下
const alertBlockers = (reasons) => {
  if (!reasons.length) return false;
  showDialog('無法開始', reasons.map((reason) => `．${reason}`).join('\n'));
  return true;
};

// 模擬器選單: 選了就把常見的連接埠填進位址欄；位址不在選單內時顯示「其他」
const adbPresets = [...document.querySelectorAll('.adb-preset')];
const syncAdbPresets = () => {
  adbPresets.forEach((preset) => {
    const input = document.querySelector(`#${preset.dataset.input}`);
    const value = input.value.trim();
    // 選了「其他」就維持其他，讓使用者自己填；否則依位址對應到選單的項目
    const known = [...preset.options].some((o) => o.value && o.value === value);
    preset.value = known && !preset.dataset.custom ? value : '';
    // 用選單選的模擬器，位址欄鎖住(不能改)；選「其他」才可以自己填
    input.dataset.locked = preset.value ? '1' : '';
    input.disabled = !!preset.value || isAnyRunning;
    document.querySelectorAll(`.adb-hint[data-for="${preset.id}"]`).forEach((hint) => (hint.hidden = !preset.value)); // 選了模擬器才顯示建議設定
  });
};
adbPresets.forEach((preset) => {
  const input = document.querySelector(`#${preset.dataset.input}`);
  preset.addEventListener('change', () => {
    if (!preset.value) {
      preset.dataset.custom = '1';
      syncAdbPresets();
      input.focus();
      return;
    }
    delete preset.dataset.custom;
    input.value = preset.value;
    input.dispatchEvent(new Event('change'));
    syncAdbPresets();
  });
  input.addEventListener('input', () => {
    delete preset.dataset.custom;
    syncAdbPresets();
  });
});

// 自動偵測模擬器的 ADB 位址: 找到就填進位址欄；找到多個時填第一個，並列出全部讓使用者確認
const adbDetectButtons = [...document.querySelectorAll('.adb-detect')];
let isAdbDetecting = false; // 偵測中: 狀態輪詢不能把按鈕又打開
adbDetectButtons.forEach((btn) => {
  btn.addEventListener('click', async () => {
    const input = document.querySelector(`#${btn.dataset.input}`);
    const label = btn.textContent;
    if (isAdbDetecting) return;
    isAdbDetecting = true;
    adbDetectButtons.forEach((b) => (b.disabled = true));
    btn.textContent = '偵測中...';
    try {
      const res = await getApi().apiDetectAdb();
      if (res.status !== 'success') {
        await showDialog('自動偵測失敗', res.message);
        return;
      }
      if (!res.devices.length) {
        await showDialog(
          '找不到模擬器',
          ['請確認：', '．模擬器已經開啟', '．模擬器設定中已開啟 ADB（Android 偵錯橋）', '．「ADB 路徑」正確'].join('\n'),
        );
        return;
      }
      let address = res.devices[0].address;
      if (res.devices.length > 1) {
        address = await showDialog('找到多個模擬器', '請選擇要使用的模擬器：', {
          confirm: true,
          choices: res.devices.map((d) => ({ value: d.address, label: `${d.address}${d.name ? `（${d.name}）` : ''}` })),
        });
        if (!address) return;
      }
      document.querySelectorAll(`.adb-preset[data-input="${btn.dataset.input}"]`).forEach((p) => delete p.dataset.custom);
      input.value = address;
      input.dispatchEvent(new Event('change'));
      syncAdbPresets();
    } catch (e) {
      await showDialog('自動偵測失敗', String(e));
    } finally {
      btn.textContent = label;
      isAdbDetecting = false;
      adbDetectButtons.forEach((b) => (b.disabled = isAnyRunning));
    }
  });
});

// 背景定時的設定只有勾選「啟用」時才顯示
const syncAltVisibility = () => {
  const enabled = document.querySelector('#fishingAltEnabled').checked;
  document.querySelectorAll('.alt-only').forEach((el) => (el.hidden = !enabled));
};
document.querySelector('#fishingAltEnabled').addEventListener('change', syncAltVisibility);

// 料理、雪雕、採集植物、砍木頭共用同一個操作模式(設定的 controlMode)，各分頁都有一個下拉選單，改任何一個其他的會跟著變。
// 採集的選單是動態產生的，所以用事件委派
document.addEventListener('change', (e) => {
  const select = e.target;
  if (!(select instanceof HTMLSelectElement) || select.dataset.key !== 'controlMode') return;
  document.querySelectorAll('select[data-key="controlMode"]').forEach((other) => (other.value = select.value));
  syncCookAdbHint();
  syncAdbWarns();
  // 換了操作模式後，先前框選的泡泡範圍與記錄的菜品截圖是另一種畫面的，要重新選取、記錄
  hasBubbleRegion = false;
  setCoordStatus({ selector: '#bubbleRegionCoord', text: '尚未選取座標', isSuccess: false });
  getApi().apiClearDish().then((ok) => ok && showDish(null));
});

// 前景定時的設定只有勾選「啟用」時才顯示
const syncFgVisibility = () => {
  const enabled = document.querySelector('#fishingEnabled').checked;
  document.querySelectorAll('.fg-only').forEach((el) => (el.hidden = !enabled));
};
document.querySelector('#fishingEnabled').addEventListener('change', syncFgVisibility);

// 設定變更
const handleSettingChange = async (e) => {
  const key = e.target.dataset.key;
  const value = e.target.type === 'checkbox' ? e.target.checked : e.target.value;
  // 同一個設定可能有多個輸入框(例如 ADB 路徑在設定頁與背景定時區各有一個)，一起更新
  document.querySelectorAll(`.setting[data-key="${key}"]`).forEach((el) => {
    if (el !== e.target) el.value = value;
  });
  syncAdbPresets();
  await getApi().apiWriteSetting(key, value);
};

// 料理數量超過建議值(3 份)時提示
// 記錄目前菜品: 框選遊戲裡的菜名並截圖，介面顯示縮圖與記錄時間
let dishRecorded = false;
// 料理選模擬器(ADB)時畫面容易卡頓、延遲，還沒記錄菜品就提示建議設定
const syncCookAdbHint = () => {
  document.querySelector('#dishAdbHint').hidden = document.querySelector('#cookingControlMode').value !== 'adb';
};
// 操作模式選了模擬器(ADB)，下拉選單下方顯示卡頓的提醒
const syncAdbWarns = () => {
  document.querySelectorAll('.adb-warn').forEach((el) => {
    const select = document.querySelector(`#${el.dataset.modeFor}`);
    el.hidden = !select || select.value !== 'adb';
  });
  document.querySelectorAll('.adb-details').forEach((box) => {
    const select = document.querySelector(`#${box.dataset.modeFor}`);
    box.hidden = !select || select.value !== 'adb';
  });
};
document.addEventListener('change', (e) => {
  if (e.target.matches?.('select[id$="ControlMode"]')) syncAdbWarns();
});
const showDish = (image, recordedAt) => {
  dishRecorded = !!image;
  syncCookAdbHint();
  syncAdbWarns();
  const img = document.querySelector('#dishImage');
  img.hidden = !image;
  document.querySelector('#dishClearBtn').hidden = !image;
  if (image) img.src = image;
  setCoordStatus({ selector: '#dishStatus', text: image ? `已記錄（${recordedAt || ''}）` : '尚未記錄', isSuccess: !!image });
};
const recordDish = async () => {
  if (isSelectingRegion) return;
  isSelectingRegion = true;
  const btn = document.querySelector('#dishRecordBtn');
  btn.disabled = true;
  try {
    const res = await getApi().apiRecordDish();
    if (res.status === 'success') showDish(res.image, res.recordedAt);
    else if (res.status === 'failed' && res.code === 'adbFailed') alertAdbFailed(res.message);
    else if (res.status === 'failed') {
      // 跟泡泡區域一樣: 失敗原因短暫顯示在狀態文字上，不跳視窗，3 秒後還原
      const el = document.querySelector('#dishStatus');
      const previous = { text: el.innerText, isSuccess: !el.classList.contains('text-danger') };
      setCoordStatus({ selector: '#dishStatus', text: res.message, isSuccess: false });
      setTimeout(() => {
        if (el.innerText === res.message) setCoordStatus({ selector: '#dishStatus', ...previous });
      }, 3000);
    }
  } finally {
    isSelectingRegion = false;
    btn.disabled = false;
  }
};
document.querySelector('#dishRecordBtn').addEventListener('click', recordDish);
document.querySelector('#dishClearBtn').addEventListener('click', async () => {
  if (!(await showDialog('清除菜品記錄', '確定要清除已記錄的菜品嗎？', { confirm: true, danger: true, okText: '清除' }))) return;
  if (await getApi().apiClearDish()) showDish(null);
});

// 安全模式切換
safeModeToggle.addEventListener('change', (e) => {
  getApi().apiWriteSetting('isSafeMode', e.target.checked);
});

// 五星停止切換
fiveStarToggle.addEventListener('change', (e) => {
  getApi().apiWriteSetting('stopAtFiveStar', e.target.checked);
});

// 萬能食材切換
const syncWildcardTag = () => {
  document.querySelector('#wildcardTag').hidden = !wildcardToggle.checked;
};
wildcardToggle.addEventListener('change', (e) => {
  getApi().apiWriteSetting('cookUseWildcard', e.target.checked);
  syncWildcardTag();
});

// 處理日誌訊息
const handleCookingLogMessage = (log) => {
  currentCookingLogIndex = log.index;
  const logContainer = document.querySelector('#logContainer');
  logContainer.value += log.msg + '\n';
  logContainer.scrollTop = logContainer.scrollHeight;
};
const handleSnowLogMessage = (log) => {
  currentSnowLogIndex = log.index;
  const logContainerSnow = document.querySelector('#logContainerSnow');
  logContainerSnow.value += log.msg + '\n';
  logContainerSnow.scrollTop = logContainerSnow.scrollHeight;
};

const handleFishAltLogMessage = (log) => {
  currentFishAltLogIndex = log.index;
  const logContainer = document.querySelector('#logContainerFishAlt');
  logContainer.value += log.msg + '\n';
  logContainer.scrollTop = logContainer.scrollHeight;
};

const handleFishLogMessage = (log) => {
  currentFishLogIndex = log.index;
  const logContainerFish = document.querySelector('#logContainerFish');
  logContainerFish.value += log.msg + '\n';
  logContainerFish.scrollTop = logContainerFish.scrollHeight;
};

const gatherLogIndex = { plant: 0, wood: 0 };
const handleGatherLogMessage = (prefix) => (log) => {
  gatherLogIndex[prefix] = log.index;
  const logContainer = document.querySelector(`#logContainer${capitalize(prefix)}`);
  logContainer.value += log.msg + '\n';
  logContainer.scrollTop = logContainer.scrollHeight;
};

// 關掉「丟維修盒」前先問一次: 關掉之後工具耐久耗盡也不會修，採集會直接停下來。
// 在事件還沒傳到設定儲存的監聽器之前攔下來(capture)，確認後再重新送出一次 change，這次放行
let repairOffConfirmed = null;
document.addEventListener(
  'change',
  async (e) => {
    const box = e.target;
    if (!box.matches?.('input[id$="RepairEnabled"]') || box.checked) return;
    if (repairOffConfirmed === box) {
      repairOffConfirmed = null;
      return;
    }
    e.stopImmediatePropagation();
    box.checked = true;
    const ok = await showDialog(
      '關閉丟維修盒',
      '關閉後，工具耐久耗盡時不會自動丟維修盒。\n確定要關閉嗎？',
      { confirm: true, danger: true, okText: '關閉' }
    );
    if (!ok) return;
    box.checked = false;
    repairOffConfirmed = box;
    box.dispatchEvent(new Event('change', { bubbles: true }));
  },
  true
);

// 沒勾「定時丟」時，每幾分鐘的欄位與還原按鈕停用
const syncRepairOptionsDisabled = () => {
  document.querySelectorAll('input[id$="UseRepair"]').forEach((timed) => {
    const prefix = timed.id.slice(0, -'UseRepair'.length);
    const minutes = document.querySelector(`#${prefix}RepairMinutes`);
    if (minutes) minutes.disabled = minutes.disabled || !timed.checked;
    document.querySelectorAll(`.reset-default[data-reset="${prefix}RepairMinutes"]`).forEach((btn) => {
      btn.disabled = btn.disabled || !timed.checked;
    });
  });
};

document.addEventListener('change', (e) => {
  if (e.target.matches?.('input[id$="UseRepair"]')) syncRepairOptionsDisabled();
  if (e.target.matches?.('input[id$="UseFood"]')) syncFoodOptionsDisabled();
  if (e.target.matches?.('.setting-check')) syncChildVisibility();
});

// 母選項(最外層的開關)沒勾時，緊接在它後面的子選項(縮排的 .sub 與食物名稱那一列)整組隱藏，遇到分隔線或下一個母選項為止
const syncChildVisibility = () => {
  document.querySelectorAll('.check:not(.sub)').forEach((parent) => {
    const toggle = parent.querySelector(':scope > .setting-check');
    if (!toggle) return;
    for (let el = parent.nextElementSibling; el && el.matches('.check.sub, .row'); el = el.nextElementSibling) {
      if (!el.matches('.always-on')) el.classList.toggle('child-off', !toggle.checked); // always-on: 不隨母選項隱藏的說明列
    }
  });
};

// 沒勾「吃食物」時，底下的子選項(分鐘、飽食度、名稱、星級...)一律停用，包含它們的還原按鈕
const syncFoodOptionsDisabled = () => {
  document.querySelectorAll('input[id$="UseFood"]').forEach((useFood) => {
    const prefix = useFood.id.slice(0, -'UseFood'.length);
    document.querySelectorAll(`[id^="${prefix}Food"]`).forEach((el) => {
      if (!el.matches('.setting, .setting-check')) return;
      el.disabled = el.disabled || !useFood.checked;
    });
    document.querySelectorAll('.reset-default').forEach((btn) => {
      if (btn.dataset.reset?.startsWith(`${prefix}Food`)) btn.disabled = btn.disabled || !useFood.checked;
    });
  });
};

// 初始化
// 將設定值填入對應的輸入框
const fillSettings = (settings) => {
  Object.entries(settings).forEach(([key, value]) => {
    document.querySelectorAll(`.setting[data-key="${key}"], .setting-check[data-key="${key}"]`).forEach((input) => {
      if (input.type === 'checkbox') input.checked = String(value).toLowerCase() === 'true';
      else input.value = value;
    });
  });
  syncChildVisibility();
  syncRepairOptionsDisabled();
  syncCookAdbHint();
  syncAdbWarns();
  getApi().apiGetDishImage().then((image) => showDish(image, settings.cookDishRecordedAt));
  adbPresets.forEach((preset) => delete preset.dataset.custom);
  syncAdbPresets();
  syncAltVisibility();
  syncFgVisibility();
  syncGatherKind();
  syncFloatLogRow();
  safeModeToggle.checked = String(settings.isSafeMode).toLowerCase() === 'true';
  fiveStarToggle.checked = String(settings.stopAtFiveStar).toLowerCase() === 'true';
  wildcardToggle.checked = String(settings.cookUseWildcard).toLowerCase() === 'true';
  syncWildcardTag();
  if (wildcardToggle.checked) document.querySelector('#cookAdvanced').open = true; // 有開的設定藏在折疊裡看不到，開程式時先展開

  updateResetButtons();
};

// 還原設定值
document.querySelector('#resetSettingsBtn').addEventListener('click', async () => {
  const confirmed = await showDialog('還原設定值', '確定要重置所有設定，回到最初始狀態嗎？', { confirm: true, danger: true, okText: '重置' });
  if (!confirmed) return;
  const settings = await getApi().apiResetSettings();
  if (settings) {
    fillSettings(settings);
    showDish(null);
    // 操作模式可能跟著變回預設，先前框選的泡泡區域要重新選取
    hasBubbleRegion = false;
    setCoordStatus({ selector: '#bubbleRegionCoord', text: '尚未選取座標', isSuccess: false });
  }
});

// ===== 更新 =====
const updateDialog = document.querySelector('#updateDialog');
const updateNowBtn = document.querySelector('#updateNowBtn');
const updateLaterBtn = document.querySelector('#updateLaterBtn');
const updateTitle = document.querySelector('#updateTitle');
const updateNotes = document.querySelector('#updateNotes');

// 簡易 Markdown 轉換(Release 說明用): 標題、粗體、斜體、行內程式碼、連結、清單、引用、分隔線、程式碼區塊。
// 一律用 textContent 組 DOM，不用 innerHTML，說明裡的 HTML 不會被執行
const appendInline = (parent, text) => {
  const pattern = /`([^`]+)`|\*\*([^*]+)\*\*|\*([^*\s][^*]*)\*|\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)|(https?:\/\/[^\s)<]+)/g;
  let last = 0;
  for (const m of text.matchAll(pattern)) {
    if (m.index > last) parent.append(text.slice(last, m.index));
    let el;
    if (m[1] !== undefined) {
      el = document.createElement('code');
      el.textContent = m[1];
    } else if (m[2] !== undefined) {
      el = document.createElement('strong');
      appendInline(el, m[2]);
    } else if (m[3] !== undefined) {
      el = document.createElement('em');
      appendInline(el, m[3]);
    } else {
      const url = m[5] || m[6];
      el = document.createElement('a');
      el.href = '#';
      el.className = 'md-link';
      el.textContent = m[4] || url;
      el.addEventListener('click', (e) => {
        e.preventDefault();
        getApi().apiOpenLink(url);
      });
    }
    parent.append(el);
    last = m.index + m[0].length;
  }
  if (last < text.length) parent.append(text.slice(last));
};

const renderMarkdown = (container, markdown) => {
  container.replaceChildren();
  const lines = markdown.replace(/\r\n?/g, '\n').split('\n');
  let list = null;
  let para = null;
  let code = null;
  const flush = () => {
    list = null;
    para = null;
  };
  for (const line of lines) {
    if (line.trim().startsWith('```')) {
      if (code) {
        code = null;
      } else {
        flush();
        code = document.createElement('pre');
        container.appendChild(code);
      }
      continue;
    }
    if (code) {
      code.textContent += (code.textContent ? '\n' : '') + line;
      continue;
    }
    let m;
    if (!line.trim()) {
      flush();
    } else if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
      flush();
      const h = document.createElement(`h${Math.min(m[1].length + 2, 6)}`);
      appendInline(h, m[2]);
      container.appendChild(h);
    } else if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      flush();
      container.appendChild(document.createElement('hr'));
    } else if ((m = line.match(/^\s*(?:[-*+]|(\d+)\.)\s+(.*)$/))) {
      const tag = m[1] ? 'ol' : 'ul';
      if (!list || list.tagName.toLowerCase() !== tag) {
        flush();
        list = document.createElement(tag);
        container.appendChild(list);
      }
      const li = document.createElement('li');
      appendInline(li, m[2]);
      list.appendChild(li);
    } else if ((m = line.match(/^>\s?(.*)$/))) {
      flush();
      const quote = document.createElement('blockquote');
      appendInline(quote, m[1]);
      container.appendChild(quote);
    } else {
      list = null;
      if (!para) {
        para = document.createElement('p');
        container.appendChild(para);
      } else {
        para.appendChild(document.createElement('br'));
      }
      appendInline(para, line.trim());
    }
  }
};

const todayString = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};

let notifiedVersion = null; // 這次開啟程式已經跳出提示的版本，定時檢查不再重複提醒同一版

const showUpdate = (info) => {
  notifiedVersion = info.latest;
  updateTitle.textContent = `有新版本 ${info.latest}（目前 ${info.current}）`;
  // 沒有更新說明時，給一個連到 Release 頁面的連結
  renderMarkdown(updateNotes, info.notes || (info.pageUrl ? `[查看更新內容](${info.pageUrl})` : ''));
  updateNowBtn.hidden = !(info.canAutoUpdate && info.hasAsset);
  updateNowBtn.disabled = false;
  updateLaterBtn.hidden = false;
  updateNotes.scrollTop = 0;
  updateDialog.hidden = false;
  updateLaterBtn.focus();
};

// 自動檢查(啟動時)一天只提醒一次: 按過「暫時不更新」的當天不再跳出；手動檢查一律顯示
// 顯示檢查結果；kind 為 update-ok(綠)或 update-new(橘)，不給就是一般顏色
const setUpdateResult = (el, text, kind) => {
  el.textContent = text;
  el.classList.remove('update-ok', 'update-new');
  if (kind) el.classList.add(kind);
};

const checkForUpdate = async (isManual) => {
  const result = document.querySelector('#updateResult');
  if (isManual) {
    setUpdateResult(result, '檢查中...');
  }
  const info = await getApi().apiCheckUpdate();
  if (info.hasUpdate) {
    if (isManual) {
      setUpdateResult(result, `🔔 有新版本 ${info.latest}`, 'update-new');
    } else {
      const settings = await getApi().apiReadSettings();
      if (settings.updateRemindDate === todayString()) return;
    }
    showUpdate(info);
  } else if (isManual) {
    if (info.checkFailed) setUpdateResult(result, '無法連線到 GitHub，請確認網路連線');
    else setUpdateResult(result, '✅ 已是最新版本', 'update-ok');
  }
};

// 定時檢查更新(程式開著不關也能發現新版本): 每 6 小時向 GitHub 查一次。
// 任務運行中不跳視窗，只在設定頁「檢查更新」旁顯示；等任務結束後才提醒。同一版只提醒一次，按過「暫時不更新」的當天不再提醒
const UPDATE_CHECK_MS = 6 * 60 * 60 * 1000;
let lastUpdateFetch = Date.now(); // 啟動時剛檢查過
let pendingUpdate = null;
const updateTick = async () => {
  try {
    if (Date.now() - lastUpdateFetch >= UPDATE_CHECK_MS) {
      lastUpdateFetch = Date.now();
      const info = await getApi().apiCheckUpdate();
      pendingUpdate = info.hasUpdate ? info : null;
      if (pendingUpdate) setUpdateResult(document.querySelector('#updateResult'), `🔔 有新版本 ${pendingUpdate.latest}`, 'update-new');
    }
    if (!pendingUpdate || isAnyRunning || notifiedVersion === pendingUpdate.latest) return;
    if (!updateDialog.hidden || !document.querySelector('#dialog').hidden) return; // 已有視窗開著就等下一輪
    const settings = await getApi().apiReadSettings();
    if (settings.updateRemindDate === todayString()) return;
    showUpdate(pendingUpdate);
  } catch (e) {}
};
setInterval(updateTick, 60 * 1000);

updateLaterBtn.addEventListener('click', async () => {
  updateDialog.hidden = true;
  await getApi().apiWriteSetting('updateRemindDate', todayString());
  showDialog('暫時不更新', '已先不更新，今天不會再提醒。\n之後想更新，可以到「設定」頁面按「檢查更新」。');
});

updateNowBtn.addEventListener('click', async () => {
  const res = await getApi().apiInstallUpdate();
  if (!res.ok) {
    showDialog('更新失敗', res.error);
    return;
  }
  updateNowBtn.disabled = true;
  updateLaterBtn.hidden = true;
  const timer = setInterval(async () => {
    const state = await getApi().apiUpdateState();
    if (state.status === 'downloading') {
      updateTitle.textContent = `下載中 ${Math.round(state.progress * 100)}%`;
    } else if (state.status === 'extracting') {
      updateTitle.textContent = '解壓縮中...';
    } else if (state.status === 'ready') {
      clearInterval(timer);
      updateTitle.textContent = '更新完成，正在重新啟動...';
      await getApi().apiApplyUpdate();
    } else if (state.status === 'error') {
      clearInterval(timer);
      updateTitle.textContent = state.error;
      updateNowBtn.disabled = false;
      updateLaterBtn.hidden = false;
    }
  }, 300);
});

document.querySelector('#checkUpdateBtn').addEventListener('click', () => checkForUpdate(true));

const hideLoading = () => document.querySelector('#loading').classList.add('hidden');

const init = async () => {
  try {
    await setup();
  } finally {
    hideLoading();
  }
};

// 把比對模板的圖片填進設定旁邊的小圖(有 data-tpl 的 img)，找不到圖的就維持隱藏
const fillTemplateImages = async () => {
  try {
    const images = await getApi().apiGetTemplateImages();
    document.querySelectorAll('img[data-tpl]').forEach((img) => {
      const src = images[img.dataset.tpl];
      if (src) {
        img.src = src;
        img.hidden = false;
      }
    });
  } catch (e) {
    // 沒有小圖不影響使用
  }
};

// 設定旁邊的還原 icon: 提示框顯示預設值，點一下把該欄位還原成預設值
// 值已經是預設值、或有任務在執行時，還原 icon 不能點
let isAnyRunning = false;
const resetDefaults = {};
const resetField = (id) => document.querySelector(`#${id}`);
const isDefaultValue = (input, value) => {
  if (input.type === 'checkbox') return input.checked === (String(value).toLowerCase() === 'true');
  return input.value === String(value) || (input.value.trim() !== '' && Number(input.value) === Number(value));
};
const updateResetButtons = () => {
  document.querySelectorAll('.reset-default').forEach((btn) => {
    const input = resetField(btn.dataset.reset);
    if (!input) return;
    btn.disabled = isAnyRunning || isDefaultValue(input, resetDefaults[input.dataset.key]);
  });
};

// 下拉選單在選項上標示「（預設）」，不放還原按鈕
const DEFAULT_LABEL_SELECTS = ['floatLogPosition', 'floatLogTheme', 'screenResolution', 'uiScale', 'matchStrictness'];

// 只有「數值」欄位(份數、秒數、頻率...)需要還原預設值按鈕，沒有的自動補上。
// 核取方塊、下拉選單、文字欄位(ADB、停止按鍵、食物名稱)不放。
// 在一整排的設定列(.check)裡直接接在欄位(與單位)後面，獨立的欄位則把欄位與按鈕包成一列
const addMissingResetButtons = () => {
  document.querySelectorAll('input[type="number"].setting[data-key]').forEach((el) => {
    if (!el.id || !(el.dataset.key in resetDefaults)) return;
    if (document.querySelector(`.reset-default[data-reset="${el.id}"]`)) return;
    const holder = document.createElement('span');
    holder.innerHTML = resetIcon(el.id);
    const btn = holder.firstElementChild;
    if (el.parentElement.matches('.check, .count-field, .check *')) {
      const unit = el.nextElementSibling?.matches('.muted') ? el.nextElementSibling : el;
      unit.after(btn);
    } else {
      const wrap = document.createElement('div');
      wrap.className = 'field-reset';
      el.replaceWith(wrap);
      wrap.append(el, btn);
    }
  });
};

const bindResetButtons = async () => {
  const defaults = await getApi().apiGetDefaults();
  Object.assign(resetDefaults, defaults);
  addMissingResetButtons();
  DEFAULT_LABEL_SELECTS.forEach((id) => {
    const select = resetField(id);
    const option = [...select.options].find((o) => o.value === String(defaults[select.dataset.key]));
    if (option) option.textContent += '（預設）';
  });
  document.querySelectorAll('.reset-default').forEach((btn) => {
    const input = resetField(btn.dataset.reset);
    const key = input.dataset.key;
    const unit = input.nextElementSibling?.textContent.trim() || '';
    const value = defaults[key];
    const shown = input.type === 'checkbox' ? (String(value).toLowerCase() === 'true' ? '勾選' : '不勾選') : value === '' ? '空白' : `${value} ${unit}`.trim();
    btn.title = `預設 ${shown}，點一下還原`;
    btn.addEventListener('click', () => {
      if (input.type === 'checkbox') input.checked = String(value).toLowerCase() === 'true';
      else input.value = value;
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    input.addEventListener('input', updateResetButtons);
    input.addEventListener('change', updateResetButtons);
  });
  updateResetButtons();
};

const setup = async () => {
  fillTemplateImages(); // 不等待結果，避免拖慢啟動

  // 定時檢查任務狀態，控制按鈕啟用或停用、設定欄位可編輯狀態
  poll(async () => {
    const status = await getApi().apiGetStatus();
    // 任一任務運行中，所有頁面的開始鈕都不能按
    const anyRunning = !!(status.cooking || status.snowCarving || status.fishing || status.plant || status.wood);

    // 料理
    const startCookBtn = document.querySelector('#startCookBtn');
    const stopCookBtn = document.querySelector('#stopCookBtn');
    if (startCookBtn) startCookBtn.disabled = anyRunning || isSelectingRegion;
    if (stopCookBtn) stopCookBtn.disabled = !status.cooking;

    // 雪雕
    const startSnowBtn = document.querySelector('#startSnowBtn');
    const stopSnowBtn = document.querySelector('#stopSnowBtn');
    if (startSnowBtn) startSnowBtn.disabled = anyRunning || isSelectingRegion;
    if (stopSnowBtn) stopSnowBtn.disabled = !status.snowCarving;

    // 釣魚
    const startFishBtn = document.querySelector('#startFishBtn');
    const stopFishBtn = document.querySelector('#stopFishBtn');
    if (startFishBtn) startFishBtn.disabled = anyRunning || isSelectingRegion;
    getMissingFoodGroups(); // 持續更新食物名稱輸入框的紅框與提示
    if (stopFishBtn) stopFishBtn.disabled = !status.fishing;

    renderRunTimes(status.runs);

    // 拖曳浮動日誌後後端會把位置切到「自訂」，讓設定頁的選單跟著變(正在操作選單時不動)
    const floatPos = document.querySelector('#floatLogPosition');
    if (status.floatLogPosition && floatPos && document.activeElement !== floatPos) floatPos.value = status.floatLogPosition;

    // 採集(植物、砍木頭)
    GATHER_KINDS.forEach(({ prefix }) => {
      const P = capitalize(prefix);
      document.querySelector(`#start${P}Btn`).disabled = anyRunning || isSelectingRegion;
      document.querySelector(`#stop${P}Btn`).disabled = !status[prefix];
    });

    // 只要有任務在執行，設定欄位、泡泡範圍選取按鈕都設為 disabled
    const isAnyTaskRunning = status.cooking || status.snowCarving || status.fishing || status.plant || status.wood;
    document.querySelectorAll('.setting, .setting-check').forEach((input) => {
      input.disabled = isAnyTaskRunning || input.dataset.locked === '1';
    });
    isAnyRunning = isAnyTaskRunning;
    updateResetButtons();
    syncFoodOptionsDisabled();
    syncRepairOptionsDisabled();
    adbPresets.forEach((preset) => (preset.disabled = isAnyTaskRunning));
    adbDetectButtons.forEach((btn) => (btn.disabled = isAnyTaskRunning || isAdbDetecting));
    if (safeModeToggle) safeModeToggle.disabled = isAnyTaskRunning;
    if (fiveStarToggle) fiveStarToggle.disabled = isAnyTaskRunning;
    if (wildcardToggle) wildcardToggle.disabled = isAnyTaskRunning;
    const resetBtn = document.querySelector('#resetSettingsBtn');
    if (resetBtn) resetBtn.disabled = isAnyTaskRunning;
    if (bubbleRegionBtn) bubbleRegionBtn.disabled = isAnyTaskRunning || isSelectingRegion;
  }, 300);

  fillSettings(await getApi().apiReadSettings());
  await bindResetButtons();
  document.querySelector('#appVersion').textContent = await getApi().apiGetVersion();
  checkForUpdate(false); // 不等待結果，避免拖慢啟動

  // 綁定設定變更監聽
  document.querySelectorAll('.setting, .setting-check').forEach((input) => {
    input.addEventListener('change', handleSettingChange);
  });

  // 定時拉取釣魚日誌
  poll(async () => {
    const logs = await getApi().apiGetNextLogs(currentFishLogIndex, 'fishing');
    logs.forEach(handleFishLogMessage);
    const altLogs = await getApi().apiGetNextLogs(currentFishAltLogIndex, 'fishingAlt');
    altLogs.forEach(handleFishAltLogMessage);
  }, 100);

  // 定時拉取採集日誌
  GATHER_KINDS.forEach(({ prefix }) => {
    const onLog = handleGatherLogMessage(prefix);
    poll(async () => {
      const logs = await getApi().apiGetNextLogs(gatherLogIndex[prefix], prefix);
      logs.forEach(onLog);
    }, 100);
  });

  // 定時拉取料理日誌
  poll(async () => {
    const logs = await getApi().apiGetNextLogs(currentCookingLogIndex, 'cooking');
    logs.forEach(handleCookingLogMessage);
  }, 100);

  // 定時拉取雪雕日誌
  poll(async () => {
    const logs = await getApi().apiGetNextLogs(currentSnowLogIndex, 'snowCarving');
    logs.forEach(handleSnowLogMessage);
  }, 100);
};

window.addEventListener('pywebviewready', init);

// 開始料理
document.querySelector('#startCookBtn').addEventListener('click', async () => {
  if (alertBlockers(getCookBlockers())) return;
  // 每次開始前列出這次的設定請使用者確認；有記錄過菜品時一併顯示上次記錄的菜名(沒記錄就不顯示)
  const onOff = (box) => (box.checked ? '[+開啟]' : '[-關閉]');
  const lines = [
    `．料理數量：${document.querySelector('#expectedCookQty').value}`,
    `．安全模式：${safeModeToggle.checked ? `[+開啟]（煮滿 **${document.querySelector('#safeModeQty').value}** 份才收）` : '[-關閉]'}`,
    `．五星停止：${onOff(fiveStarToggle)}`,
    `．使用萬能食材：${onOff(wildcardToggle)}`
  ];
  let message = lines.join('\n');
  let image = '';
  if (dishRecorded) {
    image = (await getApi().apiGetDishImage()) || '';
    message += '\n\n上次記錄的菜品如下，這次要煮的是這道嗎？\n若不是，請取消後清除或按「記錄菜名」更新。';
  }
  const ok = await showDialog('確認料理設定', message, { confirm: true, image, okText: '開始料理' });
  if (!ok) return;
  await startTask((confirmed) => getApi().startCooking(confirmed));
});

// 停止料理
document.querySelector('#stopCookBtn').addEventListener('click', async () => {
  await getApi().stop();
});

// 開始雪雕
document.querySelector('#startSnowBtn').addEventListener('click', async () => {
  if (alertBlockers(getSnowBlockers())) return;
  await startTask((confirmed) => getApi().startSnowCarving(confirmed));
});

// 停止雪雕
document.querySelector('#stopSnowBtn').addEventListener('click', async (e) => {
  await getApi().stop();
});

// 開始釣魚
document.querySelector('#startFishBtn').addEventListener('click', async () => {
  if (alertBlockers(getFishBlockers())) return;
  await startTask((confirmed) => getApi().startFishing(confirmed));
});

// 停止釣魚
document.querySelector('#stopFishBtn').addEventListener('click', async () => {
  await getApi().stop();
});

// 開始、停止採集(植物、砍木頭各自的按鈕)
GATHER_KINDS.forEach(({ prefix }) => {
  const P = capitalize(prefix);
  document.querySelector(`#start${P}Btn`).addEventListener('click', async () => {
    if (alertBlockers(getGatherBlockers(prefix))) return;
    await startTask((confirmed) => getApi().startGathering(prefix, confirmed));
  });
  document.querySelector(`#stop${P}Btn`).addEventListener('click', async () => {
    await getApi().stop();
  });
});

// Tab 切換
document.querySelectorAll('.tab').forEach((btn) => {
  btn.addEventListener('click', function () {
    document.querySelectorAll('.tab').forEach((l) => l.classList.remove('active'));
    document.querySelectorAll('.pane').forEach((p) => p.classList.remove('show', 'active'));
    this.classList.add('active');
    const pane = document.querySelector(this.getAttribute('data-target'));
    if (pane) pane.classList.add('show', 'active');
  });
});

// 數字欄位超過上限(max)就自動改回上限，例如吃食物的飽食度最多 99。用捕獲階段先處理，後面儲存設定時拿到的就是修正後的值
document.addEventListener(
  'change',
  (e) => {
    const input = e.target;
    if (!(input instanceof HTMLInputElement) || input.type !== 'number' || input.max === '' || input.value === '') return;
    if (Number(input.value) > Number(input.max)) input.value = input.max;
  },
  true
);
