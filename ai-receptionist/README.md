# AI Receptionist — демо для агенцій нерухомості (США)

Чат-віджет, який вставляється одним рядком на сайт агенції. Спілкується як
рецепціоніст, кваліфікує ліда, бронює огляд у Google Calendar, і складає всі
розмови в дашборд. Додатково — голосовий дзвінок через Vapi.

**Головне: усе працює одразу без жодного ключа.** Без ключів вмикається
демо-режим із заглушками. Кожен ключ, який ти додаєш, вмикає свій шматок.

---

## 1. Запустити локально

У терміналі, з цієї папки:

```bash
npm install
npm run dev
```

Відкрий у браузері:

| Адреса | Що це |
|---|---|
| http://localhost:3000 | демо-сайт агенції, чат-бабл у правому нижньому кутку |
| http://localhost:3000/dashboard | **сторінка менеджера** — ліди і розмови |
| http://localhost:3000/login | вхід (поки Google не підключений — пускає одразу) |

На дашборді зверху є рядок кружечків — зелений означає «ключ на місці»,
сірий — «ще не налаштовано». Це твоя карта того, що лишилось зробити.

---

## 2. Ключі — по одному

Скопіюй шаблон і заповнюй поступово:

```bash
cp .env.example .env.local
```

Після кожної зміни в `.env.local` перезапусти `npm run dev` (Ctrl+C, потім знову).

### 2.1 Claude — щоб бот справді думав

Без цього ключа бот відповідає за скриптом (5 заготовлених фраз).

1. Зайди на **console.anthropic.com** → **API Keys** → **Create Key**
2. Скопіюй ключ (починається на `sk-ant-`)
3. У `.env.local`: `ANTHROPIC_API_KEY=sk-ant-...`

Ціна: ~1–3 центи за розмову. Якщо хочеш дешевше — постав
`CLAUDE_MODEL=claude-sonnet-5`, буде приблизно в 2.5 раза дешевше.

**Перевірка:** напиши віджету «what do you have in Nob Hill under 1.3M» —
має назвати конкретний об'єкт з адресою, а не загальну фразу.

### 2.2 Supabase — щоб ліди зберігались назавжди

Без цього ключа ліди живуть у пам'яті процесу і зникають при перезапуску.

1. **supabase.com** → **New project** (безкоштовний тариф)
2. Ліворуч **SQL Editor** → **New query** → встав туди весь вміст файлу
   `supabase/schema.sql` з цієї папки → **Run**
3. Ліворуч **Settings → API**, звідти скопіюй:
   - **Project URL** → у `.env.local` в `NEXT_PUBLIC_SUPABASE_URL=`
   - **service_role** ключ (не `anon`!) → в `SUPABASE_SERVICE_ROLE_KEY=`

**Перевірка:** поговори з віджетом, потім у Supabase → **Table Editor** → `leads`
має з'явитись рядок.

### 2.3 Google Calendar — щоб бот бронював огляд

Робимо через service account — без екрану згоди Google і без верифікації.

1. **console.cloud.google.com** → створи проєкт (або візьми наявний)
2. **APIs & Services → Library** → знайди **Google Calendar API** → **Enable**
3. **APIs & Services → Credentials** → **Create credentials** →
   **Service account** → дай будь-яку назву → **Done**
4. Клікни на створений акаунт → вкладка **Keys** → **Add key** →
   **Create new key** → **JSON** → файл завантажиться
5. Відкрий цей JSON, звідти:
   - `client_email` → у `.env.local` в `GOOGLE_SERVICE_ACCOUNT_EMAIL=`
   - `private_key` → в `GOOGLE_PRIVATE_KEY=` (весь рядок, разом з лапками)
6. Відкрий **calendar.google.com** → налаштування потрібного календаря →
   **Поділитися з окремими людьми** → додай той самий `client_email` →
   права **«Вносити зміни до подій»**
7. Там же скопіюй **Calendar ID** → в `GOOGLE_CALENDAR_ID=`

**Перевірка:** попроси віджет забронювати огляд — подія має з'явитись у календарі.

> Обмеження: service account не може слати email-запрошення гостям без
> domain-wide delegation. Тому контакти ліда бот кладе в опис події. Для демо
> цього достатньо.

### 2.4 Вхід через Google у дашборд

Поки не налаштовано — `/dashboard` відкритий для всіх. Для демо це нормально,
але перед тим як показувати клієнту публічне посилання — закрий.

1. **console.cloud.google.com → Credentials** → **Create credentials** →
   **OAuth client ID** → тип **Web application**
