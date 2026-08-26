# HANY APPAREL — deployment and handover

Django store for onlinebrocoach.com: catalogue, cart, checkout, Fawaterk card
payments, cash on delivery, TikTok tracking, and order notification emails.

---

## 1. Before you go live

Three things must be done or the site will not work correctly in production.

### a) Replace the placeholder images

Every image in `accounts/static/images/` is a generated placeholder stamped
**REPLACE THIS FILE**. They exist so the layout renders; they must not ship.

| File | Used by |
|---|---|
| `images/testo/logo.png` | Header and footer of every page |
| `images/store/black-front.png` / `black-back.png` | Black tee |
| `images/store/white-front.png` / `white-back.png` | White tee |

Keep the same filenames and they drop straight in. Product images work best at
a 4:5 ratio (for example 1000×1250).

For products added later, the admin's image fields accept either a static path
(`images/store/name.png`) or a full `https://` CDN URL. **Vercel's filesystem is
read-only at runtime, so file uploads cannot be stored there** — images must be
committed to the repo or hosted externally (Cloudinary, S3, Bunny).

### b) Fill in the two blanks in `test_project/credentials.py`

```python
ORDER_NOTIFICATION_EMAIL = "..."   # where order emails are sent
TIKTOK_PIXEL_ID          = "..."   # Events Manager > your pixel
TIKTOK_ACCESS_TOKEN      = "..."   # ... > Settings > Set up Events API
```

Plus the SMTP block in the same file so emails actually send. Until SMTP is
configured, notifications are written to the Vercel log instead of being sent —
nothing breaks and no order is lost, but you will not receive anything.

Gmail: `smtp.gmail.com`, port 587, TLS on, and an **app password** (Google
rejects normal account passwords).
Hostinger: `smtp.hostinger.com`, port 465, `EMAIL_USE_SSL=1`, TLS off.

### c) Set the environment variables on Vercel

Only these are required; the payment credentials are already in the code.

```
DJANGO_SECRET_KEY=<50+ random characters>
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=onlinebrocoach.com,www.onlinebrocoach.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://onlinebrocoach.com,https://www.onlinebrocoach.com
SITE_URL=https://onlinebrocoach.com
DATABASE_URL=<Neon pooled connection string, with ?sslmode=require>
```

Generate a secret key with:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

`SITE_URL` matters more than it looks: it builds the return URLs handed to
Fawaterk, and those cannot be relative. Get it wrong and customers are
redirected to the wrong place after paying.

---

## 2. Fawaterk setup

Credentials are hardcoded in `test_project/credentials.py` as requested.
Authentication uses the OAuth2 client-credentials grant: the app posts the
client id and secret to the token URL, caches the returned bearer token until
just before it expires, and refreshes automatically.

**Register this webhook URL** in the Fawaterk dashboard under
Integrations → Webhooks:

```
https://onlinebrocoach.com/store/payment/webhook_json/
```

The `_json` on the end is required. Fawaterk sends form-encoded data to any URL
without it. (Both formats are handled, but JSON is cleaner to debug.)

The app also passes a `webhookUrl` on each invoice it creates, which overrides
the dashboard setting for store orders. So store payments work even if the
dashboard URL is wrong — but set it anyway for the coaching payment links.

### How a card payment flows

1. Customer places the order → an invoice is created → they are redirected to
   Fawaterk. Stock is reserved at this point.
2. Fawaterk calls the webhook. The HMAC signature is verified against the Hash
   API Key before anything is trusted. The order is marked paid, the TikTok
   purchase is reported, and the notification email is sent.
3. The customer returns to the success page, which independently confirms the
   payment with Fawaterk rather than trusting the redirect.

Whichever of steps 2 and 3 lands first wins; the other becomes a no-op. A
payment that fails or is abandoned releases the reserved stock.

Every callback is logged in the admin under **Webhook events**, including ones
with bad signatures or no matching order. That is the first place to look if a
customer says they paid but the order is still pending.

> Your coaching packages use hardcoded `app.fawaterk.com/paymentRequest/...`
> links on the homepage and pricing page. Those bypass this app entirely: no
> order record, no email, no purchase event. Their webhooks will arrive here,
> find no matching order, and be logged and ignored. That is safe, just be
> aware the shop only tracks apparel orders.

---

## 3. TikTok

The pixel loads in the `<head>` of every page via
`accounts/templates/partials/tracking.html`. If `TIKTOK_PIXEL_ID` is empty the
block renders nothing at all, which keeps development traffic out of reporting.

Events: `ViewContent`, `AddToCart`, `InitiateCheckout`, `PlaceAnOrder`,
`CompletePayment`. Each is reported twice — once from the browser, once from
the server — sharing an `event_id` so TikTok merges them into one conversion.

**`CompletePayment` is TikTok's name for a purchase.** `Purchase` is Meta's
name; using it means campaign optimisation never sees your conversions.

Set `TIKTOK_TEST_EVENT_CODE` while testing so events land in the Test Events
tab, and remove it before launch.

---

## 4. Running locally

```bash
pip install -r requirements.txt
export DJANGO_DEBUG=1
python manage.py migrate
python manage.py seed_store          # sample products, optional
python manage.py createsuperuser
python manage.py runserver
```

Without `DATABASE_URL` it uses local SQLite. Without SMTP, emails print to the
console. Set `FAWATERK_LIVE=0` to point payments at staging.

Run the tests:

```bash
python manage.py test store          # 90 tests
```

---

## 5. Day-to-day admin

`/admin/`

- **Products** — price, sale price, images, visibility, ordering. Stock is per
  size under Variants; a size with 0 stock shows as sold out and cannot be
  bought.
- **Orders** — customer details, items and totals are read-only, because an
  order must always match what the customer submitted and paid. Change
  `order_status` as you fulfil, or use the bulk actions. Paid orders cannot be
  deleted; cancel them instead.
- **Webhook events** — read-only payment callback log.

---

## 6. Before the first real sale

Run one live transaction end to end with a real card for a small amount:

1. Place an order, pay, confirm you land on the success page.
2. Check the order shows **Paid** in the admin.
3. Check the notification email arrived.
4. Check the webhook event is logged with `signature_valid = True`.
5. Check `CompletePayment` appears in TikTok Events Manager, counted once.

Everything in this project has been tested against the published Fawaterk and
TikTok specifications, but nothing has been run against the live services.
Step 4 in particular is worth watching: if signatures fail, the Hash API Key in
`credentials.py` does not match the one in the dashboard.

---

## 7. A note on the hardcoded credentials

`test_project/credentials.py` contains live payment secrets in plain text, at
your explicit request. Two practical consequences:

- Keep this repository **private**. The Hash API Key lets anyone forge a "paid"
  webhook and mark orders as paid without paying.
- If the repo is ever made public or handed to another developer, rotate the
  client secret and hash key in the Fawaterk dashboard.

Every value in that file already honours an environment variable of the same
name, so you can move any single secret to Vercel's environment settings later
without touching the code — the hardcoded value simply becomes the fallback.
