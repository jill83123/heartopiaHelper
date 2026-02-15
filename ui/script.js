const bubbleRegionBtn = document.querySelector('#bubbleRegionBtn');
const cookPointBtn = document.querySelector('#cookPointBtn');
const snowDetectRegionBtn = document.querySelector('#snowDetectRegionBtn');
const safeModeToggle = document.querySelector('#safeModeToggle');
const debugModeToggle = document.querySelector('#debugModeToggle');

let hasBubbleRegion = false;
let hasCookBtnPoint = false;
let hasSnowDetectRegion = false;

let currentCookingLogIndex = 0;
let currentSnowLogIndex = 0;

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

// 處理區域選取
const selectRegionHandler = async ({ apiMethod, param, selector, format, onSuccess }) => {
  const api = getApi();
  const res = param ? await api[apiMethod](param) : await api[apiMethod]();

  if (!res || res.status === 'failed') {
    setCoordStatus({
      selector,
      text: '尚未選取座標',
      isSuccess: false,
    });
    if (onSuccess) onSuccess(false);
    return;
  }

  setCoordStatus({
    selector,
    text: format(res.selection),
    isSuccess: true,
  });
  if (onSuccess) onSuccess(true);
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

// 選取開始烹飪按鈕座標
const selectCookPoint = (e) =>
  selectRegionHandler({
    apiMethod: 'apiSelectPoint',
    selector: '#startCookCoord',
    format: ({ x, y }) => `座標: ${x}, ${y}`,
    onSuccess: (ok) => (hasCookBtnPoint = ok),
  });

// 選取雪雕偵測區域
const selectSnowDetectRegion = (e) =>
  selectRegionHandler({
    apiMethod: 'apiSelectRegion',
    param: 'snowDetectRegionCoord',
    selector: '#snowDetectRegionCoord',
    format: ({ x, y, w, h }) => `座標: ${x}, ${y}, ${x + w}, ${y + h}`,
    onSuccess: (ok) => (hasSnowDetectRegion = ok),
  });

// 綁定事件: 座標選取按鈕
document.addEventListener('DOMContentLoaded', () => {
  if (bubbleRegionBtn) bubbleRegionBtn.addEventListener('click', selectBubbleRegion);
  if (cookPointBtn) cookPointBtn.addEventListener('click', selectCookPoint);
  if (snowDetectRegionBtn) snowDetectRegionBtn.addEventListener('click', selectSnowDetectRegion);
});

// 設定變更
const handleSettingChange = async (e) => {
  const key = e.target.dataset.key;
  await getApi().apiWriteSetting(key, e.target.value);
};

// 安全模式切換
safeModeToggle.addEventListener('change', (e) => {
  getApi().apiWriteSetting('isSafeMode', e.target.checked);
});

// 除錯模式切換
debugModeToggle.addEventListener('change', (e) => {
  getApi().apiWriteSetting('isDebugMode', e.target.checked);
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

// 初始化
const init = async () => {
  // 定時檢查任務狀態，控制按鈕啟用或停用、設定欄位可編輯狀態
  setInterval(async () => {
    const status = await getApi().apiGetStatus();

    // 料理
    const startCookBtn = document.querySelector('#startCookBtn');
    const stopCookBtn = document.querySelector('#stopCookBtn');
    if (startCookBtn) startCookBtn.disabled = !!status.cooking;
    if (stopCookBtn) stopCookBtn.disabled = !status.cooking;

    // 雪雕
    const startSnowBtn = document.querySelector('#startSnowBtn');
    const stopSnowBtn = document.querySelector('#stopSnowBtn');
    if (startSnowBtn) startSnowBtn.disabled = !!status.snowCarving;
    if (stopSnowBtn) stopSnowBtn.disabled = !status.snowCarving;

    // 只要有任務在執行，設定欄位、偵測範圍選取按鈕都設為 disabled
    const isAnyTaskRunning = status.cooking || status.snowCarving;
    document.querySelectorAll('.setting').forEach((input) => {
      input.disabled = isAnyTaskRunning;
    });
    if (safeModeToggle) safeModeToggle.disabled = isAnyTaskRunning;
    if (debugModeToggle) debugModeToggle.disabled = isAnyTaskRunning;
    if (bubbleRegionBtn) bubbleRegionBtn.disabled = isAnyTaskRunning;
    if (cookPointBtn) cookPointBtn.disabled = isAnyTaskRunning;
    if (snowDetectRegionBtn) snowDetectRegionBtn.disabled = isAnyTaskRunning;
  }, 300);

  const settings = await getApi().apiReadSettings();

  // 將設定值填入對應的輸入框
  Object.entries(settings).forEach(([key, value]) => {
    const input = document.querySelector(`.setting[data-key="${key}"]`);
    if (input) input.value = value;
  });
  if (String(settings.isSafeMode).toLowerCase() === 'true') {
    if (safeModeToggle) safeModeToggle.checked = true;
  }
  if (String(settings.isDebugMode).toLowerCase() === 'true') {
    if (debugModeToggle) debugModeToggle.checked = true;
  }

  // 綁定設定變更監聽
  document.querySelectorAll('.setting').forEach((input) => {
    input.addEventListener('change', handleSettingChange);
  });

  // 定時拉取料理日誌
  setInterval(async () => {
    const log = await getApi().apiGetNextLogs(currentCookingLogIndex, 'cooking');
    if (log) handleCookingLogMessage(log);
  }, 100);

  // 定時拉取雪雕日誌
  setInterval(async () => {
    const log = await getApi().apiGetNextLogs(currentSnowLogIndex, 'snowCarving');
    if (log) handleSnowLogMessage(log);
  }, 100);
};

window.addEventListener('pywebviewready', init);

// 開始料理
document.querySelector('#startCookBtn').addEventListener('click', async () => {
  if (!hasBubbleRegion || !hasCookBtnPoint) {
    alert('缺少座標！');
    return;
  }
  await getApi().startCooking();
});

// 停止料理
document.querySelector('#stopCookBtn').addEventListener('click', async () => {
  await getApi().stop();
});

// 開始雪雕
document.querySelector('#startSnowBtn').addEventListener('click', async () => {
  if (!hasSnowDetectRegion) {
    alert('缺少座標！');
    return;
  }
  await getApi().startSnowCarving();
});

// 停止雪雕
document.querySelector('#stopSnowBtn').addEventListener('click', async (e) => {
  await getApi().stop();
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