2. В **Authorized redirect URIs** додай:
   - `http://localhost:3000/api/auth/callback`
   - `https://ТВІЙ-ДОМЕН.vercel.app/api/auth/callback`
3. Скопіюй **Client ID** і **Client secret** у `.env.local`
4. Додай свою пошту: `DASHBOARD_ALLOWED_EMAILS=kholstyninandriy@gmail.com`
5. Придумай будь-який довгий випадковий рядок для `AUTH_SECRET=`

### 2.5 Google Maps — карти в картках об'єктів

1. **console.cloud.google.com → Credentials** → **Create credentials** → **API key**
2. **Library** → знайди **Maps Embed API** → **Enable**
3. Ключ → в `NEXT_PUBLIC_GOOGLE_MAPS_API_KEY=`

Без ключа картка просто стає клікабельним посиланням на Google Maps.

### 2.6 Vapi — голосовий дзвінок

Це найдорожча і найдовша частина: рахунок іде за хвилини розмови.

1. **dashboard.vapi.ai** → зареєструйся → **API Keys**
2. **Private key** → в `.env.local` в `VAPI_PRIVATE_KEY=`
3. **Public key** → в `NEXT_PUBLIC_VAPI_PUBLIC_KEY=`
4. Створи голосового агента з того самого промпта — виконай у терміналі:
   ```bash
   curl -X POST http://localhost:3000/api/vapi/provision
   ```
   У відповідь прийде `assistantId` — встав його в
   `NEXT_PUBLIC_VAPI_ASSISTANT_ID=` і перезапусти.
5. Якщо Vapi відповість помилкою про модель — встав у `.env.local`
   `VAPI_MODEL=` з тією назвою, яку Vapi показує у своєму списку моделей.

Після цього у шапці віджета з'явиться іконка телефону — це дзвінок голосом
прямо з браузера, з тими самими інструментами (пошук об'єктів, збереження ліда,
бронювання). Дзвінки з реального номера — це вже Twilio, окремий крок.

> Голос я не міг протестувати за тебе: для цього потрібен твій акаунт Vapi.
> Код написаний, але першу перевірку робиш ти.

---

## 3. Деплой на Vercel

1. Зайди на **vercel.com** → **Add New → Project** → підключи цей репозиторій
2. **ВАЖЛИВО:** у полі **Root Directory** вкажи `ai-receptionist`
   (інакше Vercel шукатиме сайт у корені, де лежить Telegram-бот)
3. **Environment Variables** — перенеси туди все зі свого `.env.local`
4. Додай `NEXT_PUBLIC_APP_URL=https://твій-домен.vercel.app`
5. **Deploy**

Після деплою онови redirect URI в Google OAuth (пункт 2.4) на бойовий домен.

---

## 4. Як вставити віджет на сайт клієнта

Один рядок перед `</body>`:

```html
<script src="https://твій-домен.vercel.app/embed.js" async></script>
```

Віджет працює всередині iframe, тому CSS сайту клієнта і наш CSS не можуть
зіткнутися. Поки чат закритий — iframe розміром із бабл і нічого не перекриває.

---

## 5. Що де лежить

| Файл | За що відповідає |
|---|---|
| `lib/prompt.ts` | характер рецепціоніста і назва агенції — правити тут під кожного клієнта |
| `lib/listings.ts` | демо-об'єкти нерухомості |
| `lib/tools.ts` | що бот вміє робити (пошук, збереження ліда, бронювання) — спільне для чату і голосу |
| `lib/claude.ts` | цикл розмови з Claude |
| `lib/calendar.ts` | Google Calendar |
| `lib/store.ts` | Supabase + запасне сховище в пам'яті |
| `lib/auth.ts` | вхід через Google |
| `components/ChatWidget.tsx` | сам чат |
| `public/embed.js` | той рядок, що вставляється на сайт клієнта |
| `app/dashboard/` | сторінка менеджера |

**Щоб перебрендувати демо під конкретну агенцію** — правиш `AGENCY` у
`lib/prompt.ts` і масив у `lib/listings.ts`. Більше нічого чіпати не треба.

---

## 6. Якщо захочеш винести в окремий репозиторій

Папка самодостатня, тому:

```bash
cp -r ai-receptionist ~/ai-receptionist
cd ~/ai-receptionist && git init && git add . && git commit -m "init"
```

---

## Чого тут свідомо немає

- Дзвінків на реальний телефонний номер (Twilio) — наступний крок після Vapi
- Оплати, кількох агенцій в одній системі, ролей користувачів
- Реального MLS-фіду — об'єкти захардкоджені в `lib/listings.ts`

Це демо для показу власнику агенції, не production-система.
