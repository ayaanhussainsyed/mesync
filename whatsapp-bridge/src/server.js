// MeSync WhatsApp sidecar.
//
// Uses whatsapp-web.js + LocalAuth (same pattern as whatsapp-opencode).
// Session state lives in ./auth/ so relogin isn't needed across restarts.
// Exposes:
//   GET  /status            -> { connected, me, qr_available }
//   GET  /qr                -> { qr }           (data URL while unauthenticated)
//   GET  /export?chats=N&per_chat=M  -> { me, chats: [...] }
//   POST /logout            -> { ok }

const express = require('express');
const path = require('path');
const fs = require('fs');
const QRCode = require('qrcode');
const qrcodeTerminal = require('qrcode-terminal');
const { Client, LocalAuth } = require('whatsapp-web.js');

const PORT = Number(process.env.PORT || 3011);
const LOOPBACK_ONLY = process.env.ALLOW_LOOPBACK_ONLY !== '0';
const AUTH_DIR = path.join(__dirname, '..', 'auth');

if (!fs.existsSync(AUTH_DIR)) fs.mkdirSync(AUTH_DIR, { recursive: true });

let currentQr = null;     // raw QR string from whatsapp-web.js
let qrDataUrl = null;     // cached image-dataurl version of the current QR
let isReady = false;
let me = null;

const client = new Client({
  authStrategy: new LocalAuth({ clientId: 'mesync', dataPath: AUTH_DIR }),
  puppeteer: {
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
    ],
  },
});

client.on('qr', async (qr) => {
  currentQr = qr;
  isReady = false;
  me = null;
  try {
    qrDataUrl = await QRCode.toDataURL(qr, { margin: 1, width: 320 });
  } catch (e) {
    qrDataUrl = null;
  }
  console.log('[whatsapp-bridge] new QR ready. Open /integrations on MeSync to scan.');
  qrcodeTerminal.generate(qr, { small: true });
});

client.on('authenticated', () => { console.log('[whatsapp-bridge] authenticated'); });
client.on('auth_failure', (m) => { console.log('[whatsapp-bridge] auth failure:', m); });

client.on('ready', () => {
  isReady = true;
  currentQr = null;
  qrDataUrl = null;
  me = client.info?.wid?.user || null;
  console.log(`[whatsapp-bridge] ready as ${me}`);
});

client.on('disconnected', (reason) => {
  console.log('[whatsapp-bridge] disconnected:', reason);
  isReady = false;
  me = null;
});

client.initialize().catch((err) => {
  console.error('[whatsapp-bridge] initialize failed:', err);
});

// ---------- HTTP ----------
const app = express();
app.use(express.json());

app.use((req, res, next) => {
  if (!LOOPBACK_ONLY) return next();
  const ip = req.socket.remoteAddress || '';
  if (ip === '127.0.0.1' || ip === '::1' || ip === '::ffff:127.0.0.1') return next();
  return res.status(403).json({ error: 'loopback only' });
});

app.get('/status', (req, res) => {
  res.json({
    connected: isReady,
    me,
    qr_available: !!currentQr,
  });
});

app.get('/qr', (req, res) => {
  // Prefer the data URL so the Flask UI can <img> it directly.
  res.json({ qr: qrDataUrl, raw: currentQr, connected: isReady });
});

app.post('/logout', async (req, res) => {
  try {
    await client.logout();
    isReady = false;
    me = null;
    currentQr = null;
    qrDataUrl = null;
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ ok: false, error: String(e) });
  }
});

app.get('/export', async (req, res) => {
  if (!isReady) return res.status(409).json({ error: 'not ready', connected: false });
  const chatLimit = Math.max(1, Math.min(40, Number(req.query.chats) || 15));
  const perChatLimit = Math.max(1, Math.min(100, Number(req.query.per_chat) || 40));

  try {
    const chats = await client.getChats();
    chats.sort((a, b) => (b.timestamp || 0) - (a.timestamp || 0));
    const picked = chats.slice(0, chatLimit);

    const out = [];
    for (const chat of picked) {
      try {
        const msgs = await chat.fetchMessages({ limit: perChatLimit });
        const flat = [];
        for (const m of msgs) {
          const body = m.body || '';
          if (!body) continue;
          flat.push({
            body,
            from_me: !!m.fromMe,
            author: m.author || null,
            timestamp: m.timestamp || 0,
          });
        }
        out.push({
          id: chat.id?._serialized || null,
          name: chat.name || chat.id?.user || 'Unknown',
          is_group: !!chat.isGroup,
          unread: chat.unreadCount || 0,
          messages: flat,
        });
      } catch (e) {
        console.warn('[whatsapp-bridge] fetch for chat failed:', e.message || e);
      }
    }

    res.json({ me, chats: out });
  } catch (e) {
    res.status(500).json({ error: String(e) });
  }
});

app.listen(PORT, '127.0.0.1', () => {
  console.log(`[whatsapp-bridge] listening on 127.0.0.1:${PORT}`);
});
