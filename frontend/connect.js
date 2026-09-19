/* Million Piksel — backend ulagichi.
 *
 * index.html mustaqil ishlaydi (demo rejim). Bu fayl unga ulanib,
 * demo o'rniga haqiqiy serverni qo'yadi: window.MP orqali.
 *
 * Django uni avtomatik qo'shadi (config/urls.py -> app_view).
 */
(function () {
  'use strict';

  var MP = window.MP;
  if (!MP) return;

  var WS_URL = (location.protocol === 'https:' ? 'wss://' : 'ws://')
             + location.host + '/ws/canvas';

  var OP_PLACE = 0x01, OP_PING = 0x02, OP_REPORT = 0x03;
  var MSG_PIXELS = 0x01, MSG_ONLINE = 0x02, MSG_ENERGY = 0x03,
      MSG_TOAST  = 0x04, MSG_RELOAD = 0x05, MSG_REJECT = 0x06;

  var ws = null;
  var token = null;
  var pending = new Map();      // "x,y" -> oldingi rang (rad etilsa qaytariladi)
  var retry = 0;
  var buffering = false;        // snapshot kelguncha efirni buferga yig'amiz
  var buffered = [];            // [x, y, c, x, y, c, ...]

  var CFG = window.MP_CONFIG || {};

  /* Chaqiruv kodi: oddiy havolada ?ref=KOD, Telegram'da startapp=KOD
   * (uni server initData ichidagi start_param dan o'zi oladi). */
  function refCode() {
    try {
      var m = /[?&]ref=([A-Za-z0-9]{4,12})/.exec(location.search);
      if (m) {
        localStorage.setItem('mp_ref', m[1].toUpperCase());
        return m[1].toUpperCase();
      }
      return localStorage.getItem('mp_ref') || '';
    } catch (e) { return ''; }
  }

  /* ------------------------------------------------------------ 1. Kirish */
  function login() {
    var ref = refCode();

    // Telegram Mini App ichida — hech narsa so'ramaymiz, initData yetarli
    var tg = window.Telegram && window.Telegram.WebApp;
    if (tg && tg.initData) {
      tg.ready();
      tg.expand();
      return post('/api/auth/telegram', { init_data: tg.initData, ref: ref });
    }

    var saved = localStorage.getItem('mp_token');
    if (saved) return Promise.resolve({ token: saved });

    return showLoginScreen(ref);
  }

  /* ------------------------------------------------ piksel-art noutbuk
   * Faqat kirish ekranida ko'rinadi va umumiy doskaga YOZILMAYDI —
   * bu bezak, foydalanuvchilar chizgan rasm emas.
   * Ekranida palitradagi 24 rang qiya tasma bo'lib o'tadi.
   */
  function drawLaptop(cv) {
    var P = MP.palette, W = 56, H = 40;
    cv.width = W; cv.height = H;
    var c = cv.getContext('2d');
    c.imageSmoothingEnabled = false;

    function box(x0, y0, x1, y1, i) {
      c.fillStyle = P[i];
      c.fillRect(x0, y0, x1 - x0 + 1, y1 - y0 + 1);
    }

    c.clearRect(0, 0, W, H);

    box(5, 1, 50, 29, 4);            // ekran ramkasi (qora)
    box(28, 2, 28, 2, 2);            // kamera nuqtasi

    // --- ekran ichi: 24 rangning qiya tasmasi ---
    var x0 = 7, x1 = 48, y0 = 3, y1 = 27;
    var dmax = (x1 - x0) + (y1 - y0);
    for (var y = y0; y <= y1; y++) {
      for (var x = x0; x <= x1; x++) {
        var d = (x - x0) + (y1 - y);                     // qiya o'q
        var i = Math.min(23, Math.floor(d / (dmax + 1) * 24));
        c.fillStyle = P[i];
        c.fillRect(x, y, 1, 1);
      }
    }

    // --- korpus ---
    box(3, 30, 52, 30, 3);
    box(1, 31, 54, 31, 2);
    box(0, 32, 55, 32, 1);
    box(24, 31, 31, 31, 3);          // trekpad
    box(6, 33, 49, 33, 3);           // soya
  }

  /* Oddiy brauzer — bu yerdan chizib bo'lmaydi, Telegram'ga yo'naltiramiz.
   *
   * Yagona kirish yo'li Telegram Mini App: imzo bot tokeni bilan
   * tekshiriladi, ya'ni soxta hisob ochish qimmatga tushadi. Shuning
   * uchun bu ekran kirish emas — havola beradi. Promise ataylab hech
   * qachon hal bo'lmaydi: doska orqada ko'rinib turadi, lekin chizib
   * bo'lmaydi.
   */
  function showLoginScreen(ref) {
    return new Promise(function () {
      var bot = CFG.bot_username || '';
      var link = bot
        ? 'https://t.me/' + bot + '/app' + (ref ? '?startapp=' + ref : '')
        : '';

      var box = document.createElement('div');
      box.id = 'mp-login';
      box.innerHTML =
        '<div class="mp-login-card">' +
          '<canvas id="mp-art" aria-hidden="true"></canvas>' +
          '<h2>Million Piksel</h2>' +
          '<p>1 000 000 piksellik umumiy doska. Chizish uchun ilovani ' +
          'Telegram orqali oching — kirish avtomatik bo‘ladi.</p>' +
          (link
            ? '<a class="mp-tg" href="' + link + '">Telegramda ochish</a>'
            : '<span class="mp-warn">Bot hali sozlanmagan ' +
              '(TELEGRAM_BOT_USERNAME)</span>') +
          '<p class="mp-login-note">Doskani shu yerdan kuzatishingiz ' +
          'mumkin, lekin piksel qo‘yish faqat Telegramda.</p>' +
        '</div>';
      document.body.appendChild(box);

      var css = document.createElement('style');
      css.textContent =
        // Fon shaffof — orqada haqiqiy doska ko'rinib turadi
        '#mp-login{position:fixed;inset:0;z-index:100;display:flex;' +
        'align-items:center;justify-content:center;padding:16px;' +
        '-webkit-backdrop-filter:blur(3px);backdrop-filter:blur(3px)}' +
        '#mp-login::before{content:"";position:absolute;inset:0;' +
        'background:var(--bg);opacity:.72}' +
        '.mp-login-card{position:relative;z-index:1;background:var(--panel);' +
        'border:1px solid var(--line);' +
        'border-radius:var(--rp);padding:24px 22px 26px;max-width:340px;' +
        'width:100%;text-align:center;}' +
        '#mp-art{width:224px;max-width:100%;height:auto;' +
        'aspect-ratio:56/40;image-rendering:pixelated;display:block;' +
        'margin:0 auto 14px}' +
        '.mp-login-card h2{margin:0 0 8px;font-size:20px}' +
        '.mp-login-card p{margin:0 0 18px;color:var(--muted);font-size:13px;' +
        'line-height:1.5}' +
        '.mp-login-note{margin:16px 0 0 !important;font-size:11px}' +
        '.mp-tg{display:block;padding:11px 14px;border-radius:var(--r);' +
        'background:var(--accent);color:#fff;text-decoration:none;' +
        'font-weight:600;font-size:14px}' +
        '.mp-warn{display:block;font-size:11px;color:var(--muted)}';
      document.head.appendChild(css);

      drawLaptop(document.getElementById('mp-art'));
    });
  }

  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) {
      if (!r.ok) throw new Error('auth ' + r.status);
      return r.json();
    });
  }

  /* -------------------------------------------------- 2. Snapshot (1 MB) */
  function loadSnapshot() {
    return fetch('/api/canvas.bin', { cache: 'no-store' })
      .then(function (r) { return r.arrayBuffer(); })
      .then(function (buf) { MP.loadSnapshot(new Uint8Array(buf)); });
  }

  /* ------------------------------------------------------- 3. WebSocket */
  function connect() {
    ws = new WebSocket(WS_URL + '?token=' + encodeURIComponent(token));
    ws.binaryType = 'arraybuffer';

    /* Tartib muhim: AVVAL WebSocket ochiladi, KEYIN snapshot yuklanadi.
     * Teskari qilinsa poyga chiqadi — snapshot olingandan keyin, WS
     * ulangunicha qo'yilgan piksellar na snapshotda, na efirda bo'ladi
     * va o'sha piksellar sahifa yangilanmaguncha ko'rinmay qoladi.
     * Shuning uchun snapshot kelguncha efirdagi piksellarni buferga
     * yig'amiz va snapshot ustiga qo'llaymiz. */
    ws.onopen = function () {
      retry = 0;
      pending.clear();
      buffering = true;
      buffered.length = 0;

      loadSnapshot().then(function () {
        for (var i = 0; i < buffered.length; i += 3) {
          MP.remotePixel(buffered[i], buffered[i + 1], buffered[i + 2]);
        }
        buffered.length = 0;
        buffering = false;
      }).catch(function () { buffering = false; });
    };

    ws.onmessage = function (ev) {
      var d = new DataView(ev.data);
      var type = d.getUint8(0);

      if (type === MSG_PIXELS) {
        // [uint16 x][uint16 y][uint8 c] * n  -> bitta kadrda qo'llanadi
        var n = (d.byteLength - 1) / 5;
        var flat = new Array(n * 3);
        for (var i = 0; i < n; i++) {
          var o = 1 + i * 5;
          var x = d.getUint16(o), y = d.getUint16(o + 2), c = d.getUint8(o + 4);
          flat[i * 3] = x; flat[i * 3 + 1] = y; flat[i * 3 + 2] = c;
          pending.delete(x + ',' + y);          // server tasdiqladi
        }
        if (buffering) {
          // snapshot hali kelmadi — yo'qotmasdan saqlab turamiz
          for (var k = 0; k < flat.length; k++) buffered.push(flat[k]);
        } else {
          MP.remoteBatch(flat);
        }

      } else if (type === MSG_ONLINE) {
        MP.setOnline(d.getUint32(1));           // kullaut shundan hisoblanadi

      } else if (type === MSG_ENERGY) {
        MP.setMaxEnergy(d.getUint16(3));      // bonus bilan o'zgarishi mumkin
        MP.setEnergy(d.getUint16(1), d.getUint32(5));

      } else if (type === MSG_TOAST) {
        MP.toast(new TextDecoder().decode(new Uint8Array(ev.data, 1)));

      } else if (type === MSG_RELOAD) {
        loadSnapshot();                          // katta rollback bo'ldi

      } else if (type === MSG_REJECT) {
        var rx = d.getUint16(1), ry = d.getUint16(3), key = rx + ',' + ry;
        if (pending.has(key)) {
          MP.remotePixel(rx, ry, pending.get(key));
          pending.delete(key);
        }
      }
    };

    ws.onclose = function (e) {
      if (e.code === 4001) { localStorage.removeItem('mp_token');
                             MP.toast('Qaytadan kiring'); return; }
      if (e.code === 4003) { MP.toast('Hisobingiz bloklangan'); return; }
      if (e.code === 4029) { MP.toast('Juda tez — biroz sekinlashtiring'); }

      retry = Math.min(retry + 1, 6);
      var wait = Math.pow(2, retry) * 300 + Math.random() * 500;
      MP.toast('Aloqa uzildi, qayta ulanmoqda…');
      // snapshot'ni connect() ning onopen'i o'zi yuklaydi
      setTimeout(connect, wait);
    };
  }

  /* --------------------------------------------------- 4. index.html ilgaklari */
  MP.onPaint = function (x, y, color, prev) {
    if (!ws || ws.readyState !== 1) return;
    pending.set(x + ',' + y, prev);
    var b = new DataView(new ArrayBuffer(6));
    b.setUint8(0, OP_PLACE); b.setUint16(1, x); b.setUint16(3, y);
    b.setUint8(5, color);
    ws.send(b.buffer);
  };

  MP.onReport = function (x, y) {
    if (!ws || ws.readyState !== 1) return;
    var b = new DataView(new ArrayBuffer(5));
    b.setUint8(0, OP_REPORT); b.setUint16(1, x); b.setUint16(3, y);
    ws.send(b.buffer);
  };

  setInterval(function () {
    if (ws && ws.readyState === 1) ws.send(new Uint8Array([OP_PING]).buffer);
  }, 20000);

  /* ------------------------------------------------------------- start
   * Snapshot AVVAL yuklanadi (u ochiq endpoint) — shunda kirish ekrani
   * orqasida haqiqiy foydalanuvchilar chizgan doska ko'rinib turadi.
   */
  loadSnapshot()
    .then(function () { MP.cover(); })   // fon butun ekranni qoplasin
    .catch(function () { /* doska yuklanmasa ham kirishga xalaqit bermaydi */ })
    .then(login)
    .then(function (res) {
      token = res.token;
      window.MP_TOKEN = token;        // onboarding.js /api/me uchun ishlatadi
      localStorage.setItem('mp_token', token);
      MP.fit();       // kirgandan keyin butun doska ko'rinadi
      connect();      // yangi snapshot'ni onopen o'zi yuklaydi
      if (MP.maybeShowIntro) MP.maybeShowIntro();
    })
    .catch(function (err) {
      console.error(err);
      MP.toast('Serverga ulanib bo‘lmadi');
    });
})();
