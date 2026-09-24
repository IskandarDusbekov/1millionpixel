# Million Piksel

1000×1000 (1 000 000 piksel) umumiy kanvas — r/place uslubida.
Django + Channels + Redis + PostgreSQL, frontend esa bitta HTML fayl
(framework va tashqi kutubxonasiz).

---

## Papkalar tuzilishi

```
1millionpixel/
├── frontend/
│   ├── index.html          # BUTUN frontend: HTML + CSS + JS (kanvas, effektlar, zoom)
│   ├── connect.js          # backend ulagichi (kirish, bot orqali kirish, WebSocket, snapshot)
│   └── onboarding.js       # tanishtiruv va do'stlar oynasi
├── backend/
│   ├── manage.py
│   ├── requirements.txt
│   ├── config/
│   │   ├── settings.py     # sozlamalar (kanvas, energiya, broadcast)
│   │   ├── urls.py         # HTTP marshrutlar + frontend'ni uzatish
│   │   └── asgi.py         # ASGI + broadcaster'ni ishga tushirish
│   ├── canvas/
│   │   ├── cooldown.py     # DINAMIK KULLAUT — yagona manba
│   │   ├── redis_store.py  # kanvas 1 MB satr + atomar Lua skript
│   │   ├── broadcaster.py  # BATCHING + pub/sub fan-out (high-load yuragi)
│   │   ├── consumers.py    # WebSocket (issiq yo'l, DB'ga tegmaydi)
│   │   ├── auth.py         # Telegram initData tekshiruvi / JWT
│   │   ├── api.py          # HTTP API (Django Ninja) + bot orqali kirish + /api/canvas.bin
│   │   ├── models.py       # Player, PixelEvent, Report, SiteSettings, UploadedFile, ...
│   │   ├── site.py         # SEO <head>, robots.txt, sitemap.xml, manifest, ikonkalar
│   │   ├── moderation.py   # rollback va ban
│   │   ├── admin.py        # Django admin ro'yxatlari
│   │   ├── adminpanel.py   # ADMIN PANEL (faqat superuser + CSRF)
│   │   └── management/commands/
│   │       ├── drain_history.py   # Redis Stream -> PostgreSQL
│   │       └── telegram_bot.py    # Mini App tugmasi, /start, brauzer kirishini tasdiqlash
│   └── templates/admin/
│       ├── login.html      # panel kirish sahifasi (login + parol)
│       ├── panel.html      # boshqaruv paneli (sidebar, SEO, fayllar, kanvas, ...)
│       ├── rollback.html   # oddiy rollback formasi
│       └── mp_index.html   # Django admin bosh sahifasi
├── deploy/
│   ├── nginx.conf          # gzip, WebSocket, rate limit, fayl yuklash
│   └── nginx-host.conf     # serverda nginx allaqachon bo'lsa
├── Dockerfile
├── docker-compose.yml       # redis + postgres + web + history
├── docker-compose.prod.yml  # + nginx + certbot + bot
└── .env.example
```

---

## Arxitektura — nega aynan shunday

```
                brauzer (index.html + connect.js)
                    │  WebSocket, binar paketlar
                    ▼
        ┌───────────────────────────┐
        │  uvicorn worker (N ta)    │
        │  consumers.py             │
        └─────────┬─────────────────┘
                  │ Lua: energiya + SETRANGE + RPUSH   (1 ta qadam, atomar)
                  ▼
        ┌───────────────────────────┐
        │  Redis                    │
        │  mp:canvas   1 MB satr    │ ← joriy holat
        │  mp:buf      bufer        │ ← broadcast uchun
        │  mp:hist     Stream       │ ← tarix
        │  mp:online   ZSET         │ ← onlayn hisob
        └─────┬─────────────┬───────┘
              │             │
   80 ms batch│             │ drain_history worker
              ▼             ▼
        pub/sub fan-out   PostgreSQL (PixelEvent, Player, Report)
              │
              ▼  har bir worker o'z socketlariga tarqatadi
           brauzerlar
```

### Uchta qaror — loyihaning butun yuki shularga bog'liq

