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
    Promise, JSON, Date, Error, URLSearchParams, encodeURIComponent,
    fetch: (url, opts) => {
      const reply = sandbox.__reply && sandbox.__reply(url, opts);
      if (!reply) return Promise.reject(new Error('no net'));
      return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(reply) });
    },
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
    listTest();
  }, 50);
}, 50);

// 6. Список на модерацию: приходит с сервера, рисуется, отметка убирает строку.
function listTest() {
  console.log('\n6. Список на модерацию');
  const r3 = run('list', { bridge: okBridge, storageThrows: false, search: VK_LAUNCH });
  const acked = [];
  r3.sandbox.__reply = (url, opts) => {
    if (String(url).includes('/vk/queue/list')) {
      return { total: 2, group_id: '236838246', tasks: [
        { id: 'aaa1', action: 'remove', user_id: '277162801', reason: 'client-side mode', created: 1786000000 },
        { id: 'bbb2', action: 'ban', user_id: '1002', reason: 'спам', created: 1786000100 },
      ] };
    }
    if (String(url).includes('/vk/queue/ack')) {
      acked.push(JSON.parse(opts.body));
      return { status: 'ok' };
    }
    return null;
  };
  r3.els.serverUrl.value = 'https://89-108-78-99.sslip.io';

  r3.els.btnLoadList.onclick();
  setTimeout(() => {
    const html = r3.els.list.innerHTML;
    check('строки отрисованы', (html.match(/class="item"/g) || []).length === 2, html.slice(0, 200));
    check('ссылка на пользователя ведёт в VK',
      html.includes('https://vk.com/id277162801'), html.slice(0, 300));
    check('видно действие «удалить»', /удалить/.test(html));
    check('видно действие «заблокировать»', /заблокировать/.test(html));
    check('счётчик очереди показан', /В очереди: 2/.test(r3.els.listState.textContent),
      r3.els.listState.textContent);
    check('ссылка на сообщество проставлена',
      r3.els.linkGroup.href === 'https://vk.com/club236838246', r3.els.linkGroup.href);

    // Нажимаем «Сделано» у первой строки.
    r3.els.list.onclick({ target: {
      getAttribute: (a) => (a === 'data-done' ? 'aaa1' : null), disabled: false } });
    setTimeout(() => {
      check('отметка ушла на сервер как выполненная',
        acked.length === 1 && acked[0].id === 'aaa1' && acked[0].ok === true, acked);
      check('строка исчезла из списка',
        (r3.els.list.innerHTML.match(/class="item"/g) || []).length === 1);
      check('счётчик уменьшился', /В очереди: 1/.test(r3.els.listState.textContent),
        r3.els.listState.textContent);

      // «Пропустить» должно уходить с ok=false и пояснением.
      r3.els.list.onclick({ target: {
        getAttribute: (a) => (a === 'data-skip' ? 'bbb2' : null), disabled: false } });
      setTimeout(() => {
        check('пропуск помечен как невыполненный',
          acked.length === 2 && acked[1].ok === false && /вручную/.test(acked[1].error), acked);
        console.log('\n' + (fails ? 'ПРОВАЛЕНО: ' + fails : 'ВСЁ ЗЕЛЁНОЕ'));
        process.exit(fails ? 1 : 0);
      }, 60);
    }, 60);
  }, 60);
}
