# weblog.py — Discord Audit Log Web Server
import os, json, time, uuid, hashlib, asyncio
from pathlib import Path
from aiohttp import web
import aiohttp as _aio

LOG_FILE     = "voice_log.json"
MAX_PER_GUILD = 2000

ACTION_META = {
    "join":          ("🎤","เข้าห้อง",          "voice",   "#22c55e"),
    "leave":         ("🚪","ออกจากห้อง",         "voice",   "#ef4444"),
    "move":          ("🔀","ย้ายห้อง",            "voice",   "#3b82f6"),
    "server_mute":   ("🔇","ปิดไมค์แดง",          "voice",   "#f97316"),
    "server_unmute": ("🔊","เปิดไมค์แดง",         "voice",   "#22c55e"),
    "server_deaf":   ("🔕","ปิดหู",               "voice",   "#dc2626"),
    "server_undeaf": ("🔔","เปิดหู",              "voice",   "#22c55e"),
    "v_disconnect":  ("⚡","ตัดออก (รายคน)",      "voice",   "#ef4444"),
    "mute":          ("🔇","ไมค์แดง (ทีมงาน)",   "mod",     "#f97316"),
    "unmute":        ("🔊","ปลดไมค์ (ทีมงาน)",   "mod",     "#22c55e"),
    "disconnect":    ("✂️","ตัดหมด",              "mod",     "#ef4444"),
    "recall":        ("📣","Recall",               "mod",     "#eab308"),
    "msg_delete":    ("🗑️","ลบข้อความ",           "message", "#ef4444"),
    "msg_edit":      ("✏️","แก้ไขข้อความ",        "message", "#3b82f6"),
    "msg_bulk":      ("💥","ลบข้อความหมู่",       "message", "#dc2626"),
    "timeout":       ("⏱️","หมดเวลา",             "penalty", "#f43f5e"),
    "untimeout":     ("✅","ยกเลิกหมดเวลา",       "penalty", "#22c55e"),
    "ban":           ("🔨","แบน",                  "penalty", "#dc2626"),
    "unban":         ("♻️","ปลดแบน",               "penalty", "#22c55e"),
    "room_create":   ("🏠","สร้างห้องส่วนตัว",    "room",    "#06b6d4"),
    "room_delete":   ("🗑️","ลบห้องส่วนตัว",       "room",    "#8b5cf6"),
    "cmd":           ("⌨️","คำสั่ง",               "cmd",     "#a78bfa"),
    "member_join":   ("🟢","เข้าเซิร์ฟ",            "server",  "#22c55e"),
    "member_leave":  ("🔴","ออกเซิร์ฟ",             "server",  "#ef4444"),
    "member_kick":   ("👟","เตะ",                    "server",  "#f97316"),
    "role_add":      ("⭐","ได้รับยศ",               "server",  "#eab308"),
    "role_remove":   ("💔","เสียยศ",                 "server",  "#94a3b8"),
    "nick_change":   ("✏️","เปลี่ยนชื่อเล่น",       "server",  "#3b82f6"),
    "ch_create":     ("➕","สร้างช่อง",              "server",  "#06b6d4"),
    "ch_delete":     ("➖","ลบช่อง",                "server",  "#ef4444"),
}

_servers: list[dict] = []
_guild_logs: dict[str, list] = {}

# ── image cache ──────────────────────────────────────────────────────────────
_IMG_DIR = Path("img_cache")
_IMG_DIR.mkdir(exist_ok=True)
_http: _aio.ClientSession | None = None

async def _sess():
    global _http
    if _http is None or _http.closed:
        _http = _aio.ClientSession()
    return _http

