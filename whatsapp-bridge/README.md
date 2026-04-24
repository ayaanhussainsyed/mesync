# MeSync WhatsApp Bridge

Tiny Node sidecar that runs `whatsapp-web.js` and exposes three endpoints the MeSync Flask app calls over localhost:

- `GET  /status`  – `{ connected, me, qr_available }`
- `GET  /qr`      – `{ qr }` (data URL while you still need to scan)
- `GET  /export?chats=N&per_chat=M` – recent chats + messages
- `POST /logout`  – drop the WhatsApp session

## Run

```bash
cd whatsapp-bridge
npm install
npm start
```

Default port is `3011`. Match it with `WHATSAPP_BRIDGE_URL` in `../.env`.

First run prints a QR code in the terminal **and** makes it available to the MeSync `/integrations` page. Scan it from WhatsApp → Settings → Linked Devices → Link a Device.

Session state lives in `./auth/` (LocalAuth). It survives restarts; delete the folder to force a fresh QR.

## Security

The server only accepts loopback connections by default. Set `ALLOW_LOOPBACK_ONLY=0` if you really need remote access (you probably don't).
