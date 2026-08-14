// Прогон стартового кода index.html с подставным DOM: проверяем, что страница
// не молчит ни в одном из сценариев отказа.
const fs = require('fs');
const vm = require('vm');

const html = fs.readFileSync('D:/OpenClawData/workspace-coder/vk-callback/index.html', 'utf8');
const code = html.match(/<script>\s*\(function \(\)[\s\S]*?<\/script>/)[0]
  .replace(/^<script>/, '').replace(/<\/script>$/, '');

function makeEl() {
  return { innerHTML: '', textContent: '', value: '', disabled: false,
           className: '', onclick: null, onchange: null,
           classList: { remove() {}, add() {} }, appendChild() {} };
}

function run(name, { bridge, storageThrows, search = '' }) {
  const els = {};
  const listeners = {};
  const sandbox = {
    console: { log() {}, error() {} },
    setTimeout, clearTimeout, setInterval, clearInterval,
    Promise, JSON, Date, Error, URLSearchParams,
    fetch: () => Promise.reject(new Error('no net')),
  };
  sandbox.window = sandbox;
  sandbox.location = { search, origin: 'https://hawkey-prog.github.io', pathname: '/vk-callback/' };
  // Верхнее окно отличается от текущего — так выглядит iframe VK.
  sandbox.parent = { name: 'top' };
  sandbox.vkBridge = bridge;
  sandbox.localStorage = {
    getItem(k) { if (storageThrows) throw new Error('SecurityError'); return null; },
    setItem(k, v) { if (storageThrows) throw new Error('SecurityError'); },
  };
  sandbox.document = {
    getElementById(id) { return (els[id] = els[id] || makeEl()); },
    createElement: makeEl,
  };
  sandbox.addEventListener = (ev, fn) => { listeners[ev] = fn; };

  let threw = null;
  try {
    vm.createContext(sandbox);
    vm.runInContext(code, sandbox);
  } catch (e) { threw = e.message; }

  return {
    threw,
    diag: els.diag ? els.diag.textContent : '(нет)',
    state: (els.authState ? els.authState.innerHTML : '').replace(/<[^>]+>/g, ''),
    clickable: !!(els.btnAuth && typeof els.btnAuth.onclick === 'function'),
    els, listeners, sandbox,
  };
}

const okBridge = {
  isEmbedded: () => true,
  send: (m) => m === 'VKWebAppInit' ? Promise.resolve({ result: true })
                                    : Promise.reject({ error_data: { error_reason: 'нет доступа' } }),
};

let fails = 0;
function check(name, cond, extra) {
  console.log((cond ? '  ok   ' : '  FAIL ') + name + (cond ? '' : '  ← ' + JSON.stringify(extra)));
  if (!cond) fails++;
}

const VK_LAUNCH = '?vk_app_id=54720386&vk_user_id=146275235&vk_is_app_user=1';

console.log('\n1. vk-bridge не загрузился');
let r = run('no bridge', { bridge: undefined, storageThrows: false });
check('скрипт не упал', !r.threw, r.threw);
check('диагностика говорит про мост', /vk-bridge: НЕ загружен/.test(r.diag), r.diag);
check('статус объясняет причину', /не загрузился/.test(r.state), r.state);
check('кнопка всё равно с обработчиком', r.clickable);

console.log('\n2. Открыто вне VK');
r = run('not embedded', { bridge: { isEmbedded: () => false, send: () => new Promise(() => {}) }, storageThrows: false });
check('скрипт не упал', !r.threw, r.threw);
check('диагностика: запуска из VK нет', /запуск из VK: нет/.test(r.diag), r.diag);
check('статус зовёт открыть в VK', /не внутри VK/.test(r.state), r.state);

console.log('\n2а. Запуск из VK виден независимо от моста');
r = run('vk params, no bridge', { bridge: undefined, storageThrows: false, search: VK_LAUNCH });
check('параметры VK распознаны', /запуск из VK: да, app 54720386/.test(r.diag), r.diag);
check('iframe распознан', /iframe: да/.test(r.diag), r.diag);
check('при этом видно, что мост не поднялся', /vk-bridge: НЕ загружен/.test(r.diag), r.diag);

console.log('\n3. Хранилище закрыто (iframe VK)');
r = run('storage throws', { bridge: okBridge, storageThrows: true, search: VK_LAUNCH });
check('скрипт не упал из-за localStorage', !r.threw, r.threw);
check('APP_ID виден в диагностике', /APP_ID: 54720386/.test(r.diag), r.diag);
check('запуск из VK распознан', /запуск из VK: да/.test(r.diag), r.diag);

console.log('\n4. Нормальный запуск внутри VK');
r = run('ok', { bridge: okBridge, storageThrows: false, search: VK_LAUNCH });
check('скрипт не упал', !r.threw, r.threw);
check('мост загружен', /vk-bridge: загружен/.test(r.diag), r.diag);
check('обработчики навешаны',
  r.clickable && typeof r.els.btnWorker.onclick === 'function' && typeof r.els.btnPush.onclick === 'function');
check('перехватчик отказов промисов установлен', typeof r.listeners.unhandledrejection === 'function');

// Нажимаем «Выдать права» — мост откажет, ошибка обязана появиться на экране.
r.els.btnAuth.onclick();
setTimeout(() => {
  check('отказ моста показан пользователем', /нет доступа/.test(r.els.authInfo.innerHTML),
    r.els.authInfo.innerHTML);

  console.log('\n5. Мост есть, но VKWebAppGetAuthToken падает Error-ом');
  const r2 = run('err', {
    bridge: { isEmbedded: () => true,
              send: (m) => m === 'VKWebAppInit' ? Promise.resolve({}) : Promise.reject(new Error('окно закрыто')) },
    storageThrows: false });
  r2.els.btnAuth.onclick();
  setTimeout(() => {
    check('текст Error показан, а не {}', /окно закрыто/.test(r2.els.authInfo.innerHTML),
      r2.els.authInfo.innerHTML);
    console.log('\n' + (fails ? 'ПРОВАЛЕНО: ' + fails : 'ВСЁ ЗЕЛЁНОЕ'));
    process.exit(fails ? 1 : 0);
  }, 50);
}, 50);