async def download_image(att_id: int, url: str) -> str | None:
    """Download attachment by ID → img_cache/{att_id}.ext  return filename or None"""
    ext = url.split("?")[0].rsplit(".", 1)[-1].lower()
    if ext not in ("png","jpg","jpeg","gif","webp"): ext = "jpg"
    dest = _IMG_DIR / f"{att_id}.{ext}"
    if dest.exists(): return dest.name
    try:
        s = await _sess()
        async with s.get(url, timeout=_aio.ClientTimeout(total=10)) as r:
            if r.status != 200: return None
            data = await r.read()
            if len(data) > 8_000_000: return None
            dest.write_bytes(data)
            return dest.name
    except Exception:
        return None

def cached_img_url(att_id: int, original_url: str) -> str:
    """Return /img/<file> if cached else original URL."""
    ext = original_url.split("?")[0].rsplit(".", 1)[-1].lower()
    if ext not in ("png","jpg","jpeg","gif","webp"): ext = "jpg"
    p = _IMG_DIR / f"{att_id}.{ext}"
    return f"/img/{p.name}" if p.exists() else original_url


def set_server(gid: str, name: str, icon):
    for s in _servers:
        if s["id"] == gid:
            s.update(name=name, icon=icon); return
    _servers.append({"id": gid, "name": name, "icon": icon})


def remove_server(gid: str):
    global _servers
    _servers = [s for s in _servers if s["id"] != gid]


class LogStore:
    def __init__(self):
        self._load()

    def _load(self):
        global _guild_logs
        try:
            raw = json.loads(Path(LOG_FILE).read_text(encoding="utf-8"))
            _guild_logs = raw if isinstance(raw, dict) else {"legacy": raw}
        except Exception:
            _guild_logs = {}

    def _save(self):
        Path(LOG_FILE).write_text(
            json.dumps(_guild_logs, ensure_ascii=False), encoding="utf-8")

    def add(self, gid: str, action: str, **kw) -> dict:
        m = ACTION_META.get(action, ("❓", action, "other", "#fff"))
        e = {"id": uuid.uuid4().hex[:8], "ts": time.time(), "gid": gid,
             "action": action, "icon": m[0], "label": m[1],
             "category": m[2], "color": m[3], **kw}
        bucket = _guild_logs.setdefault(gid, [])
        bucket.append(e)
        if len(bucket) > MAX_PER_GUILD:
            _guild_logs[gid] = bucket[-MAX_PER_GUILD:]
        self._save()
        return e

    def query(self, gid: str, cat="all", limit=200, q="") -> list:
        rows = _guild_logs.get(gid, [])
        if cat != "all":
            rows = [r for r in rows if r.get("category") == cat]
        if q:
            ql = q.lower()
            def hit(r):
                return any(ql in str(r.get(k, "")).lower()
                           for k in ("actor_name","target_name","user_name",
                                     "staff_name","actor_id","target_id",
                                     "user_id","staff_id","desc","content","before","after"))
            rows = [r for r in rows if hit(r)]
        return list(reversed(rows[-limit:]))


log_store = LogStore()