**1. Joriy holat Redis'da, PostgreSQL faqat tarix uchun.**
Har bo'yash — bitta `SETRANGE`, ya'ni O(1). PostgreSQL sekundiga minglab
`UPDATE` ni ko'tarmaydi. Yangi foydalanuvchi butun doskani bitta so'rovda
oladi: `GET /api/canvas.bin` → 1 000 000 bayt (gzip'dan keyin ~30–80 KB).

**2. Energiya tekshiruvi — Lua skriptda, atomar.**
`redis_store.py` dagi `PLACE_LUA` energiyani hisoblaydi, kamaytiradi va
pikselni bitta qadamda yozadi. Python tomonda tekshirilsa, bitta odam ikkita
ulanishdan bir vaqtda so'rov yuborib limitni chetlab o'tadi.

**3. Broadcast — batching + protsess darajasida fan-out.**
1000 kishi × 20 piksel/sek × 1000 socket = 20 000 000 send/sek. Ko'tarilmaydi.
Buning o'rniga piksellar `mp:buf` ga yig'iladi, har 80 ms da bitta binar
paketga qadoqlanadi va pub/sub orqali har bir workerga BIR marta yuboriladi.
Worker esa o'zidagi socketlarga tarqatadi. 20 000 xabar → sekundiga 12.5 ta.

> Channels'ning `group_send` i shu yerda ishlatilmadi: u har bir socket uchun
> alohida Redis yozuvi qiladi, ya'ni yuqoridagi muammoni hal qilmaydi.

---

## Dinamik kullaut

`backend/canvas/cooldown.py` — yagona manba (frontend ham server bergan
qiymatdan foydalanadi, o'zi qayta hisoblamaydi):

| Onlayn | 1 piksel tiklanish vaqti |
|---|---|
| 0–10 | 1 soniya |
| 10–100 | 10 soniya |
| 100–1000 | 30 soniya |
| 1000+ | 60 soniya |

Maksimal zaxira — 50 piksel. Energiya hech qayerda "saqlanmaydi": u
`(oxirgi_bo'yash_vaqti, o'sha_paytdagi_energiya)` juftligidan hisoblanadi.
Shuning uchun offlayn foydalanuvchi uchun hech qanday fon vazifasi kerak emas.

Onlayn soni — `mp:online` ZSET'idagi yozuvlar (45 soniyada eskiradi).
Har 2 soniyada bitta worker (Redis `SET NX` qulfi bilan) qayta sanaydi va
natijani pub/sub orqali hammaga tarqatadi.

> **Yozuvni server yangilaydi, mijoz emas** (`broadcaster.refresh_presence`).
> Mijoz ping'iga ishonib bo'lmaydi: brauzer fon tabdagi `setInterval` ni
> 60+ soniyaga sekinlashtiradi (Telegram Mini App va mobil brauzerlarda
> ayniqsa qattiq). 20 soniyalik ping kechikadi, 45 soniyalik TTL tugaydi va
> FAOL foydalanuvchi "offline" bo'lib qoladi — natijada onlayn soni tushib,
> kullaut noto'g'ri darajaga o'tadi. Server esa socket ochiqligini aniq
> biladi va har 2 soniyada bitta `ZADD` bilan hammasini yangilaydi
> (1000 ta mijoz uchun ham bitta so'rov). WebSocket'dagi `0x02` ping endi
> faqat keepalive — proksi jim turgan ulanishni uzmasligi uchun.

---

## WebSocket protokoli (binar, JSON emas)

**Mijoz → server**

| Bayt 0 | Mazmuni |
|---|---|
| `0x01` | `uint16 x` `uint16 y` `uint8 color` — bo'yash |
| `0x02` | keepalive (20 soniyada bir; onlayn hisobiga ta'sir qilmaydi) |
| `0x03` | `uint16 x` `uint16 y` — shikoyat |

**Server → mijoz**

| Bayt 0 | Mazmuni |
|---|---|
| `0x01` | `(x, y, c)` × n — piksel paketi (80 ms batch) |
| `0x02` | `uint32 online` `uint32 cooldown_ms` |
| `0x03` | `uint16 energy` `uint16 max` `uint32 next_ms` — shaxsiy |
| `0x04` | utf-8 matn — toast |
| `0x05` | snapshot'ni qayta yukla (katta rollback'dan keyin) |
| `0x06` | `uint16 x` `uint16 y` — bo'yash rad etildi, qaytar |

Binar format JSON'dan ~10 barobar kichik: bitta piksel 5 bayt
(`{"x":512,"y":397,"c":6}` — 24 bayt).

---

## Frontend

`frontend/index.html` — bitta fayl, tashqi kutubxonasiz. Django uni uzatayotganda
`<head>` ga admin paneldagi SEO ma'lumotlarini (`<!--MP:SEO-->`), sozlamalarni
(`<!--MP:CONFIG-->`) va robotlar uchun matnni (`<!--MP:SEOBODY-->`) yozadi,
so'ng `onboarding.js` va `connect.js` ni qo'shadi. Demo rejim yo'q: fayl
backendsiz ishlamaydi.

**Nega Pixi.js kerak emas:** doska `Uint8Array(1 000 000)` da (1 MB) va
1000×1000 offscreen canvas'da turadi. Ekranga chizish — har kadrga **bitta**
`drawImage`, ya'ni GPU'da masshtablanadigan bitta tekstura. Pixi ham aynan
shuni qiladi. Piksel bo'yash — offscreen'ga bitta `fillRect(x, y, 1, 1)`, O(1).
Qotiradigan yondashuv — har kadrda 1 000 000 `fillRect` yoki har piksel uchun
DOM elementi; bu yerda ikkalasi ham yo'q.

Boshqaruv:

- **Bosish = darhol bo'yash** (tasdiqlash bosqichi yo'q). Doskada allaqachon
  shu rang bo'lsa, bo'yoq sarflanmaydi.
- **Tez chizish.** Pastdagi qalam tugmasi (yoki `D`) — «chizish rejimi»:
  barmoqni/sichqonchani sudrab chizasiz, oraliq kataklar Bresenham chizig'i
  bilan to'ldiriladi va `STROKE_MS` (38 ms) oralig'ida navbat bilan
  bo'yaladi (server chegarasiga urilmaslik uchun). Chizish rejimida ikki
  barmoq — surish/zoom. Kompyuterda `Shift` + sudrash ham chizadi.
- **Rang olish** (pipetka): tugma, o'ng tugma yoki `Alt` + bosish. `[` / `]` —
  oldingi/keyingi rang.
- Juda uzoqdan (`ZOOM_ASSIST`) bosish bo'yamaydi, o'sha joyga yaqinlashtiradi —
  xato piksel bo'yoqni yemasin.
- 6 pikseldan ko'p surilsa — bo'yash emas, surish (`DRAG_SLOP`); qo'yib
  yuborilganda inersiya bilan sirpanadi.
- **Silliq zoom.** G'ildirak maqsad masshtabga eksponensial yaqinlashadi
  (kursor ostidagi katak joyida qoladi), tugmalar va «butun doska» — 200–380 ms
  animatsiya (masshtab logarifmik, markaz chiziqli), trekpad chimchilashi
  (`ctrl+wheel`) va pinch qo'llanadi. Max zoom — 1 piksel ≈ 64 px.
- `clampScale()` va `clampPan()` **alohida** funksiyalar. Aralashtirilsa, zoom
  paytida rasm siljib ketadi.
- Klaviatura: strelkalar — surish, `+`/`−` — zoom, `0` — butun doska,
  `D` — chizish rejimi, `M` — ovoz.
- Yorug'/qorong'i rejim `prefers-color-scheme` orqali, `prefers-reduced-motion`
  hurmat qilinadi (zarralar va animatsiyalar o'chadi), barcha tugmalarda
  `aria-label` va `:focus-visible`.
- Havola: `/#x,y,zoom` — ochilganda o'sha joyga uchadi. «Ulashish» tugmasi
  shu havolani yasaydi; «Saqlash» tugmasi doskani PNG qiladi.

### Bo'yash effektlari va psixologik dizayn

Har bir harakat darhol **javob** qaytaradi — bu foydalanuvchini "qayta
bo'yash"ga undaydi (neyro-lingvistik/xulq-atvor dizayni tamoyillari):

- **Pop + to'lqin + zarralar.** Bo'yalgan katak "sakrab" o'sadi, atrofida
  halqa tarqaladi, rang zarralari sochiladi (`#fx` alohida qatlam, doska
  koordinatalarida — surilsa ham joyida qoladi). Boshqalarning piksellari
  sezilar-sezilmas nur bilan ko'rinadi.
- **Cho'tka.** Pastda tanlangan rangli doira; uning atrofidagi halqa
  KEYINGI bo'yoq qachon kelishini ko'rsatadi. Yangi bo'yoq kelganda cho'tka
  sakraydi, «+1» uchadi, mayin ovoz chalinadi.
- **Ovoz va titrash.** Pentatonik ovozlar (qanday bosilsa ham uyg'un),
  Telegram `HapticFeedback` / `navigator.vibrate`. `M` yoki dinamik
  tugmasi bilan o'chiriladi.
- **Yutuqlar.** 1, 10, 25, 50, 100, … piksel — iliq tabrik, katta zarralar.
  Hisob serverdagi jami songa (`/api/me`) tayanadi.
- **Til.** «Energiya» o'rniga «bo'yoq»; taqiq emas, kutish: «Bo'yoq tiklanmoqda —
  12 soniyadan keyin yana chizasiz».

Sozlash uchun fayl boshidagi konstantalar: `N`, `MAX_SCALE`, `DRAG_SLOP`,
`GRID_FROM`, `ZOOM_ASSIST`, `STROKE_MS`.

### Kirish ekrani (ro'yxatdan o'tmaganlar uchun)

Birinchi marta kelgan foydalanuvchi darhol doskani ko'radi, lekin chiza
olmaydi — oldida kirish kartochkasi turadi:

- **Orqa fon — haqiqiy doska.** Snapshot avtorizatsiyadan OLDIN yuklanadi
  (`/api/canvas.bin` ochiq endpoint), shuning uchun orqada boshqa
  foydalanuvchilar chizgan rasmlar ko'rinib turadi. Fon `MP.cover()` bilan
  butun ekranni qoplaydi va ustiga blur + shaffof qatlam tushadi.
- **O'rtada piksel-art noutbuk** — ekranida palitradagi 24 rang qiya tasma
  bo'lib o'tadi. Bu **bezak**: `connect.js` dagi alohida `<canvas>` ga
  chiziladi, umumiy doskaga yozilmaydi.
- **Kirgandan keyin** kartochka olib tashlanadi va ko'rinish `MP.fit()` bilan
  butun doskaga qaytadi.

Ko'rinish rejimi (`viewMode`: `fit` / `cover` / `free`) eslab qolinadi va
`resize()` uni qayta qo'llaydi. Bu shart: sahifa yuklanganda yoki telefon
burilganda layout keyinroq o'zgaradi, aks holda tanlangan holat birinchi
`resize` da yo'qolib ketadi.

---

## Timelapse — tezlashtirilgan video

Doskaning suratlari muntazam saqlanadi, ketma-ket kadrlardan video yig'iladi.

**Oraliq faollikka qarab o'zgaradi.** Agar har 30 soniyada surat olsak,
kechasi hech kim chizmaganda minglab bir xil kadr yig'iladi; har 30 daqiqada
olsak, eng qizg'in paytdagi harakat yo'qoladi. Shuning uchun:

| Oxirgi suratdan beri qo'yilgan piksel | Keyingi surat |
|---|---|
| 0 | surat olinmaydi (kadr takrorlanmaydi) |
| ~200 | ~27 daqiqa |
| ~1000 | ~15 daqiqa |
| 2000+ | 30 soniya |

Bundan tashqari doska **1, 5, 10, 25, 50, 75, 90, 95, 99, 100%** to'lganda
alohida surat olinadi (`reason=milestone`, 100% da `full`).

Fayl — palitrali PNG (`media/timelapse/YYYYMMDD-HHMMSS-sabab.png`).
24 ta rang bo'lgani uchun hajmi kichik: bo'sh doska ~2 KB, to'lgan ~35 KB.
Ya'ni kuniga 100 ta surat ham 3-4 MB dan oshmaydi.

Ishga tushirish (Docker'da `timelapse` xizmati o'zi qiladi):

```bash
python manage.py timelapse_worker          # doimiy
python manage.py timelapse_worker --once   # bitta surat
```

Video yig'ish:

```bash
ffmpeg -framerate 24 -pattern_type glob -i 'media/timelapse/*.png' \
  -c:v libx264 -pix_fmt yuv420p -vf scale=1000:1000:flags=neighbor timelapse.mp4
```

> `scale=...:flags=neighbor` muhim — busiz ffmpeg piksellarni silliqlab
> yuboradi va rasm xira chiqadi.

Admin panelning **Suratlar** bo'limida: surat olishni to'xtatish/yoqish,
hoziroq surat olish, barcha kadrlar ro'yxati va shu ffmpeg buyrug'i.

Sozlash (`.env`): `TIMELAPSE_MIN_SEC`, `TIMELAPSE_MAX_SEC`,
`TIMELAPSE_BUSY_PIXELS`, `TIMELAPSE_KEEP_DAYS` (0 = cheksiz saqlash).

> Lokal sinovda `FAKE_REDIS=1` bo'lsa, worker alohida protsessda ishlagani
> uchun web-server bilan bitta doskani bo'lishmaydi va suratlar bo'sh
> chiqadi. Bunday holatda admin paneldagi «Hozir surat ol» tugmasidan
> foydalaning — u server protsessining o'zida bajariladi. Haqiqiy Redis
> bilan bu muammo yo'q.

---

## Do'st chaqirish va jamoalar

**Chaqiruv.** Har bir foydalanuvchida qisqa kod bor. Havola:
`https://sayt.uz/?ref=KOD`, Telegram'da esa
`https://t.me/<bot>/app?startapp=KOD` (server uni `initData` ichidagi
`start_param` dan o'zi oladi).

Chaqirilgan do'st **haqiqatan chiza boshlagach** (`INVITE_MIN_PIXELS`,
sukut bo'yicha 20 piksel) chaqiruvchining zaxirasi `INVITE_BONUS_ENERGY`
ga oshadi (sukut bo'yicha +5, jami chegara +50).

> **Nega IP bo'yicha tekshirmaymiz.** Birinchi yondashuv "bir xil IP dan
> kelgan chaqiruv hisoblanmasin" edi. O'zbekistonda bu ishlamaydi: mobil
> operatorlar CGNAT ishlatadi, ya'ni bitta IP ostida minglab abonent
> turadi — bir-birini tanimaydigan haqiqiy foydalanuvchilar ham bloklanib
> qolardi, bir uydagi ikki kishi ham. Shuning uchun himoya boshqacha:
> hisob ochish uchun Telegram majburiy, bonus esa faqat haqiqiy
> chizishdan keyin beriladi. Soxta hisob ochib har biriga 20 piksel chizib
> chiqish +5 energiya uchun arzimaydi.

Piksel soni Redis'dan o'qiladi (`mp:e:<uid>` hash'idagi `n`), shuning uchun
bonus `drain_history` worker ishlashiga bog'liq emas. Bonus berilgach,
WebSocket orqali ~20 soniyada yetib boradi — qayta ulanish shart emas.

**Jamoalar.** Do'stlar bitta jamoada chizadi. Jamoa ochiladi (nom beriladi),
qolganlar kod bilan qo'shiladi. Chaqirilgan do'st avtomatik chaqiruvchining
jamoasiga tushadi. Jamoalar reytingi — `/api/team/top` va admin panelda.

Foydalanuvchi buni saytdagi **«Do'stlar»** tugmasi orqali ko'radi: havola,
nusxalash, Telegram'ga ulashish, jamoa ochish/qo'shilish, top jamoalar.

---

## Birinchi marta kirganlar uchun

Ro'yxatdan o'tmagan foydalanuvchi kirish ekranini ko'radi (orqada haqiqiy
doska, o'rtada piksel-art noutbuk). Kirgandan keyin **bir marta** qisqa
tushuntirish chiqadi — uch qadam: bosish = bo'yash, zoom/surish, energiya
va do'st chaqirish. `localStorage` da belgilanadi, ikkinchi marta
ko'rsatilmaydi (`mp_intro_seen_v1`).

Kod: [frontend/onboarding.js](frontend/onboarding.js). Qayta ko'rsatish
uchun brauzer konsolida: `MP.showIntro()`.

---

## Moderatsiya paneli

**`/admin/panel/`** — yagona boshqaruv paneli. **Faqat superuser, login va parol
bilan** (`/admin/panel/login/`; oddiy `is_staff` hisoblar kira olmaydi). Chapda
sidebar, o'ngda bo'limlar:

| Bo'lim | Nima qiladi |
|---|---|
| **Bosh sahifa** | onlayn, kullaut, to'lish, piksel/soat, foydalanuvchilar; 24 soatlik va 14 kunlik grafiklar |
| **Kanvas** | jonli doska; hudud tanlab rollback; katakni bossangiz — kim, qachon, qaysi rangni qo'ygan |
| **Shikoyatlar** | koordinatani bosish kanvasni o'sha joyga olib boradi; «100×100 · 15 daq» — bitta bosishda qaytarish |
| **Foydalanuvchilar** | ism/ID bo'yicha qidirish, ban (24 soat / muddatsiz, IP bilan) va bekor qilish |
| **Jamoalar** | reyting, a'zolar, kod, kapitan |
| **SEO** | title, description, kalit so'zlar, robotlar uchun matn, asosiy manzil, OG rasm, favicon, brauzer rangi, X akkaunti, Google/Yandex tasdiqlash kodi, indekslashni o'chirish; Google va ulashish kartasi **jonli ko'rinishi** va SEO tekshiruv ro'yxati |
| **Fayllar** | sudrab tashlab yuklash (jarayon ko'rsatkichi bilan), ro'yxat, URL nusxalash, «OG» / «Favicon» qilib tayinlash, o'chirish |
| **Sayt holati** | e'lon (saytning yuqorisida hammaga ko'rinadi) va «faqat ko'rish» rejimi (hech kim chiza olmaydi) |
| **Suratlar** | timelapse kadrlari, to'xtatish/yoqish, hoziroq surat olish |
| **Jurnal** | har bir admin amali (`ModerationLog`) — kim nima qilgani, kirishlar ham |
| **Hisob** | parolni almashtirish (eski parol so'raladi, kamida 10 belgi) |

`/admin/` (Django admin, model ro'yxatlari) ham faqat superuser uchun va kirish
sahifasi shu yagona login. Superuser yaratish: `python manage.py createsuperuser`.

**Kirish himoyasi:** CSRF, IP bo'yicha 8 ta xatodan keyin 15 daqiqa blok
(Redis), sessiya 12 soat, xato sababi aytilmaydi ("login yoki parol noto'g'ri"),
`next` faqat shu sayt ichiga.

### SEO qanday ishlaydi

Sozlamalar `SiteSettings` (bitta qator) da turadi. Bosh sahifa har so'rovda
`<head>` ga server tomonda yoziladi (`canvas/site.py`) — qidiruv robotlari
JavaScriptsiz ham ko'radi: `<title>`, description, canonical, robots,
Open Graph, Twitter Card, JSON-LD (`WebApplication`), theme-color, PWA
manifest. Avtomatik yaratiladi: `/robots.txt`, `/sitemap.xml`,
`/manifest.webmanifest`, `/icon-{32,180,192,512}.png`. Sozlamalar workerlarda
10 soniyagacha keshlanadi.

Yuklanadigan fayllar: png, jpg, gif, webp, ico, pdf, txt; 5 MB gacha; rasmning
mazmuni Pillow bilan tekshiriladi (kengaytma yolg'on bo'lsa rad); **SVG rad
etiladi** (ichida skript bo'lishi mumkin); nomi tasodifiy (`/media/uploads/…`).
Nginx `/admin/panel/api/files/upload` uchun 6 MB ga ruxsat berishi kerak
(`deploy/nginx*.conf` da bor).

### Faqat ko'rish rejimi va e'lon

Panelda yoqilganda bayroq Redis'ga (`mp:readonly`) yoziladi. Har worker uni
`online_loop` da 2 soniyada bir o'qiydi — ya'ni bo'yash yo'lida qo'shimcha
Redis so'rovi yo'q. Rad etilgan piksel mijozda avtomatik qaytariladi.

### Rollback qanday ishlaydi

Hududdagi har bir katak uchun T vaqtidan oldingi oxirgi hodisa topiladi —
katak bo'yicha tsikl emas, bitta SQL so'rov:

- PostgreSQL — `DISTINCT ON (x, y)`, `px_xy_time_idx` indeksi bilan;
- SQLite (lokal sinov) — `ROW_NUMBER() OVER (PARTITION BY x, y)`,
  chunki SQLite `DISTINCT ON` ni qo'llamaydi.

Natija Redis'ga yoziladi, **tarixga ham qo'shiladi** (aks holda keyingi
rollback vandalizmni qaytarib qo'yardi) va mijozlarga darhol yuboriladi.
20 000 pikseldan katta hududda alohida paket o'rniga «snapshot'ni qayta
yukla» buyrug'i ketadi.

### Ban

Avval Redis'ga (`mp:ban:u:<id>`, `mp:ban:ip:<ip>`), keyin PostgreSQL'ga.
Shu tartib muhim: ochiq WebSocket ulanishlar keyingi piksel urinishida
darhol rad javobini oladi, bazaga bog'lanmasdan.

### Nega panel API django-ninja'da emas

`django-ninja` POST'larni `csrf_exempt` qiladi. Sessiya bilan ishlaydigan
admin endpointi shu holatda CSRF hujumiga ochiq bo'lardi — admin boshqa
saytdagi formani bosishi bilan hudud o'chib ketishi mumkin edi. Shuning
uchun panel endpointlari oddiy Django view'lar: `superuser_required` +
`CsrfViewMiddleware`, panel esa `X-CSRFToken` sarlavhasini yuboradi.

---

## Lokal ishga tushirish

### Eng oson yo'l (Windows)

Loyiha ildizidagi **`run.bat`** ni ikki marta bosing. U o'zi kerakli papkaga
o'tadi, virtual muhit yaratadi, kutubxonalarni o'rnatadi va serverni
ishga tushiradi.

> `manage.py` **`backend/` ichida**, loyiha ildizida emas. Shuning uchun
> ildizdan `py manage.py runserver` ishlamaydi — avval `cd backend` qiling.

### Windows — Docker'siz, Redis'siz, Postgres'siz

`.env` da `USE_SQLITE=1` va `FAKE_REDIS=1` bo'lsa, hammasi bitta protsessda
ishlaydi (fakeredis Lua skriptni ham qo'llaydi):

```bash
cd backend
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe manage.py migrate
.venv\Scripts\python.exe manage.py createsuperuser
.venv\Scripts\python.exe -m uvicorn config.asgi:application --port 8000
```

> Bu rejim faqat sinov uchun: bitta worker, ma'lumot saqlanmaydi.
> Ishlab chiqarishda ikkala flagni ham `0` qiling.

Frontend backendsiz ishlamaydi (demo rejim olib tashlangan).

### Docker

```bash
cp .env.example .env
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

Sayt `http://localhost:8000` · Panel `http://localhost:8000/admin/panel/`

---

## Avtorizatsiya

**Saytga faqat Telegram hisobi bilan kiriladi** — ikki yo'l bilan. Google OAuth
ham, parolsiz "dasturchi kirishi" ham ataylab olib tashlangan: har bir piksel
haqiqiy Telegram hisobiga bog'lanadi va soxta hisob ochish qimmatga tushadi.
**Admin panelga esa faqat superuser login va paroli bilan** kiriladi (yuqoridagi
«Moderatsiya paneli»).

1. **Mini App ichida** — bir bosishsiz, `initData` imzosi tekshiriladi.
2. **Oddiy brauzerda — bot orqali.** Kirish oynasidagi «Telegram bot orqali
   kirish» tugmasi:

```
brauzer ──POST /api/auth/bot/start──▶ server: kod (16 hex, 5 daqiqa) + havola
brauzer ──t.me/<bot>?start=login_<kod>──▶ Telegram (bot ochiladi)
bot: «Qurilma: Chrome · Windows, IP: … Tasdiqlaysizmi?»  [Tasdiqlayman]
bot ──Redis: kod → {telegram_id, ism}──▶
brauzer ──GET /api/auth/bot/poll (har 2 s)──▶ server: kod bir marta beriladi → JWT
```

   **Nega tasdiqlash tugmasi bor?** Kodni boshqa odam yaratib, sizga havola
   sifatida yuborishi mumkin: «Start» bosgan zahoti u sizning hisobingizga kirib
   olardi. Shuning uchun bot so'rov qayerdan kelganini (IP, qurilma) ko'rsatadi
   va faqat ochiq tasdiq bilan kiritadi. Yana: kod bir martalik va 5 daqiqalik,
   `start` IP bo'yicha daqiqada 10 ta bilan cheklangan, bot yo'q kodni
   tasdiqlay olmaydi.

> **Bot jarayoni doim ishlab turishi shart** (`telegram_bot` buyrug'i,
> `docker compose … up -d bot`) — brauzerdan kirish shunga tayanadi. Bot
> to'xtasa, Mini App orqali kirish ishlayveradi, brauzerdan esa kirib bo'lmaydi.
>
> `TELEGRAM_BOT_TOKEN` sozlanmagan bo'lsa saytga hech kim kira olmaydi
> (server ishga tushganda log'ga ogohlantirish yozadi). Admin panel
> superuser hisobi bilan ishlayveradi.

### Telegram Mini App

1. `@BotFather` → `/newbot` → tokenni `.env` dagi `TELEGRAM_BOT_TOKEN` ga yozing.
2. `/newapp` (yoki `/mybots` → Bot Settings → Menu Button) → Mini App URL:
   `https://sizning-domen.uz`
3. Menyu tugmasini va `/start` javobini sozlash:

```bash
python manage.py telegram_bot --url https://sizning-domen.uz
```

`--setup-only` bersangiz, faqat menyu tugmasini qo'yib chiqadi (bot doim
ishlab turishi shart emas).

Ishlashi: `connect.js` `window.Telegram.WebApp.initData` ni
`POST /api/auth/telegram` ga yuboradi → server HMAC-SHA256 imzoni tekshiradi
(`auth.py: verify_telegram`, 24 soatdan eski initData rad etiladi) → JWT.
Telegram ichida foydalanuvchidan hech narsa so'ralmaydi.

JWT `localStorage` da saqlanadi va WebSocket ulanishida query parametr
sifatida uzatiladi.

### Sinovda qanday kirish

Google va dasturchi kirishi olib tashlangani uchun sinovlar ham haqiqiy
yo'ldan yuradi: `initData` sinov bot tokeni bilan imzolanadi va
`/api/auth/telegram` ga yuboriladi, bot orqali kirish esa
`store.botlogin_confirm_sync()` bilan (bot jarayonining o'rniga) tekshiriladi.
Ya'ni ishlab chiqarishdagi oqimning aynan o'zi tekshiriladi.

`/api/auth/google` va `/api/auth/dev` endi **mavjud emas** (404).

---

## Deploy

> **Serverda allaqachon nginx va boshqa saytlar bo'lsa** — to'liq ketma-ket
> buyruqlar alohida faylda: **[deploy/SERVER.md](deploy/SERVER.md)**.
> Quyidagisi — nginx yo'q, bo'sh serverdagi umumiy yo'riqnoma.

### 1. Server tayyorlash

Ubuntu 22.04+, 2 vCPU / 4 GB — 1000 ga yaqin bir vaqtli foydalanuvchiga yetadi.

```bash
curl -fsSL https://get.docker.com | sh
git clone <repo> millionpixel && cd millionpixel
cp .env.example .env
```

`.env` da albatta o'zgartiring:

| O'zgaruvchi | Qiymat |
|---|---|
| `SECRET_KEY`, `JWT_SECRET` | tasodifiy uzun satr (`openssl rand -hex 32`) |
| `DEBUG` | `0` |
| `USE_SQLITE`, `FAKE_REDIS` | `0` (yoki umuman olib tashlang) |
| `ALLOWED_HOSTS` | `sizning-domen.uz` |
| `CSRF_TRUSTED_ORIGINS` | `https://sizning-domen.uz` |
| `POSTGRES_PASSWORD` | kuchli parol |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` | yuqoridagi bo'limga qarang |
| `PUBLIC_URL` | `https://sizning-domen.uz` (bot uchun) |

### 2. TLS sertifikati

`deploy/nginx.conf` dagi `millionpixel.uz` ni o'z domeningizga almashtiring,
so'ng:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d nginx
docker compose run --rm certbot certonly --webroot -w /var/www/certbot -d sizning-domen.uz
```

> **Serverda allaqachon nginx bor bo'lsa** (boshqa saytlaringiz uchun),
> `nginx` va `certbot` konteynerlarini ISHGA TUSHIRMANG — 80/443 portlari
> band, konflikt chiqadi. Buning o'rniga:
> ```bash
> docker compose up -d           # web, redis, postgres, history
> ```
> `web` 8000-portda turadi. Mavjud nginx'ingizga `deploy/nginx.conf` dagi
> `location` bloklarini ko'chiring va `proxy_pass http://127.0.0.1:8000;`
> qiling. Sertifikatni odatdagicha `certbot --nginx -d subdomen.domeningiz.uz`
> bilan oling. WebSocket uchun `Upgrade` sarlavhalari va
> `proxy_read_timeout 3600s` ni ko'chirishni unutmang — busiz real-time
> ishlamaydi.

### 3. Ishga tushirish

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

Telegram Mini App **faqat https** bilan ishlaydi — sertifikatsiz ochilmaydi.

### 4. Zaxira nusxa

Kanvas Redis'da yashaydi (`appendonly yes` yoqilgan), lekin 1 MB — kuniga
bir necha marta nusxalash arzon:

```bash
docker compose exec redis redis-cli --no-raw GET mp:canvas > canvas-$(date +%F-%H).bin
docker compose exec postgres pg_dump -U mp millionpixel | gzip > db-$(date +%F).sql.gz
```

---

## Kuchsiz serverda (4 GB RAM, 2 yadro)

Loyiha shunday serverda ishlaydi. Xotira taxminan quyidagicha bo'linadi:

| Xizmat | RAM |
|---|---|
| PostgreSQL (sozlangan) | ~600 MB |
| uvicorn × 2 worker | ~350 MB |
| Redis | ~150 MB |
| drain_history | ~120 MB |
| nginx | ~20 MB |
| OS + boshqa saytlaringiz | ~1 GB |
| **Jami** | **~2.3 GB / 4 GB** |

`.env` ga qo'shing:

```ini
# 2 yadro -> 2 worker. Ko'proq qilish foyda bermaydi, kontekst almashinuvi oshadi.
SNAPSHOT_CACHE_SEC=2.0     # kuchsiz CPU da 2 soniya qiling
BROADCAST_INTERVAL_MS=100  # 80 -> 100: CPU kamroq, kechikish sezilmaydi
```

`docker-compose.prod.yml` da `--workers 4` ni `--workers 2` ga o'zgartiring.

PostgreSQL uchun (`postgresql.conf` yoki compose `command`):

```
shared_buffers = 256MB
work_mem = 8MB
max_connections = 50
effective_cache_size = 1GB
```

Redis uchun: `--maxmemory 256mb --maxmemory-policy noeviction`
(`noeviction` muhim — kanvas satri hech qachon o'chirilmasligi kerak).

2 GB swap qo'shib qo'ying — zaxira sifatida:

```bash
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```

### Kanal kengligi

Snapshot gzip bilan quyidagicha siqiladi (o'lchangan):

| Doska holati | gzip | 30 Mb/s da |
|---|---|---|
| bo'sh | 1 KB | 3700 kirish/sek |
| 10% to'lgan | 8 KB | 476 kirish/sek |
| 40% to'lgan | 20 KB | 181 kirish/sek |
| 90% to'lgan | 34 KB | 109 kirish/sek |

WebSocket oqimi kichik: 1000 kishi onlayn bo'lganda kullaut 60 s, ya'ni
butun sayt bo'yicha sekundiga ~17 piksel. Har bir mijozga ~600 bayt/sek
tushadi, 1000 mijozga jami ~5 Mb/s. 30 Mb/s kanalga sig'adi, TAS-IX
(200 Mb/s) orqali kelgan mahalliy foydalanuvchilar esa umuman cheklanmaydi.

**Xulosa:** 500–1000 bir vaqtli foydalanuvchi bemalol. Undan yuqorisida
avval 2 yadro, keyin 30 Mb/s tashqi kanal cheklaydi.

---

## Yuk oshganda

- **`PixelEvent` partitioning.** Kuniga millionlab satr. `created_at` bo'yicha
  oylik partition qiling va eski partitionlarni arxivga oling; rollback
  odatda faqat oxirgi soatlarga kerak.
- **Ko'proq worker.** `--workers` ni CPU yadrosiga tenglashtiring. Kod
  gorizontal kengayishga tayyor: holat Redis'da, fan-out pub/sub orqali,
  hech qanday worker boshqasiga bog'liq emas.
- **`drain_history` ni ko'paytirish.** `CONSUMER` nomini o'zgartirib bir necha
  nusxa qo'shing — Redis Stream guruhi buni qo'llab-quvvatlaydi.
- **Redis bo'g'iz bo'lsa.** Kanvas satrini bo'laklarga bo'lib Redis Cluster'ga
  tarqating (masalan har 100 satr — alohida kalit).
- **Rate limit.** WebSocket'da token bucket bor (20/sek, burst 60);
  `deploy/nginx.conf` da IP bo'yicha ulanish va auth limiti ham qo'yilgan.

---

## Ma'lum cheklovlar (MVP)

- Frontend energiyani o'zi ham hisoblaydi (tez javob uchun), lekin server
  yagona haqiqat: har bo'yashdan keyin `0x03` xabari bilan sinxronlanadi.
  Rad etilgan bo'yash `0x06` orqali aynan qaytariladi.
- `drain_history` bitta consumer sifatida ishlaydi. Yuk oshsa `CONSUMER` nomini
  o'zgartirib bir nechta nusxa qo'shing (Redis Stream guruhi buni qo'llaydi).
- Rollback 20 000 pikseldan katta bo'lsa, mijozlarga alohida paket emas,
  "snapshot'ni qayta yukla" buyrug'i yuboriladi.
