# weblog.py — Voice Audit Web Server
import os, json, time, uuid
from pathlib import Path
from aiohttp import web

LOG_FILE  = "voice_log.json"
MAX_ENTRIES = 1000

ACTION_META = {
    "join":        ("🎤", "เข้าห้อง",     "voice",   "#22c55e"),
    "leave":       ("🚪", "ออกจากห้อง",   "voice",   "#ef4444"),
    "move":        ("🔀", "ย้ายห้อง",     "voice",   "#3b82f6"),
    "mute":        ("🔇", "ไมค์แดง",      "mod",     "#f97316"),
    "unmute":      ("🔊", "ปลดไมค์",      "mod",     "#22c55e"),
    "disconnect":  ("✂️",  "ตัดหมด",      "mod",     "#ef4444"),
    "recall":      ("📣", "Recall",        "mod",     "#eab308"),
    "room_create": ("🏠", "สร้างห้อง",    "room",    "#06b6d4"),
    "room_delete": ("🗑️",  "ลบห้อง",     "room",    "#8b5cf6"),
    "timeout":     ("⏱️",  "หมดเวลา",    "penalty", "#f43f5e"),
    "untimeout":   ("✅", "ยกเลิกหมดเวลา","penalty", "#22c55e"),
    "ban":         ("🔨", "แบน",          "penalty", "#dc2626"),
    "unban":       ("♻️",  "ปลดแบน",     "penalty", "#22c55e"),
    "cmd":         ("⌨️",  "คำสั่ง",     "cmd",     "#a78bfa"),
}


_server_info: dict = {"name": "Discord Server", "icon": None}

def set_server_info(name: str, icon: str | None):
    _server_info["name"] = name
    _server_info["icon"] = icon


class LogStore:
    def __init__(self):
        self.entries: list[dict] = []
        self._load()

    def _load(self):
        try:
            self.entries = json.loads(Path(LOG_FILE).read_text(encoding="utf-8"))
        except Exception:
            self.entries = []

    def _save(self):
        Path(LOG_FILE).write_text(
            json.dumps(self.entries[-MAX_ENTRIES:], ensure_ascii=False, indent=None),
            encoding="utf-8",
        )

    def add(self, action: str, **kw) -> dict:
        icon, label, cat, color = ACTION_META.get(action, ("❓", action, "voice", "#fff"))
        entry = {
            "id":       uuid.uuid4().hex[:8],
            "ts":       time.time(),
            "action":   action,
            "icon":     icon,
            "label":    label,
            "category": cat,
            "color":    color,
            **kw,
        }
        self.entries.append(entry)
        if len(self.entries) > MAX_ENTRIES:
            self.entries = self.entries[-MAX_ENTRIES:]
        self._save()
        return entry

    def query(self, cat="all", limit=200) -> list[dict]:
        src = self.entries if cat == "all" else [e for e in self.entries if e["category"] == cat]
        return list(reversed(src[-limit:]))


log_store = LogStore()