# ──────────────────────────────────────────── HTML ───
_HTML = """<!DOCTYPE html>
<html lang="th">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Audit Log</title>
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Thai:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#0d1117;--s1:#161b22;--s2:#1c2128;--border:#21262d;--text:#e6edf3;--muted:#7d8590;--accent:#8b5cf6;--r:10px}
*{box-sizing:border-box;margin:0;padding:0}body{background:var(--bg);color:var(--text);font-family:'Noto Sans Thai',sans-serif;min-height:100vh}
/* header */
header{background:linear-gradient(135deg,#160b2e,#0d1117 70%);border-bottom:1px solid #30175c;padding:12px 20px;position:sticky;top:0;z-index:200;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.srv-icon{width:40px;height:40px;border-radius:10px;background:linear-gradient(135deg,#8b5cf6,#6d28d9);display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;flex-shrink:0;overflow:hidden;transition:.3s}
.srv-icon img{width:40px;height:40px;object-fit:cover;border-radius:10px}
.htitle{flex:1;min-width:0}
.htitle h1{font-size:13px;font-weight:700;letter-spacing:.08em;color:#c4b5fd;text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.live-row{display:flex;align-items:center;gap:6px;margin-top:2px}
.dot{width:7px;height:7px;border-radius:50%;background:#22c55e;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 #22c55e55}50%{box-shadow:0 0 0 4px #22c55e00}}
.live-txt{font-size:11px;color:var(--muted)}
.hright{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.srv-select{background:#1c2128;border:1px solid var(--border);color:var(--text);padding:5px 10px;border-radius:8px;font-family:inherit;font-size:12px;cursor:pointer;transition:.15s}
.srv-select:focus{outline:none;border-color:var(--accent)}
.search-wrap{position:relative}
.search-wrap input{background:#1c2128;border:1px solid var(--border);color:var(--text);padding:5px 10px 5px 30px;border-radius:8px;font-family:inherit;font-size:12px;width:180px;transition:.2s}
.search-wrap input:focus{outline:none;border-color:var(--accent);width:220px}
.search-wrap input::placeholder{color:var(--muted)}
.search-ic{position:absolute;left:9px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:12px;pointer-events:none}
/* tabs */
.tabs{display:flex;gap:5px;padding:10px 16px;border-bottom:1px solid var(--border);background:var(--bg);overflow-x:auto;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tab{border:1px solid var(--border);background:transparent;color:var(--muted);padding:5px 14px;border-radius:20px;cursor:pointer;font-family:inherit;font-size:12px;white-space:nowrap;transition:.15s;transform:scale(1);active:scale(.95)}
.tab:hover{background:#21262d;color:var(--text)}
.tab:active{transform:scale(.93)}
.tab.active{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
/* log list */
main{padding:10px 16px;max-width:980px;margin:0 auto;display:flex;flex-direction:column;gap:7px}
@keyframes slideIn{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:translateY(0)}}
.entry{background:var(--s1);border:1px solid var(--border);border-left:3px solid var(--clr,#8b5cf6);border-radius:var(--r);padding:10px 14px;display:grid;grid-template-columns:70px auto 1fr auto;gap:10px;align-items:start;animation:slideIn .2s ease;transition:border-color .15s}
.entry:hover{border-color:#3d444d}
/* timestamp */
.ts-t{font-size:13px;font-weight:600;color:var(--text);font-variant-numeric:tabular-nums}
.ts-d{font-size:10px;color:var(--muted);margin-top:2px}
/* badge */
.badge{display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:12px;font-size:11px;font-weight:600;border:1px solid transparent;white-space:nowrap}
/* content */
.top-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.actor-group{display:flex;align-items:center;gap:6px}
.actor{display:flex;align-items:center;gap:4px}
.aname{font-size:13px;font-weight:600}
.arrow{color:var(--muted);font-size:12px}
.ch{background:#21262d;border:1px solid var(--border);border-radius:6px;padding:1px 7px;font-size:11px;color:#a0a8b8;white-space:nowrap}
.desc{font-size:12px;color:var(--muted);margin-top:4px;line-height:1.5}
/* avatar */
.av{width:28px;height:28px;border-radius:50%;object-fit:cover;flex-shrink:0}
.av-i{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:#fff;flex-shrink:0}
/* msg blocks */
.msg-block{margin-top:6px;padding:6px 10px;border-radius:6px;font-size:12px;line-height:1.5;white-space:pre-wrap;word-break:break-word;border-left:3px solid}
.msg-block.del{background:#ef44441a;border-color:#ef4444;color:#fca5a5}
.msg-block.before{background:#3b82f61a;border-color:#3b82f6;color:#93c5fd}
.msg-block.after{background:#22c55e1a;border-color:#22c55e;color:#86efac}
.msg-label{display:block;font-size:10px;font-weight:700;letter-spacing:.06em;opacity:.7;margin-bottom:3px;text-transform:uppercase}
.att-img{max-width:220px;max-height:160px;border-radius:6px;margin-top:6px;display:block;border:1px solid var(--border)}
/* members */
.members{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.mtag{display:inline-flex;align-items:center;gap:4px;background:#21262d;border:1px solid var(--border);border-radius:6px;padding:2px 7px;font-size:11px;color:#a0a8b8}
/* count */
.cnt{font-size:11px;color:var(--muted);white-space:nowrap;padding-top:2px}
.who-row{display:flex;flex-wrap:wrap;gap:6px;margin-top:3px}
.who-actor,.who-target{display:flex;align-items:center;gap:4px;background:#ffffff08;border:1px solid var(--border);border-radius:6px;padding:2px 8px 2px 4px}
.who-label{font-size:9px;color:var(--muted);letter-spacing:.05em;text-transform:uppercase;margin-right:2px}
.who-name{font-size:12px;font-weight:600;color:var(--text)}
.who-target .who-label{color:#f97316aa}
.empty{text-align:center;color:var(--muted);padding:60px;font-size:14px}
@media(max-width:600px){.entry{grid-template-columns:60px auto 1fr}.cnt{display:none}.search-wrap input{width:130px}.search-wrap input:focus{width:150px}}
</style></head>
<body>
<header>
  <div class="srv-icon" id="srv-icon"><span id="srv-init">KN</span></div>
  <div class="htitle"><h1 id="srv-name">Discord Audit Log</h1>
    <div class="live-row"><span class="dot"></span><span class="live-txt" id="status">กำลังโหลด...</span></div>
  </div>
  <div class="hright">
    <select class="srv-select" id="srv-sel"><option value="">-- เลือกเซิร์ฟเวอร์ --</option></select>
    <div class="search-wrap"><span class="search-ic">🔍</span><input type="text" id="search" placeholder="ค้นหาชื่อหรือไอดี..." autocomplete="off"></div>
  </div>
</header>
<div class="tabs" id="tabs">
  <button class="tab active" data-cat="all">ทั้งหมด</button>
  <button class="tab" data-cat="voice">🎤 เสียง</button>
  <button class="tab" data-cat="mod">🔇 ควบคุม</button>
  <button class="tab" data-cat="message">💬 ข้อความ</button>
  <button class="tab" data-cat="penalty">🚫 โทษ</button>
  <button class="tab" data-cat="room">🏠 ห้องส่วนตัว</button>
  <button class="tab" data-cat="cmd">⌨️ คำสั่ง</button>
  <button class="tab" data-cat="server">🏛️ เซิร์ฟ</button>
</div>
<main id="list"></main>
<script>
let guild='', cat='all', q='', audioCtx=null, debounceT=null;

function playClick(f=700){
  try{
    if(!audioCtx) audioCtx=new(window.AudioContext||window.webkitAudioContext)();
    const o=audioCtx.createOscillator(), g=audioCtx.createGain();
    o.connect(g); g.connect(audioCtx.destination);
    o.type='sine';
    o.frequency.setValueAtTime(f,audioCtx.currentTime);
    o.frequency.exponentialRampToValueAtTime(f*.5,audioCtx.currentTime+.07);
    g.gain.setValueAtTime(.12,audioCtx.currentTime);
    g.gain.exponentialRampToValueAtTime(.001,audioCtx.currentTime+.07);
    o.start(); o.stop(audioCtx.currentTime+.07);
  }catch(e){}
}

function esc(s){return(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

function av(url, name, sz=28){
  const cols=['#8b5cf6','#3b82f6','#06b6d4','#22c55e','#f97316','#ec4899','#eab308'];
  const c=cols[name?[...name].reduce((a,ch)=>a+ch.charCodeAt(0),0)%cols.length:0];
  const init=(name||'?')[0].toUpperCase();
  const st=`width:${sz}px;height:${sz}px`;
  if(url && url!='None' && url!='null')
    return `<img src="${url}" class="av" style="${st}" title="${esc(name)}" onerror="this.outerHTML='<div class=\\"av-i\\" style=\\"${st};background:${c}\\">${init}</div>'">`;
  return `<div class="av-i" style="${st};background:${c}">${init}</div>`;
}

function actorTarget(e){
  const an=e.actor_name||e.staff_name||e.user_name||'';
  const aa=e.actor_avatar||e.staff_avatar||e.user_avatar||'';
  const tn=e.target_name||e.user_name||'';
  const ta=e.target_avatar||e.user_avatar||'';
  if(!an) return '';
  let h=`<div class="actor-group"><div class="actor">${av(aa,an)}<span class="aname">${esc(an)}</span></div>`;
  if(tn && tn!==an) h+=`<span class="arrow">→</span><div class="actor">${av(ta,tn)}<span class="aname">${esc(tn)}</span></div>`;
  return h+'</div>';
}

function renderEntry(e){
  const d=new Date(e.ts*1000);
  const tt=d.toLocaleTimeString('th-TH',{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
  const dd=d.toLocaleDateString('th-TH',{day:'2-digit',month:'2-digit',year:'2-digit'});
  let extra='';
  if(e.content) extra+=`<div class="msg-block del"><span class="msg-label">ข้อความที่ลบ</span>${esc(e.content)}</div>`;
  if(e.before)  extra+=`<div class="msg-block before"><span class="msg-label">ก่อนแก้ไข</span>${esc(e.before)}</div>`;
  if(e.after)   extra+=`<div class="msg-block after"><span class="msg-label">หลังแก้ไข</span>${esc(e.after)}</div>`;
  if(e.attachment_url) extra+=`<img src="${e.attachment_url}" class="att-img" onerror="this.style.display='none'" alt="ไฟล์แนบ">`;
  let mems='';
  if(e.members && e.members.length){
    const sh=e.members.slice(0,20), more=e.members.length-20;
    mems='<div class="members">'+sh.map(m=>`<span class="mtag">${av(m.avatar,m.name,18)}<span>${esc(m.name)}</span></span>`).join('')+(more>0?`<span class="mtag">+${more}</span>`:'')+`</div>`;
  }
  const ch=e.channel?`<span class="ch">🔊 ${esc(e.channel)}</span>`:'';
  const chMove=e.src_channel?`<span class="ch">📢 ${esc(e.src_channel)}</span><span class="arrow">→</span><span class="ch">📥 ${esc(e.dst_channel)}</span>`:'';
  const cnt=e.member_count!=null?e.member_count+' คน':e.count!=null?e.count+' คน':'';
  // แสดงชื่อคนทำ (actor) และคนโดน (target) แยกชัดเจน
  const aName=e.actor_name||e.staff_name||'';
  const aAv=e.actor_avatar||e.staff_avatar||'';
  const tName=e.target_name||'';
  const tAv=e.target_avatar||'';
  let whoRow='';
  if(aName){
    whoRow+=`<div class="who-actor">${av(aAv,aName,22)}<span class="who-label">ผู้ทำ</span><span class="who-name">${esc(aName)}</span></div>`;
  }
  if(tName && tName!==aName){
    whoRow+=`<div class="who-target">${av(tAv,tName,22)}<span class="who-label">เป้าหมาย</span><span class="who-name">${esc(tName)}</span></div>`;
  }
  if(!aName){
    const uName=e.user_name||'';
    const uAv=e.user_avatar||'';
    if(uName) whoRow+=`<div class="who-actor">${av(uAv,uName,22)}<span class="who-name">${esc(uName)}</span></div>`;
  }
  return `<div class="entry" style="--clr:${e.color}">
    <div><div class="ts-t">${tt}</div><div class="ts-d">${dd}</div></div>
    <div style="display:flex;flex-direction:column;gap:4px">
      <span class="badge" style="color:${e.color};border-color:${e.color}33;background:${e.color}15">${e.icon} ${e.label}</span>
      ${whoRow?`<div class="who-row">${whoRow}</div>`:''}
    </div>
    <div>
      <div class="top-row">${ch}${chMove}</div>
      ${e.desc?`<div class="desc">${esc(e.desc)}</div>`:''}
      ${extra}${mems}
    </div>
    <div class="cnt">${cnt}</div></div>`;
}

async function load(){
  if(!guild) return;
  try{
    const r=await fetch(`/api/logs?guild=${guild}&cat=${cat}&limit=300&q=${encodeURIComponent(q)}`);
    const data=await r.json();
    document.getElementById('status').textContent=`ออนไลน์ • ${data.length} รายการ • อัปเดตทุก 5 วิ`;
    const el=document.getElementById('list');
    el.innerHTML=data.length?data.map(renderEntry).join(''):'<div class="empty">ยังไม่มีรายการ</div>';
  }catch{document.getElementById('status').textContent='เชื่อมต่อไม่ได้';}
}

async function loadServers(){
  try{
    const data=await(await fetch('/api/servers')).json();
    const sel=document.getElementById('srv-sel');
    sel.innerHTML=data.map(s=>`<option value="${s.id}">${s.name}</option>`).join('');
    if(data.length){
      guild=data[0].id;
      document.getElementById('srv-name').textContent=data[0].name+' — Audit Log';
      if(data[0].icon){
        document.getElementById('srv-icon').innerHTML=`<img src="${data[0].icon}" alt="icon">`;
      }
    }
    sel.addEventListener('change',()=>{
      playClick(600);
      guild=sel.value;
      const s=data.find(x=>x.id===guild);
      if(s){
        document.getElementById('srv-name').textContent=s.name+' — Audit Log';
        document.getElementById('srv-icon').innerHTML=s.icon?`<img src="${s.icon}" alt="icon">`:'<span>KN</span>';
      }
      load();
    });
    load();
  }catch{}
}

document.getElementById('tabs').addEventListener('click',e=>{
  const t=e.target.closest('.tab'); if(!t) return;
  playClick(750);
  document.querySelectorAll('.tab').forEach(x=>x.classList.remove('active'));
  t.classList.add('active'); cat=t.dataset.cat; load();
});

document.getElementById('search').addEventListener('input',e=>{
  clearTimeout(debounceT);
  debounceT=setTimeout(()=>{q=e.target.value; load();},350);
});

loadServers(); setInterval(load,5000);
</script>
</body></html>"""


