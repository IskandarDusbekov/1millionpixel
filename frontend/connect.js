/* Million Piksel — backend ulagichi.
 *
 * index.html doskani chizadi va foydalanuvchi bilan gaplashadi; bu fayl
 * unga serverni ulaydi (window.MP orqali): kirish, snapshot, WebSocket.
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

    var saved = null;
    try { saved = localStorage.getItem('mp_token'); } catch (e) {}
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

  function fmtTime(sec) {
    var m = Math.floor(sec / 60), s = sec % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  /* Oddiy brauzer uchun kirish oynasi.
   *
   * Ikki yo'l, ikkalasida ham Telegram hisobi tekshiriladi (soxta hisob
   * ochish qimmatga tushadi):
   *   1) Mini App — bir bosishda, hech narsa tasdiqlash shart emas;
   *   2) Bot orqali — brauzer kod oladi, foydalanuvchi uni botda TASDIQLAYDI,
   *      brauzer natijani so'rab turadi.
   * Promise kirish tugagach hal bo'ladi; shu paytgacha doska orqada
   * ko'rinib turadi, lekin chizib bo'lmaydi.
   */
  function showLoginScreen(ref) {
    return new Promise(function (resolve) {
      var bot = CFG.bot_username || '';
      var appLink = bot
        ? 'https://t.me/' + bot + '/app' + (ref ? '?startapp=' + ref : '')
        : '';

      var box = document.createElement('div');
      box.id = 'mp-login';
      box.innerHTML =
        '<div class="mp-login-card">' +
          '<canvas id="mp-art" aria-hidden="true"></canvas>' +
          '<h2>' + (CFG.site_name || 'Million Piksel') + '</h2>' +
          '<p id="mp-lead">1 000 000 piksellik umumiy doska. Chizish uchun ' +
          'Telegram hisobingiz bilan kiring — bir necha soniya.</p>' +
          (bot
            ? '<button class="mp-tg" id="mp-botbtn" type="button">' +
                'Telegram bot orqali kirish</button>' +
              '<div id="mp-wait" hidden>' +
                '<div class="mp-spin" aria-hidden="true"></div>' +
                '<div id="mp-waittxt">Telegramda «Tasdiqlayman» tugmasini bosing</div>' +
                '<a class="mp-tg mp-alt" id="mp-open" target="_blank" ' +
                  'rel="noopener" href="#">Telegramni ochish</a>' +
                '<button class="mp-link" id="mp-cancel" type="button">Bekor qilish</button>' +
              '</div>' +
              '<a class="mp-link" id="mp-app" href="' + appLink + '">' +
                'yoki Mini App sifatida ochish</a>'
            : '<span class="mp-warn">Bot hali sozlanmagan ' +
              '(TELEGRAM_BOT_USERNAME)</span>') +
          '<p class="mp-login-note">Doskani shu yerdan kuzatishingiz ' +
          'mumkin, chizish uchun kirish kerak.</p>' +
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
        'border-radius:var(--rp);padding:24px 22px 22px;max-width:340px;' +
        'width:100%;text-align:center;}' +
        '#mp-art{width:224px;max-width:100%;height:auto;' +
        'aspect-ratio:56/40;image-rendering:pixelated;display:block;' +
        'margin:0 auto 14px}' +
        '.mp-login-card h2{margin:0 0 8px;font-size:20px}' +
        '.mp-login-card p{margin:0 0 18px;color:var(--muted);font-size:13px;' +
        'line-height:1.5}' +
        '.mp-login-note{margin:16px 0 0 !important;font-size:11px}' +
        '.mp-tg{display:block;width:100%;padding:12px 14px;border-radius:var(--r);' +
        'border:0;background:var(--accent);color:var(--accent-fg,#fff);' +
        'text-decoration:none;font:inherit;font-weight:600;font-size:14px;cursor:pointer}' +
        '.mp-tg.mp-alt{background:transparent;color:var(--text);' +
        'border:1px solid var(--line);margin-top:12px;font-weight:500}' +
        '.mp-link{display:block;margin:12px auto 0;background:none;border:0;' +
        'color:var(--muted);font:inherit;font-size:12px;cursor:pointer;' +
        'text-decoration:underline}' +
        '#mp-wait{color:var(--text);font-size:13px;line-height:1.5}' +
        '#mp-wait[hidden]{display:none}' +
        '.mp-spin{width:26px;height:26px;margin:2px auto 10px;border-radius:50%;' +
        'border:3px solid var(--line);border-top-color:var(--accent);' +
        'animation:mpspin 1s linear infinite}' +
        '@keyframes mpspin{to{transform:rotate(360deg)}}' +
        '.mp-warn{display:block;font-size:11px;color:var(--muted)}';
      document.head.appendChild(css);

      drawLaptop(document.getElementById('mp-art'));
      if (!bot) return;

      var btn = document.getElementById('mp-botbtn');
      var wait = document.getElementById('mp-wait');
      var waitTxt = document.getElementById('mp-waittxt');
      var openA = document.getElementById('mp-open');
      var appA = document.getElementById('mp-app');
      var timer = null, tick = null, active = false, ttl = 0;

      function stop() {
        active = false;
        clearInterval(timer); clearInterval(tick);
        wait.hidden = true; btn.hidden = false; appA.hidden = false;
      }
      function fail(msg) {
        stop();
        document.getElementById('mp-lead').textContent = msg;
      }

      document.getElementById('mp-cancel').onclick = stop;

      btn.onclick = function () {
        btn.disabled = true;
        post('/api/auth/bot/start', {}).then(function (s) {
          btn.disabled = false;
          active = true; ttl = s.expires;
          btn.hidden = true; appA.hidden = true; wait.hidden = false;
          openA.href = s.link;
          // Ko'p brauzerlar bu yerda oynani ochadi; bloklansa — pastdagi tugma bor
          try { window.open(s.link, '_blank', 'noopener'); } catch (e) {}

          tick = setInterval(function () {
            ttl--;
            waitTxt.textContent = 'Telegramda «Tasdiqlayman» tugmasini bosing · ' + fmtTime(Math.max(0, ttl));
            if (ttl <= 0) fail('Vaqt tugadi. Qayta urinib ko‘ring.');
          }, 1000);

          timer = setInterval(function () {
            if (!active) return;
            var q = '/api/auth/bot/poll?code=' + encodeURIComponent(s.code) +
                    (ref ? '&ref=' + encodeURIComponent(ref) : '');
            fetch(q, { cache: 'no-store' })
              .then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
              .then(function (r) {
                if (!active) return;
                if (!r.ok) { fail(r.j.detail || 'Kirishda xatolik'); return; }
                if (r.j.status === 'expired') { fail('Havola eskirdi. Qayta urinib ko‘ring.'); return; }
                if (r.j.status === 'ok') {
                  active = false; stop();
                  box.remove(); css.remove();
                  resolve(r.j);
                }
              })
              .catch(function () { /* tarmoq xatosi — keyingi urinishda */ });
          }, 2000);
        }).catch(function (err) {
          btn.disabled = false;
          fail(String(err.message || '').indexOf('429') >= 0
            ? 'Juda ko‘p urinish. Bir daqiqadan keyin qayta urinib ko‘ring.'
            : 'Botga ulanib bo‘lmadi. Birozdan keyin urinib ko‘ring.');
        });
      };
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
      if (e.code === 4001) {
        // Token eskirgan: o'chirib, sahifani qayta ochamiz — kirish oynasi chiqadi
        try { localStorage.removeItem('mp_token'); } catch (err) {}
        MP.toast('Sessiya tugadi, qaytadan kiring');
        setTimeout(function () { location.reload(); }, 1200);
        return;
      }
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

  /* ------------------------------------- 5. E'lon va "faqat ko'rish" holati */
  function refreshSite() {
    fetch('/api/site', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (s) { MP.setSite(s); })
      .catch(function () {});
  }
  setInterval(refreshSite, 60000);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) refreshSite();
  });

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
      try { localStorage.setItem('mp_token', token); } catch (e) {}
      MP.fit();       // kirgandan keyin butun doska (yoki havoladagi joy) ko'rinadi
      connect();      // yangi snapshot'ni onopen o'zi yuklaydi
      if (MP.maybeShowIntro) MP.maybeShowIntro();

      // Yutuqlar (1, 10, 100-piksel...) serverdagi jami songa tayanadi
      fetch('/api/me', { headers: { Authorization: 'Bearer ' + token } })
        .then(function (r) { return r.ok ? r.json() : null; })
        .then(function (me) { if (me) MP.setPainted(me.pixels); })
        .catch(function () {});
    })
    .catch(function (err) {
      console.error(err);
      MP.toast('Serverga ulanib bo‘lmadi');
    });
})();