# ─── HTML ──────────────────────────────────────────────────────────────
_HTML = r"""<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Voice Audit Log</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Thai:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#0d1117;--surface:#161b22;--border:#21262d;--text:#e6edf3;--muted:#7d8590;--accent:#8b5cf6;--radius:10px}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:'Noto Sans Thai',sans-serif;min-height:100vh}
/* header */
header{background:linear-gradient(135deg,#1a0533 0%,#0d1117 60%);border-bottom:1px solid #3b1f6b;padding:18px 24px;display:flex;align-items:center;justify-content:space-between;position:sticky;top:0;z-index:100;gap:12px;flex-wrap:wrap}
.hgroup{display:flex;align-items:center;gap:14px}
.server-badge{width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,#8b5cf6,#6d28d9);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:15px;letter-spacing:.5px;flex-shrink:0;overflow:hidden}.server-badge img{width:44px;height:44px;object-fit:cover;border-radius:12px}
.htitle h1{font-size:14px;font-weight:700;letter-spacing:.08em;color:#c4b5fd;text-transform:uppercase}
.htitle .sub{font-size:12px;color:var(--muted);margin-top:2px;display:flex;align-items:center;gap:6px}
.live-dot{width:8px;height:8px;border-radius:50%;background:#22c55e;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1;box-shadow:0 0 0 0 #22c55e55}50%{opacity:.7;box-shadow:0 0 0 4px #22c55e00}}
.count-pill{background:#21262d;border:1px solid var(--border);border-radius:20px;padding:4px 14px;font-size:12px;color:var(--muted)}
/* tabs */
.tab-bar{display:flex;gap:6px;padding:14px 20px;border-bottom:1px solid var(--border);background:var(--bg);overflow-x:auto}
.tab{border:1px solid var(--border);background:transparent;color:var(--muted);padding:6px 16px;border-radius:20px;cursor:pointer;font-family:inherit;font-size:13px;white-space:nowrap;transition:.15s}
.tab:hover{background:#21262d;color:var(--text)}
.tab.active{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
/* entries */
main{padding:12px 20px;display:flex;flex-direction:column;gap:8px;max-width:900px;margin:0 auto}
.entry{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:12px 16px;display:grid;grid-template-columns:80px auto 1fr auto;align-items:start;gap:12px;transition:border-color .15s}
.entry:hover{border-color:#3d444d}
.ts-col{text-align:right}
.ts-time{font-size:14px;font-weight:600;color:var(--text);font-variant-numeric:tabular-nums;letter-spacing:.02em}
.ts-date{font-size:11px;color:var(--muted);margin-top:2px}
.avatar{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:700;flex-shrink:0;color:#fff}
.info{}
.info-top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.badge{display:inline-flex;align-items:center;gap:4px;padding:2px 10px;border-radius:12px;font-size:11px;font-weight:600;background:#ffffff12;border:1px solid transparent}
.username{font-weight:600;font-size:14px}
.desc{font-size:13px;color:var(--muted);margin-top:4px;line-height:1.5}
.ch-chip{display:inline-flex;align-items:center;gap:4px;background:#21262d;border:1px solid var(--border);border-radius:6px;padding:1px 8px;font-size:11px;color:#a0a8b8;white-space:nowrap}
.count-badge{font-size:12px;color:var(--muted);white-space:nowrap;padding-top:2px}
.members-list{margin-top:6px;display:flex;flex-wrap:wrap;gap:4px}
.member-tag{background:#21262d;border:1px solid var(--border);border-radius:6px;padding:1px 8px;font-size:11px;color:#a0a8b8}
.empty{text-align:center;color:var(--muted);padding:60px 0;font-size:14px}
/* scrollbar */
::-webkit-scrollbar{width:6px;height:6px}::-webkit-scrollbar-track{background:#0d1117}::-webkit-scrollbar-thumb{background:#30363d;border-radius:3px}
@media(max-width:600px){.entry{grid-template-columns:64px auto 1fr}.count-badge{display:none}}
</style>
</head>
<body>
<header>
  <div class="hgroup">
    <div class="server-badge" id="srv-icon"><span id="srv-init">KN</span></div>
    <div class="htitle">
      <h1 id="srv-name">Voice Audit Log</h1>
      <div class="sub"><span class="live-dot"></span><span id="status">กำลังโหลด...</span></div>
    </div>
  </div>
  <div class="count-pill" id="total">0 รายการ</div>
</header>
<div class="tab-bar">
  <button class="tab active" data-cat="all">ทั้งหมด</button>
  <button class="tab" data-cat="voice">🎤 เสียง</button>
  <button class="tab" data-cat="mod">🔇 ควบคุม</button>
  <button class="tab" data-cat="room">🏠 ห้องส่วนตัว</button>
  <button class="tab" data-cat="penalty">🚫 โทษ</button>
  <button class="tab" data-cat="cmd">⌨️ คำสั่ง</button>
</div>
<main id="list"></main>
<script>
let cat='all', data=[];
const fmt = ts => {
  const d=new Date(ts*1000);
  return {
    time: d.toLocaleTimeString('th-TH',{hour12:false}),
    date: d.toLocaleDateString('th-TH',{day:'2-digit',month:'2-digit',year:'2-digit'})
  };
};
const avatarColor = name => {
  const h = [...name].reduce((a,c)=>a+c.charCodeAt(0),0);
  const colors = ['#8b5cf6','#3b82f6','#06b6d4','#22c55e','#f97316','#ec4899','#eab308'];
  return colors[h % colors.length];
};
const render = () => {
  const el = document.getElementById('list');
  if(!data.length){ el.innerHTML='<div class="empty">ยังไม่มีรายการ</div>'; return; }
  el.innerHTML = data.map(e => {
    const {time,date} = fmt(e.ts);
    const uname = e.user_name||e.staff_name||'System';
    const init = uname[0]?.toUpperCase()||'?';
    const bg = avatarColor(uname);
    const members = (e.members||[]).slice(0,12).map(m=>`<span class="member-tag">${m.name}</span>`).join('');
    const extra_more = (e.members||[]).length > 12 ? `<span class="member-tag">+${(e.members||[]).length-12}</span>` : '';
    return `<div class="entry">
      <div class="ts-col"><div class="ts-time">${time}</div><div class="ts-date">${date}</div></div>
      <div class="avatar" style="background:${bg}">${init}</div>
      <div class="info">
        <div class="info-top">
          <span class="badge" style="color:${e.color};border-color:${e.color}33;background:${e.color}18">${e.icon} ${e.label}</span>
          <span class="username">${uname}</span>
          ${e.channel ? `<span class="ch-chip">🔊 ${e.channel}</span>` : ''}
          ${e.src_channel ? `<span class="ch-chip">📢 ${e.src_channel}</span><span style="color:var(--muted);font-size:12px">→</span><span class="ch-chip">📥 ${e.dst_channel}</span>` : ''}
        </div>
        <div class="desc">${e.desc||''}</div>
        ${members ? `<div class="members-list">${members}${extra_more}</div>` : ''}
      </div>
      <div class="count-badge">${e.member_count != null ? e.member_count+' คน' : e.count != null ? e.count+' คน' : ''}</div>
    </div>`;
  }).join('');
};
const load = async () => {
  try {
    const r = await fetch(`/api/logs?cat=${cat}&limit=200`);
    data = await r.json();
    document.getElementById('status').textContent = 'ออนไลน์ • อัปเดตทุก 5 วิ';
    document.getElementById('total').textContent = data.length+' รายการ';
    render();
  } catch { document.getElementById('status').textContent = 'ไม่สามารถเชื่อมต่อได้'; }
};
document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => {
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  t.classList.add('active');
  cat = t.dataset.cat;
  load();
}));
fetch('/api/server').then(r=>r.json()).then(d=>{
  if(d.name) document.getElementById('srv-name').textContent=d.name+' — Voice Audit Log';
  if(d.icon){
    const el=document.getElementById('srv-icon');
    el.innerHTML=`<img src="${d.icon}" alt="server icon">`;
  }
}).catch(()=>{});
load(); setInterval(load, 5000);
</script>
</body></html>"""


# ─── Web Server ─────────────────────────────────────────────────────────
async def _handle_server(req):
    return web.json_response(_server_info)


async def _handle_index(req):
    return web.Response(text=_HTML, content_type="text/html")


async def _handle_api(req):
    cat   = req.query.get("cat", "all")
    limit = min(int(req.query.get("limit", "200")), 500)
    return web.json_response(log_store.query(cat, limit))


async def start(port: int = None):
    port = port or int(os.getenv("SERVER_PORT", "8080"))
    app  = web.Application()
    app.router.add_get("/", _handle_index)
    app.router.add_get("/api/server", _handle_server)
    app.router.add_get("/api/logs", _handle_api)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"[weblog] http://0.0.0.0:{port}")