async def _idx(r): return web.Response(text=_HTML, content_type="text/html", headers={"Access-Control-Allow-Origin":"*"})

async def _img_serve(r):
    fname = r.match_info.get("fname","")
    if "/" in fname or ".." in fname: return web.Response(status=403)
    p = _IMG_DIR / fname
    if not p.exists(): return web.Response(status=404)
    ct = {"png":"image/png","gif":"image/gif","webp":"image/webp"}.get(fname.rsplit(".",1)[-1],"image/jpeg")
    return web.Response(body=p.read_bytes(), content_type=ct, headers={"Access-Control-Allow-Origin":"*"})
async def _srvs(r): return web.json_response(_servers, headers={"Access-Control-Allow-Origin":"*"})
async def _logs(r):
    gid   = r.query.get("guild","")
    cat   = r.query.get("cat","all")
    q     = r.query.get("q","")
    limit = min(int(r.query.get("limit","300")),500)
    if not gid and _servers: gid=_servers[0]["id"]
    return web.json_response(log_store.query(gid,cat,limit,q), headers={"Access-Control-Allow-Origin":"*"})

async def start(port: int = None):
    port = port or int(os.getenv("SERVER_PORT","8080"))
    app = web.Application()
    app.router.add_get("/", _idx)
    app.router.add_get("/img/{fname}", _img_serve)
    app.router.add_get("/api/servers", _srvs)
    app.router.add_get("/api/logs", _logs)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner,"0.0.0.0",port).start()
    print(f"[weblog] http://0.0.0.0:{port}")
