/* Million Piksel — birinchi marta kirganlar uchun qisqa tushuntirish
 * va do'stlarni chaqirish oynasi.
 *
 * index.html dan mustaqil: faqat window.MP orqali gaplashadi.
 * Django uni connect.js bilan birga qo'shadi.
 */
(function () {
  'use strict';

  var MP = window.MP;
  if (!MP) return;

  var SEEN_KEY = 'mp_intro_seen_v1';

  /* ------------------------------------------------------------ uslub */
  var css = document.createElement('style');
  css.textContent =
    '.mp-sheet{position:fixed;inset:0;z-index:90;display:flex;' +
      'align-items:center;justify-content:center;padding:16px}' +
    '.mp-sheet::before{content:"";position:absolute;inset:0;' +
      'background:var(--bg);opacity:.75}' +
    '.mp-box{position:relative;z-index:1;background:var(--panel);' +
      'border:1px solid var(--line);border-radius:var(--rp);' +
      'padding:22px 20px;max-width:360px;width:100%;max-height:86vh;' +
      'overflow:auto}' +
    '.mp-box h3{margin:0 0 6px;font-size:17px}' +
    '.mp-box p{margin:0 0 14px;color:var(--muted);font-size:13px;' +
      'line-height:1.55}' +
    '.mp-box h4{margin:18px 0 8px;font-size:13px}' +
    '.mp-row{display:flex;gap:8px;margin-top:10px}' +
    '.mp-btn{flex:1;padding:10px 12px;border:1px solid var(--line);' +
      'border-radius:var(--r);background:transparent;color:inherit;' +
      'font:inherit;cursor:pointer}' +
    '.mp-btn.go{background:var(--accent);border-color:var(--accent);color:#fff}' +
    '.mp-in{width:100%;padding:9px 10px;border:1px solid var(--line);' +
      'border-radius:var(--r);background:transparent;color:inherit;' +
      'font:inherit;text-align:center}' +
    '.mp-steps{display:grid;gap:12px;margin:16px 0 4px}' +
    '.mp-step{display:flex;gap:11px;align-items:flex-start}' +
    '.mp-ico{flex:none;width:34px;height:34px;border-radius:9px;' +
      'background:var(--bg);border:1px solid var(--line);' +
      'display:flex;align-items:center;justify-content:center}' +
    '.mp-step b{display:block;font-size:13px;margin-bottom:2px}' +
    '.mp-step span{font-size:12px;color:var(--muted);line-height:1.5}' +
    '.mp-dots{display:flex;gap:5px;justify-content:center;margin-top:16px}' +
    '.mp-dots i{width:6px;height:6px;border-radius:50%;background:var(--line)}' +
    '.mp-dots i.on{background:var(--accent)}' +
    '.mp-stat{display:flex;justify-content:space-between;font-size:13px;' +
      'padding:7px 0;border-bottom:1px solid var(--line)}' +
    '.mp-stat b{font-variant-numeric:tabular-nums}' +
    '.mp-code{font-family:ui-monospace,Menlo,Consolas,monospace;' +
      'font-size:15px;letter-spacing:1px;font-weight:600}' +
    '.mp-top{font-size:12px;color:var(--muted);display:flex;' +
      'justify-content:space-between;padding:4px 0}' +
    '.mp-note{font-size:11px;color:var(--muted);margin-top:12px}';
  document.head.appendChild(css);

  function sheet(html) {
    var el = document.createElement('div');
    el.className = 'mp-sheet';
    el.innerHTML = '<div class="mp-box">' + html + '</div>';
    document.body.appendChild(el);
    el.addEventListener('click', function (e) {
      if (e.target === el) el.remove();          // tashqarisiga bosilsa yopiladi
    });
    return el;
  }

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c];
    });
  }

  /* ------------------------------------------------- 1. Tanishtiruv */
  var ICON_TAP =
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
    '<path d="M9 11V6a2 2 0 1 1 4 0v6"/>' +
    '<path d="M13 12V9a2 2 0 1 1 4 0v6a5 5 0 0 1-5 5h-1a5 5 0 0 1-5-5v-3"/>' +
    '</svg>';
  var ICON_ZOOM =
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linecap="round">' +
    '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5M8 11h6M11 8v6"/>' +
    '</svg>';
  var ICON_BOLT =
    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2" stroke-linejoin="round">' +
    '<path d="M13 2 4 14h7l-1 8 9-12h-7l1-8z"/></svg>';

  var STEPS = [
    {
      icon: ICON_TAP,
      title: 'Bosing — bo‘yaladi',
      text: 'Pastdan rang tanlang va doskaga bosing. Tasdiqlash shart emas, ' +
            'piksel darhol qo‘yiladi va uni hamma ko‘radi. ' +
            'Tez chizish uchun qalam tugmasini yoqing va barmog‘ingizni sudrang.'
    },
    {
      icon: ICON_ZOOM,
      title: 'Yaqinlashtiring va suring',
      text: 'Ikki barmoq bilan kattalashtiring, bir barmoq bilan suring. ' +
            'Kompyuterda — g‘ildirak, sichqonchani bosib surish, ' +
            'o‘ng tugma yoki Alt bilan bosib rangni olish.'
    },
    {
      icon: ICON_BOLT,
      title: 'Bo‘yoq o‘zi tiklanadi',
      text: 'Har piksel 1 bo‘yoq oladi. Cho‘tka atrofidagi halqa ' +
            'keyingi bo‘yoq qachon kelishini ko‘rsatadi. ' +
            'Do‘st chaqirsangiz zaxirangiz kattalashadi.'
    }
  ];

  function showIntro() {
    var el = sheet(
      '<h3>Xush kelibsiz!</h3>' +
      '<p>Bu — 1 000 000 pikselli umumiy doska. Hamma birga chizadi, ' +
      'har bir piksel joyida qoladi.</p>' +
      '<div class="mp-steps">' +
        STEPS.map(function (s) {
          return '<div class="mp-step">' +
            '<div class="mp-ico">' + s.icon + '</div>' +
            '<div><b>' + s.title + '</b><span>' + s.text + '</span></div>' +
          '</div>';
        }).join('') +
      '</div>' +
      '<div class="mp-row"><button class="mp-btn go" id="mp-introok">' +
        'Boshladik</button></div>'
    );
    document.getElementById('mp-introok').onclick = function () {
      try { localStorage.setItem(SEEN_KEY, '1'); } catch (e) {}
      el.remove();
    };
  }

  function maybeShowIntro() {
    var seen = false;
    try { seen = localStorage.getItem(SEEN_KEY) === '1'; } catch (e) {}
    if (!seen) setTimeout(showIntro, 400);   // doska ko'ringandan keyin
  }

  /* --------------------------------------------- 2. Do'stlar oynasi */
  function inviteUrl(code) {
    var tg = window.Telegram && window.Telegram.WebApp;
    if (tg && tg.initDataUnsafe && window.MP_CONFIG &&
        window.MP_CONFIG.bot_username) {
      return 'https://t.me/' + window.MP_CONFIG.bot_username +
             '/app?startapp=' + code;
    }
    return location.origin + '/?ref=' + code;
  }

  function showFriends() {
    var el = sheet('<h3>Do‘stlar</h3><p>Yuklanmoqda…</p>');
    var box = el.querySelector('.mp-box');

    fetch('/api/me', {
      headers: { Authorization: 'Bearer ' + (window.MP_TOKEN || '') }
    })
      .then(function (r) { return r.json(); })
      .then(function (me) { render(box, el, me); })
      .catch(function () {
        box.innerHTML = '<h3>Do‘stlar</h3><p>Ma‘lumot olinmadi.</p>';
      });
  }

  function render(box, el, me) {
    var url = inviteUrl(me.invite_code);
    var t = me.team;

    box.innerHTML =
      '<h3>Do‘stlarni chaqiring</h3>' +
      '<p>Chaqirgan har bir do‘stingiz uchun bo‘yoq zaxirangiz ' +
      '<b>+' + me.bonus_per_invite + '</b> piksel oshadi.</p>' +

      '<div class="mp-stat"><span>Chaqirilgan</span><b>' + me.invites + '</b></div>' +
      '<div class="mp-stat"><span>Bo‘yoq zaxirangiz</span><b>' + me.max_energy +
        ' piksel</b></div>' +
      '<div class="mp-stat"><span>Qo‘ygan piksellaringiz</span><b>' +
        me.pixels + '</b></div>' +

      '<h4>Havolangiz</h4>' +
      '<input class="mp-in" id="mp-url" readonly value="' + esc(url) + '">' +
      '<div class="mp-row">' +
        '<button class="mp-btn go" id="mp-copy">Nusxalash</button>' +
        '<button class="mp-btn" id="mp-share">Ulashish</button>' +
      '</div>' +

      '<h4>Jamoa</h4>' +
      (t
        ? '<div class="mp-stat"><span>' + esc(t.name) + '</span><b>' +
            t.members + ' a‘zo</b></div>' +
          '<div class="mp-stat"><span>Jamoa kodi</span>' +
            '<b class="mp-code">' + esc(t.code) + '</b></div>' +
          '<div class="mp-stat"><span>Jamoa piksellari</span><b>' +
            t.pixels + '</b></div>' +
          '<div class="mp-row"><button class="mp-btn" id="mp-leave">' +
            'Jamoadan chiqish</button></div>'
        : '<p>Do‘stlaringiz bilan bitta jamoada chizing. Jamoa oching ' +
          'yoki kod bilan qo‘shiling.</p>' +
          '<input class="mp-in" id="mp-tname" placeholder="Jamoa nomi" ' +
            'maxlength="32">' +
          '<div class="mp-row"><button class="mp-btn go" id="mp-create">' +
            'Jamoa ochish</button></div>' +
          '<input class="mp-in" id="mp-tcode" placeholder="Kod bilan qo‘shilish" ' +
            'maxlength="12" style="margin-top:10px">' +
          '<div class="mp-row"><button class="mp-btn" id="mp-join">' +
            'Qo‘shilish</button></div>') +

      '<h4>Eng faol jamoalar</h4><div id="mp-topteams" class="mp-note">' +
        'yuklanmoqda…</div>' +

      '<div class="mp-row" style="margin-top:16px">' +
        '<button class="mp-btn" id="mp-close">Yopish</button></div>';

    document.getElementById('mp-close').onclick = function () { el.remove(); };

    document.getElementById('mp-copy').onclick = function () {
      var inp = document.getElementById('mp-url');
      inp.select();
      var done = false;
      if (navigator.clipboard) {
        navigator.clipboard.writeText(inp.value).then(function () {
          MP.toast('Havola nusxalandi');
        }, fallback);
        done = true;
      }
      if (!done) fallback();
      function fallback() {
        try { document.execCommand('copy'); MP.toast('Havola nusxalandi'); }
        catch (e) { MP.toast('Havolani qo‘lda nusxalang'); }
      }
    };

    document.getElementById('mp-share').onclick = function () {
      var text = 'Million Piksel — birga rasm chizamiz!';
      var u = document.getElementById('mp-url').value;
      var tg = window.Telegram && window.Telegram.WebApp;
      if (tg && tg.openTelegramLink) {
        tg.openTelegramLink('https://t.me/share/url?url=' +
          encodeURIComponent(u) + '&text=' + encodeURIComponent(text));
      } else if (navigator.share) {
        navigator.share({ title: 'Million Piksel', text: text, url: u })
          .catch(function () {});
      } else {
        window.open('https://t.me/share/url?url=' + encodeURIComponent(u) +
          '&text=' + encodeURIComponent(text), '_blank', 'noopener');
      }
    };

    bindTeam(el);
    loadTopTeams();
  }

  function api(path, body) {
    return fetch(path, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer ' + (window.MP_TOKEN || '')
      },
      body: JSON.stringify(body || {})
    }).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.detail || j.message || 'Xato');
        return j;
      });
    });
  }

  function bindTeam(el) {
    var create = document.getElementById('mp-create');
    var join = document.getElementById('mp-join');
    var leave = document.getElementById('mp-leave');

    if (create) create.onclick = function () {
      api('/api/team/create', { name: document.getElementById('mp-tname').value })
        .then(function (r) { MP.toast('Jamoa ochildi: ' + r.name); el.remove(); })
        .catch(function (e) { MP.toast(e.message); });
    };
    if (join) join.onclick = function () {
      api('/api/team/join', { code: document.getElementById('mp-tcode').value })
        .then(function (r) { MP.toast('Qo‘shildingiz: ' + r.name); el.remove(); })
        .catch(function (e) { MP.toast(e.message); });
    };
    if (leave) leave.onclick = function () {
      api('/api/team/leave')
        .then(function () { MP.toast('Jamoadan chiqdingiz'); el.remove(); })
        .catch(function (e) { MP.toast(e.message); });
    };
  }

  function loadTopTeams() {
    fetch('/api/team/top')
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var box = document.getElementById('mp-topteams');
        if (!box) return;
        if (!d.items.length) { box.textContent = 'Hali jamoa yo‘q'; return; }
        box.innerHTML = d.items.slice(0, 5).map(function (t, i) {
          return '<div class="mp-top"><span>' + (i + 1) + '. ' +
            esc(t.name) + '</span><span>' + t.pixels + ' px</span></div>';
        }).join('');
      })
      .catch(function () {});
  }

  /* ------------------------------------------------------------ ulash */
  MP.onFriends = showFriends;
  MP.showIntro = showIntro;
  MP.maybeShowIntro = maybeShowIntro;
})();
