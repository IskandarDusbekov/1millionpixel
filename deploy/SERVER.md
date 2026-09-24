# Serverga qo'yish — ketma-ket buyruqlar

Sizning holatingiz: Ubuntu server, unda allaqachon **2 ta sayt va nginx**
bor. Million Piksel alohida papkaga o'rnatiladi va host'dagi nginx orqali
ishlaydi. **Docker'dagi nginx konteyneri ishlatilmaydi** — 80/443 portlari
band, to'qnashuv chiqadi.

Server: 4 GB RAM, 2 yadro, 80 GB NVMe. Yetadi.

---

## 0. AVVAL TEKSHIRING (hech narsani o'zgartirmaydi)

Bu buyruqlar faqat o'qiydi — mavjud saytlaringizga tegmaydi.

```bash
ssh root@SERVER_IP
```

Nima o'rnatilgan:

```bash
nginx -v; certbot --version; docker --version; ffmpeg -version 2>/dev/null | head -1
```

Resurslar:

```bash
free -h; df -h /; nproc
```

**Eng muhimi — 8000-port bo'shmi:**

```bash
sudo ss -ltnp | grep -E ':(8000|8080)' || echo "8000 BO'SH — davom eting"
```

Agar band bo'lsa, `.env` da `WEB_PORT=8010` qiling va `nginx-host.conf`
dagi `127.0.0.1:8000` larni `127.0.0.1:8010` ga almashtiring.

Mavjud saytlaringiz qayerda:

```bash
ls /etc/nginx/sites-enabled/
```

Mavjud sertifikatlaringiz (bularga tegilmaydi):

```bash
sudo certbot certificates | grep "Certificate Name"
```

### Nimani qayta o'rnatish SHART EMAS

| Narsa | Holat |
|---|---|
| nginx | Bor — qayta o'rnatilmaydi, faqat yangi sayt fayli qo'shiladi |
| certbot | Bor — `apt install certbot` ni **o'tkazib yuboring** |
| Mavjud sertifikatlar | Tegilmaydi, yangisi alohida qo'shiladi |
| Mavjud saytlar | Tegilmaydi, o'z fayllarida qoladi |

### Docker

Agar `docker --version` javob bermasa:

```bash
curl -fsSL https://get.docker.com | sh
```

O'rnatish ~1-2 daqiqa (~120 MB yuklanadi). Tekshirish:

```bash
docker run --rm hello-world
```

> **UFW haqida.** Docker odatda UFW qoidalarini chetlab o'tadi va e'lon
> qilingan portlarni ochib yuboradi. Bu loyihada bunday xavf yo'q: Redis
> va PostgreSQL portlari umuman e'lon qilinmagan, web esa faqat
> `127.0.0.1` ga bog'langan. Ya'ni tashqaridan faqat nginx ko'rinadi.

> **Docker o'rnatgingiz kelmasa** — Docker'siz ham ishlatsa bo'ladi, lekin
> unda serverga Redis va PostgreSQL ni alohida o'rnatish, `systemd`
> xizmati yozish kerak. Docker bilan mavjud saytlaringiz bilan
> aralashmaslik kafolati kuchliroq.

---

## 1. Domen

Subdomen qo'shing — masalan `piksel.SIZNING-DOMEN.uz`. DNS'da:

| Tur | Nom | Qiymat |
|---|---|---|
| A | `piksel` | serveringiz IP manzili |

DNS tarqalganini tekshiring (serverning IP si chiqishi kerak):

```bash
dig +short piksel.SIZNING-DOMEN.uz
```

> Telegram Mini App **faqat https** bilan ishlaydi. Sertifikatni 3-qadamda
> olamiz. DNS tayyor bo'lmasa, certbot sertifikat bera olmaydi.

---

## 2. Loyihani joylash

```bash
apt install -y git
```

### Agar repo OCHIQ (public) bo'lsa

```bash
git clone https://github.com/IskandarDusbekov/1millionpixel.git /opt/millionpixel
```

### Agar repo YOPIQ (private) bo'lsa — deploy key

Serverda kalit yarating (parol so'raganda Enter bosing):

```bash
ssh-keygen -t ed25519 -C "millionpixel-server" -f ~/.ssh/millionpixel -N ""
```

Ochiq kalitni ko'ring va nusxalang:

```bash
cat ~/.ssh/millionpixel.pub
```

GitHub'da: repo → **Settings** → **Deploy keys** → **Add deploy key** →
nomi `server`, kalitni joylashtiring, "Allow write access" **belgilamang**
(serverga faqat o'qish kerak).

SSH sozlamasi:

```bash
printf 'Host github-mp\n  HostName github.com\n  User git\n  IdentityFile ~/.ssh/millionpixel\n  IdentitiesOnly yes\n' >> ~/.ssh/config
```

Tekshiring (`successfully authenticated` chiqishi kerak):

```bash
ssh -T git@github-mp
```

Klonlang:

```bash
git clone github-mp:IskandarDusbekov/1millionpixel.git /opt/millionpixel
```

### Keyin ikkala holatda ham

```bash
cd /opt/millionpixel && ls
```

Sozlamalar faylini yarating:

```bash
cd /opt/millionpixel && cp .env.example .env
```

Maxfiy kalitlarni generatsiya qiling va ekranga chiqaring:

```bash
echo "SECRET_KEY=$(openssl rand -hex 32)"; echo "JWT_SECRET=$(openssl rand -hex 32)"; echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"
```

`.env` ni tahrirlang:

```bash
nano /opt/millionpixel/.env
```

Quyidagilarni albatta o'zgartiring:

```ini
SECRET_KEY=<yuqoridagi birinchi qiymat>
JWT_SECRET=<yuqoridagi ikkinchi qiymat>
POSTGRES_PASSWORD=<yuqoridagi uchinchi qiymat>

DEBUG=0
USE_SQLITE=0
FAKE_REDIS=0

ALLOWED_HOSTS=piksel.SIZNING-DOMEN.uz
CSRF_TRUSTED_ORIGINS=https://piksel.SIZNING-DOMEN.uz
PUBLIC_URL=https://piksel.SIZNING-DOMEN.uz

WEB_WORKERS=2
SNAPSHOT_CACHE_SEC=2.0
BROADCAST_INTERVAL_MS=100

TELEGRAM_BOT_TOKEN=<@BotFather bergan token>
TELEGRAM_BOT_USERNAME=<bot nomi, @ siz>
```

> `DEBUG=0`, `USE_SQLITE=0`, `FAKE_REDIS=0` — **majburiy**. Parolsiz kirish
> yo'li loyihada yo'q: saytga faqat Telegram hisobi bilan kiriladi, admin
> panelga esa faqat superuser login va paroli bilan.

Fayl faqat sizga ko'rinadigan bo'lsin:

```bash
chmod 600 /opt/millionpixel/.env
```

---

## 3. Swap (4 GB uchun tavsiya etiladi)

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
```

```bash
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 4. Ishga tushirish

Faqat kerakli xizmatlar (nginx konteynerisiz):

```bash
cd /opt/millionpixel && docker compose up -d --build
```

Birinchi qurilish 3-6 daqiqa oladi. Keyin:

```bash
docker compose ps
```

Beshta xizmat `running` bo'lishi kerak: `redis`, `postgres`, `web`,
`history`, `timelapse`.

Bazani tayyorlang:

```bash
docker compose exec web python manage.py migrate
```

Admin hisobi:

```bash
docker compose exec web python manage.py createsuperuser
```

Ishlayaptimi:

```bash
curl -s http://127.0.0.1:8000/api/state
```

`{"size": 1000, "max_energy": 50, ...}` chiqishi kerak.

---

## 5. Nginx (mavjud nginx'ga qo'shamiz)

```bash
sudo cp /opt/millionpixel/deploy/nginx-host.conf /etc/nginx/sites-available/millionpixel
```

Domenni almashtiring (ikkala joyda):

```bash
sudo sed -i 's/piksel.SIZNING-DOMEN.uz/piksel.HAQIQIY-DOMEN.uz/g' /etc/nginx/sites-available/millionpixel
```

Yoqing:

```bash
sudo ln -s /etc/nginx/sites-available/millionpixel /etc/nginx/sites-enabled/
```

Sintaksisni tekshiring — **xato bo'lsa reload qilmang**, boshqa
saytlaringiz ham o'chib qoladi:

```bash
sudo nginx -t
```

`syntax is ok` va `test is successful` chiqsa:

```bash
sudo systemctl reload nginx
```

> Agar `duplicate "connection_upgrade" variable` xatosi chiqsa — bu map
> boshqa saytingizda ham bor. `nginx-host.conf` dagi `map $http_upgrade`
> blokini o'chirib tashlang va qaytadan `nginx -t` qiling.

---

## 6. HTTPS sertifikati

Certbot allaqachon bor bo'lsa, o'rnatish qadamini **o'tkazib yuboring**.
Faqat yo'q bo'lsa:

```bash
sudo apt install -y certbot python3-certbot-nginx
```

Yangi domen uchun sertifikat. Bu **mavjud sertifikatlaringizga tegmaydi** —
har bir domen `/etc/letsencrypt/live/<domen>/` da alohida turadi va
avtomatik yangilanish taymeri yangisini o'zi ko'radi:

```bash
sudo certbot --nginx -d piksel.HAQIQIY-DOMEN.uz
```

Savollarga: email kiriting, shartlarga rozilik bering, HTTP'dan HTTPS'ga
yo'naltirishni **yoqing** (2-variant).

Avtomatik yangilanishni tekshiring:

```bash
sudo certbot renew --dry-run
```

Saytni oching: `https://piksel.HAQIQIY-DOMEN.uz`

---

## 7. Telegram bot

```bash
cd /opt/millionpixel && docker compose run --rm web python manage.py telegram_bot --url https://piksel.HAQIQIY-DOMEN.uz --setup-only
```

`Bot: @sizning_bot` va `Menyu tugmasi sozlandi` chiqishi kerak.

**Botni doimiy ishlatib qo'ying — bu majburiy.** Oddiy brauzerdan «Telegram
bot orqali kirish» tugmasi shu bot orqali ishlaydi (foydalanuvchi botda
«Tasdiqlayman» ni bosadi). Bot to'xtab qolsa, Mini App ichidan kirish
ishlayveradi, lekin brauzerdan kirib bo'lmaydi.

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d bot
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail 20 bot
```

> Bitta bot tokeniga faqat BITTA `telegram_bot` jarayoni ulanishi mumkin
> (Telegram long polling shuni talab qiladi) va tokenda webhook o'rnatilgan
> bo'lmasligi kerak.

Nginx'ni yangilagandan keyin admin panelga fayl yuklash ishlashi uchun
(`deploy/nginx-host.conf` dagi `/admin/panel/api/files/upload` bloki, 6 MB
gacha) `sudo nginx -t && sudo systemctl reload nginx` ni unutmang.

---

## 8. Tekshiruv ro'yxati

```bash
curl -sI https://piksel.HAQIQIY-DOMEN.uz | head -1
```

- [ ] Sayt ochiladi, kirish ekrani ko'rinadi
- [ ] «Telegram bot orqali kirish» → botda «Tasdiqlayman» → sayt o'zi kiradi
- [ ] Piksel qo'yiladi va **ikkinchi brauzerda darhol ko'rinadi**
- [ ] `/admin/panel/` — login/parol so'raydi, superuser bilan kirilganda panel ochiladi
- [ ] Panelda **SEO** bo'limida asosiy manzilni yozib saqlang; `/robots.txt` va `/sitemap.xml` to'g'ri chiqadi
- [ ] Panelda rasm yuklab ko'ring (413 chiqsa — nginx yangilanmagan)
- [ ] Telegram'da bot ochilib, Mini App ishlaydi

WebSocket ishlayotganini log'dan ko'rish:

```bash
cd /opt/millionpixel && docker compose logs -f web --tail 50
```

`WebSocket /ws/canvas ... [accepted]` satrlari ko'rinishi kerak.

---

## Fayllar qayerda turadi

| Nima | Joyi | Mavjud saytlaringizga aloqasi |
|---|---|---|
| Loyiha kodi | `/opt/millionpixel/` | Yo'q — alohida papka |
| Sozlamalar | `/opt/millionpixel/.env` | Yo'q |
| Timelapse suratlari | `/opt/millionpixel/media/timelapse/` | Yo'q |
| Baza va Redis ma'lumoti | `/var/lib/docker/volumes/` | Yo'q — Docker ichida |
| Nginx sayt fayli | `/etc/nginx/sites-available/millionpixel` | Alohida fayl |
| Sertifikat | `/etc/letsencrypt/live/piksel.DOMEN.uz/` | Alohida papka |
| Loglar | `/var/log/nginx/millionpixel.*.log` | Alohida fayl |

Mavjud saytlaringiz odatda `/var/www/` yoki `/home/...` da — ularga
biror fayl yozilmaydi.

**Butunlay o'chirib tashlash** (agar yoqmasa, hech qanday iz qolmaydi):

```bash
cd /opt/millionpixel && docker compose down -v
```

```bash
sudo rm /etc/nginx/sites-enabled/millionpixel && sudo nginx -t && sudo systemctl reload nginx
```

```bash
sudo rm -rf /opt/millionpixel
```

---

## Kundalik buyruqlar

Holat:

```bash
cd /opt/millionpixel && docker compose ps
```

Loglar:

```bash
cd /opt/millionpixel && docker compose logs -f --tail 100
```

Qayta ishga tushirish:

```bash
cd /opt/millionpixel && docker compose restart web
```

Kodni yangilash (kompyuterda `git push` qilgandan keyin):

```bash
cd /opt/millionpixel && git pull && docker compose up -d --build && docker compose exec web python manage.py migrate
```

> `.env` git'da yo'q, shuning uchun `git pull` uni hech qachon
> almashtirmaydi. Yangi sozlama qo'shilsa, `.env.example` dan ko'chirib
> qo'shasiz.

To'xtatish:

```bash
cd /opt/millionpixel && docker compose down
```

Resurslar (boshqa saytlaringizga joy qolyaptimi):

```bash
docker stats --no-stream; free -h
```

---

## Zaxira nusxa

Kanvas (1 MB) va baza:

```bash
cd /opt/millionpixel && docker compose exec -T redis redis-cli --no-raw GET mp:canvas > backup-canvas-$(date +%F-%H).bin
```

```bash
cd /opt/millionpixel && docker compose exec -T postgres pg_dump -U mp millionpixel | gzip > backup-db-$(date +%F).sql.gz
```

Har kuni avtomatik (cron):

```bash
(crontab -l 2>/dev/null; echo "0 4 * * * cd /opt/millionpixel && docker compose exec -T postgres pg_dump -U mp millionpixel | gzip > /opt/backups/mp-\$(date +\%F).sql.gz") | crontab -
```

```bash
sudo mkdir -p /opt/backups && sudo chown $USER:$USER /opt/backups
```

---

## Timelapse videosi

Suratlar `/opt/millionpixel/media/timelapse/` da yig'iladi.

```bash
ls /opt/millionpixel/media/timelapse/ | wc -l
```

Video yig'ish:

```bash
sudo apt install -y ffmpeg
```

```bash
cd /opt/millionpixel && ffmpeg -framerate 24 -pattern_type glob -i 'media/timelapse/*.png' -c:v libx264 -pix_fmt yuv420p -vf scale=1000:1000:flags=neighbor timelapse.mp4
```

Kompyuteringizga yuklab olish (Windows PowerShell'da):

```bash
scp root@SERVER_IP:/opt/millionpixel/timelapse.mp4 .
```

---

## Muammolar

**Sayt ochilmaydi, nginx 502 beradi**

```bash
cd /opt/millionpixel && docker compose logs web --tail 50
```

Odatda `.env` da xato (masalan `ALLOWED_HOSTS` da domen yo'q).

**WebSocket ulanmaydi (piksel qo'yilmaydi)**

`nginx-host.conf` dagi `location /ws/` bloki va `map $http_upgrade`
joyidami tekshiring:

```bash
sudo nginx -T | grep -A5 "location /ws/"
```

**Xotira tugadi**

```bash
free -h; docker stats --no-stream
```

`.env` da `WEB_WORKERS=1` qiling va `docker compose up -d web`.

**Boshqa saytlarim o'chib qoldi**

Demak `nginx -t` dan o'tmagan konfiguratsiya reload qilingan.

```bash
sudo rm /etc/nginx/sites-enabled/millionpixel && sudo nginx -t && sudo systemctl reload nginx
```

Keyin xatoni tuzatib, qaytadan urinib ko'ring.
