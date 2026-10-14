import os
import json
import weblog
import hashlib
import time
import asyncio
import discord
from discord.ext import commands

# ===================== ตั้งค่า =====================
def load_token():
    t = os.getenv("DISCORD_TOKEN")
    if t:
        return t.strip()
    try:
        with open("token.txt", "r", encoding="utf-8") as f:  # ไฟล์นี้ไม่ต้องขึ้น GitHub
            return f.read().strip()
    except FileNotFoundError:
        raise SystemExit("ไม่พบโทเคน: ตั้ง DISCORD_TOKEN หรือสร้างไฟล์ token.txt")


TOKEN = load_token()
PREFIX = "!"

# ยศที่ใช้คำสั่งทีมงาน และยศที่ถูกข้าม ตั้งจากในดิสด้วย /ตั้งค่ายศ (เฉพาะแอดมิน)
# ห้อง lobby และหมวดหมู่ที่สร้างห้อง ตั้งจากในดิสด้วย /ตั้งค่าห้อง (เฉพาะแอดมิน)
DATA_FILE = "vroom_data.json"
STREAM_TEXT = "องค์กรลับ พร้อมใช้งาน ♻️"                # ข้อความสถานะสตรีมมิ่ง
STREAM_URL = "https://www.twitch.tv/discord"          # ต้องเป็นลิงก์ twitch/youtube ถึงจะขึ้นสีม่วง
# ==================================================

intents = discord.Intents.default()
intents.members = True
intents.voice_states = True
intents.message_content = True


# ---------- เก็บข้อมูล ----------
class Store:
    def __init__(self):
        self.rooms = {}      # channel_id -> owner_id
        self.users = {}      # owner_id -> {name, limit, status, bans}
        self.config = {"staff_roles": [], "protected_roles": [], "lobby": 0, "category": 0}
        self.load()

    def load(self):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            self.rooms = {int(k): v for k, v in d.get("rooms", {}).items()}
            self.users = {int(k): v for k, v in d.get("users", {}).items()}
            self.config.update(d.get("config", {}))
        except Exception:
            pass

    def save(self):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({"rooms": self.rooms, "users": self.users, "config": self.config}, f, ensure_ascii=False)

    def profile(self, uid):
        return self.users.setdefault(uid, {"name": None, "limit": 0, "status": None, "bans": []})


store = Store()


class VoiceBot(commands.Bot):
    async def setup_hook(self):
        self.add_view(PanelView())
        await weblog.start()
        try:
            await self.tree.sync()
        except Exception as e:
            print("sync error:", e)


bot = VoiceBot(command_prefix=PREFIX, intents=intents, help_command=None)


# ===================== ส่วนที่ 1: คำสั่งทีมงาน =====================
def is_staff():
    async def pred(ctx):
        m = ctx.author
        return m.guild_permissions.administrator or any(r.id in store.config["staff_roles"] for r in m.roles)
    return commands.check(pred)


def has_protected(m):
    return any(r.id in store.config["protected_roles"] for r in m.roles)


def lines(members):
    return "\n".join(f"• {m.mention} | {m.display_name} | {m.id}" for m in members)


async def mass_voice(ctx, mode):
    if not ctx.author.voice or not ctx.author.voice.channel:
        return await ctx.send("เข้าห้องเสียงก่อนใช้คำสั่งนี้")
    if ctx.interaction:
        await ctx.defer()
    vc = ctx.author.voice.channel
    t0 = time.perf_counter()
    ok, already, protected, failed = [], [], [], []
    todo = []
    for m in vc.members:
        if m.bot or m.id == ctx.author.id:
            continue
        if has_protected(m):
            protected.append(m)
        elif mode == "mute" and m.voice.mute:
            already.append(m)
        elif mode == "unmute" and not m.voice.mute:
            already.append(m)
        else:
            todo.append(m)

    async def do(m):
        if mode == "mute":
            await m.edit(mute=True)
        elif mode == "unmute":
            await m.edit(mute=False)
        else:
            await m.move_to(None)

    results = await asyncio.gather(*[do(m) for m in todo], return_exceptions=True)
    for m, r in zip(todo, results):
        (failed if isinstance(r, Exception) else ok).append(m)
    _action = {"mute":"mute","unmute":"unmute"}.get(mode,"disconnect")
    _desc   = {"mute":f"ไมค์แดง {len(ok)} คน ใน {vc.name}",
                "unmute":f"ปลดไมค์ {len(ok)} คน ใน {vc.name}",
                "disconnect":f"ตัดหมด {len(ok)} คน ใน {vc.name}"}[mode]
    weblog.log_store.add(str(ctx.guild.id),_action,
        actor_name=ctx.author.display_name, actor_id=str(ctx.author.id),
        actor_avatar=str(ctx.author.display_avatar.url),
        staff_name=ctx.author.display_name, staff_id=str(ctx.author.id),
        channel=vc.name, channel_id=str(vc.id),
        count=len(ok),
        members=[{"id":str(m.id),"name":m.display_name,
                  "avatar":str(m.display_avatar.url)} for m in ok],
        desc=_desc)

    secs = time.perf_counter() - t0
    if mode == "disconnect":
        text = (f"✂️ ตัดการเชื่อมต่อในห้อง **{vc.name}** เสร็จแล้ว!\n"
                f"• ตัดแล้ว: {len(ok)} คน\n• ข้าม (มี role): {len(protected)} คน\n"
                f"• ไม่สำเร็จ: {len(failed)} คน\n⚡ เวลาที่ใช้: {secs:.2f} วินาที")
        return await ctx.send(text)

    title, label, color = (("🔇 รายงาน Server Mute", "Mute", discord.Color.red()) if mode == "mute"
                           else ("🔊 รายงาน Server Unmute", "Unmute", discord.Color.green()))
    desc = f"{vc.mention}\n\n✅ **{label} สำเร็จ ({len(ok)} คน)**\n{lines(ok)}"
    if already:
        desc += f"\n⏭️ **ข้ามคนที่{'ถูก Mute อยู่แล้ว' if mode == 'mute' else 'ไม่ได้ถูก Mute'}**\n{len(already)} คน"
    if protected:
        desc += f"\n🛡️ **ข้าม (มี role) ({len(protected)} คน)**\n" + "\n".join(f"• {m.display_name}" for m in protected)
    if failed:
        desc += f"\n❌ **ไม่สำเร็จ**\n{len(failed)} คน"
    embed = discord.Embed(title=title, description=desc[:4000], color=color)
    embed.set_footer(text=f"⚡ เวลาที่ใช้: {secs:.2f} วินาที")
    await ctx.send(embed=embed)


@bot.hybrid_command(name="ไมค์แดง", description="Server Mute ทุกคนในห้องเสียงที่คุณอยู่")
@is_staff()
async def mic_red(ctx):
    await mass_voice(ctx, "mute")


@bot.hybrid_command(name="ปลดไมค์", description="ปลด Server Mute ทุกคนในห้องเสียงที่คุณอยู่")
@is_staff()
async def mic_free(ctx):
    await mass_voice(ctx, "unmute")


@bot.hybrid_command(name="ตัดหมด", description="ตัดการเชื่อมต่อทุกคนในห้องเสียงที่คุณอยู่")
@is_staff()
async def cut_all(ctx):
    await mass_voice(ctx, "disconnect")


# ----- ตั้งค่ายศจากในดิส (เฉพาะแอดมิน) -----
def settings_embed(guild):
    def fmt(ids):
        txt = " ".join(f"<@&{i}>" for i in ids if guild.get_role(i))
        return txt or "ยังไม่ได้ตั้ง"
    e = discord.Embed(title="⚙️ ตั้งค่ายศ", color=discord.Color.blurple())
    e.add_field(name="🛠️ ยศที่ใช้ !ไมค์แดง !ปลดไมค์ !ตัดหมด ได้", value=fmt(store.config["staff_roles"]), inline=False)
    e.add_field(name="🛡️ ยศที่ถูกข้าม (ไม่โดนมิวต์/ตัด)", value=fmt(store.config["protected_roles"]), inline=False)
    e.set_footer(text="เลือกได้หลายยศ • เอาเครื่องหมายออกทั้งหมดเพื่อล้างค่า • แอดมินใช้คำสั่งได้เสมอ")
    return e


class RoleSettingsView(discord.ui.View):
    def __init__(self, guild):
        super().__init__(timeout=300)
        for key, holder, row in (("staff_roles", "เลือกยศที่ใช้คำสั่งได้", 0),
                                 ("protected_roles", "เลือกยศที่ถูกข้าม", 1)):
            defaults = [discord.SelectDefaultValue(id=i, type=discord.SelectDefaultValueType.role)
                        for i in store.config[key] if guild.get_role(i)][:25]
            sel = discord.ui.RoleSelect(placeholder=holder, min_values=0, max_values=25,
                                        default_values=defaults, row=row)
            sel.callback = self.make_cb(key, sel)
            self.add_item(sel)

    def make_cb(self, key, sel):
        async def cb(i: discord.Interaction):
            store.config[key] = [r.id for r in sel.values]
            store.save()
            await i.response.edit_message(embed=settings_embed(i.guild))
        return cb

    async def interaction_check(self, i: discord.Interaction):
        if not i.user.guild_permissions.administrator:
            await i.response.send_message("เฉพาะแอดมินเท่านั้น", ephemeral=True)
            return False
        return True


@bot.hybrid_command(name="ตั้งค่ายศ", description="ตั้งค่ายศที่ใช้คำสั่งทีมงานและยศที่ถูกข้าม (แอดมินเท่านั้น)")
@commands.has_permissions(administrator=True)
@commands.guild_only()
async def set_roles(ctx):
    await ctx.send(embed=settings_embed(ctx.guild), view=RoleSettingsView(ctx.guild))


def room_settings_embed(guild):
    lobby = guild.get_channel(store.config["lobby"])
    cat = guild.get_channel(store.config["category"])
    e = discord.Embed(title="⚙️ ตั้งค่าห้องส่วนตัว", color=discord.Color.blurple())
    e.add_field(name="🎤 ห้อง lobby (กดเข้าเพื่อสร้างห้อง)", value=lobby.mention if lobby else "ยังไม่ได้ตั้ง", inline=False)
    e.add_field(name="📁 หมวดหมู่ที่สร้างห้อง", value=cat.mention if cat else "หมวดเดียวกับ lobby", inline=False)
    return e


class RoomSettingsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        lobby = discord.ui.ChannelSelect(placeholder="เลือกห้อง lobby (ห้องเสียง)",
                                         channel_types=[discord.ChannelType.voice], row=0)
        cat = discord.ui.ChannelSelect(placeholder="เลือกหมวดหมู่ที่สร้างห้อง (ไม่เลือก = หมวดเดียวกับ lobby)",
                                       channel_types=[discord.ChannelType.category],
                                       min_values=0, max_values=1, row=1)
        lobby.callback = self.make_cb("lobby", lobby)
        cat.callback = self.make_cb("category", cat)
        self.add_item(lobby)
        self.add_item(cat)

    def make_cb(self, key, sel):
        async def cb(i: discord.Interaction):
            store.config[key] = sel.values[0].id if sel.values else 0
            store.save()
            await i.response.edit_message(embed=room_settings_embed(i.guild))
        return cb

    async def interaction_check(self, i: discord.Interaction):
        if not i.user.guild_permissions.administrator:
            await i.response.send_message("เฉพาะแอดมินเท่านั้น", ephemeral=True)
            return False
        return True


@bot.hybrid_command(name="ตั้งค่าห้อง", description="ตั้งห้อง lobby และหมวดหมู่ของห้องส่วนตัว (แอดมินเท่านั้น)")
@commands.has_permissions(administrator=True)
@commands.guild_only()
async def set_rooms(ctx):
    await ctx.send(embed=room_settings_embed(ctx.guild), view=RoomSettingsView())


# ===================== ส่วนที่ 2: ห้องส่วนตัว =====================
def is_locked(ch):
    return ch.overwrites_for(ch.guild.default_role).connect is False


def is_hidden(ch):
    return ch.overwrites_for(ch.guild.default_role).view_channel is False


async def act_lock(ch, locked):
    ow = ch.overwrites_for(ch.guild.default_role)
    ow.connect = False if locked else True
    await ch.set_permissions(ch.guild.default_role, overwrite=ow)
    return "🔒 ล็อคห้องแล้ว" if locked else "🔓 เปิดห้องแล้ว (ทุกคนเข้าได้)"


async def act_hide(ch, hidden):
    ow = ch.overwrites_for(ch.guild.default_role)
    ow.view_channel = False if hidden else True
    await ch.set_permissions(ch.guild.default_role, overwrite=ow)
    return "🙈 ซ่อนห้องแล้ว" if hidden else "👁️ แสดงห้องให้ทุกคนเห็นแล้ว"


async def act_limit(ch, owner_id, n):
    n = max(0, min(99, n))
    await ch.edit(user_limit=n)
    store.profile(owner_id)["limit"] = n
    store.save()
    return f"👥 จำกัดจำนวนคน: {'ไม่จำกัด' if n == 0 else n}"


async def act_name(ch, owner_id, name):
    name = name.strip()[:100]
    await ch.edit(name=name)
    store.profile(owner_id)["name"] = name
    store.save()
    return f"✏️ เปลี่ยนชื่อห้องเป็น {name}"


async def act_status(ch, owner_id, text):
    try:
        await ch.edit(status=text[:500] or None)
    except Exception:
        return "❌ ตั้งสถานะไม่ได้ (ต้องใช้ discord.py 2.5 ขึ้นไป และบอทต้องมีสิทธิ์จัดการห้อง)"
    store.profile(owner_id)["status"] = text[:500]
    store.save()
    return f"💬 ตั้งสถานะห้องแล้ว: {text}"


def guard(owner_id, member):
    if member.id == owner_id:
        return "ทำกับเจ้าของห้องไม่ได้"
    if member.bot:
        return "ทำกับบอทไม่ได้"
    return None


async def act_grant(ch, owner_id, member):
    if (g := guard(owner_id, member)):
        return g
    prof = store.profile(owner_id)
    if member.id in prof["bans"]:
        prof["bans"].remove(member.id)
        store.save()
    await ch.set_permissions(member, view_channel=True, connect=True)
    return f"✅ อนุญาตให้ {member.mention} เห็นและเข้าห้องแล้ว"


async def act_kick(ch, owner_id, member):
    if (g := guard(owner_id, member)):
        return g
    await ch.set_permissions(member, connect=False)
    if member in ch.members:
        await member.move_to(None)
    return f"🚫 เตะ {member.mention} ออกและล็อคไม่ให้เข้าแล้ว (ไม่มีผลกับห้องอนาคต)"


async def act_ban(ch, owner_id, member):
    if (g := guard(owner_id, member)):
        return g
    prof = store.profile(owner_id)
    if member.id not in prof["bans"]:
        prof["bans"].append(member.id)
        store.save()
    await ch.set_permissions(member, view_channel=False, connect=False)
    if member in ch.members:
        await member.move_to(None)
    return f"⛔ แบน {member.mention} แล้ว (มีผลกับห้องอนาคตจนกว่าจะปลด)"


async def act_unban(ch, owner_id, member):
    prof = store.profile(owner_id)
    if member.id in prof["bans"]:
        prof["bans"].remove(member.id)
        store.save()
    await ch.set_permissions(member, overwrite=None)
    return f"♻️ ปลดแบน {member.mention} แล้ว"


def panel_embed(owner, ch):
    e = discord.Embed(title=f"🎶 ห้องของ {owner.display_name}", color=discord.Color.blurple())
    e.add_field(name="👑 เจ้าของ", value=owner.mention)
    e.add_field(name="🔐 การเข้าถึง", value="🔒 ล็อค" if is_locked(ch) else "🔓 ปลดล็อค")
    e.add_field(name="👀 การมองเห็น", value="🙈 ซ่อน" if is_hidden(ch) else "👁️ แสดง")
    e.add_field(name="👥 จำนวนคน", value=f"{len(ch.members)} / {ch.user_limit or 'ไม่จำกัด'}")
    e.set_image(url=owner.display_avatar.replace(size=512).url)
    e.set_footer(text="กดปุ่มด้านล่างเพื่อจัดการห้องของคุณ • เจ้าของห้องเท่านั้น")
    return e


# ----- ปุ่ม / Modal / เลือกคน -----
class LimitModal(discord.ui.Modal, title="จำกัดจำนวนคน"):
    n = discord.ui.TextInput(label="จำนวนคน (0=ไม่จำกัด สูงสุด 99)", max_length=2)

    def __init__(self, ch, owner_id):
        super().__init__()
        self.ch, self.owner_id = ch, owner_id

    async def on_submit(self, i: discord.Interaction):
        if not self.n.value.isdigit():
            return await i.response.send_message("ใส่เป็นตัวเลขเท่านั้น", ephemeral=True)
        await i.response.send_message(await act_limit(self.ch, self.owner_id, int(self.n.value)), ephemeral=True)


class NameModal(discord.ui.Modal, title="เปลี่ยนชื่อห้อง"):
    name = discord.ui.TextInput(label="ชื่อห้องใหม่", max_length=100)

    def __init__(self, ch, owner_id):
        super().__init__()
        self.ch, self.owner_id = ch, owner_id

    async def on_submit(self, i: discord.Interaction):
        await i.response.send_message(await act_name(self.ch, self.owner_id, self.name.value), ephemeral=True)


class PickView(discord.ui.View):
    def __init__(self, ch, owner_id, mode):
        super().__init__(timeout=60)
        self.ch, self.owner_id, self.mode = ch, owner_id, mode
        self.sel = discord.ui.UserSelect(placeholder="เลือกคน")
        self.sel.callback = self.cb
        self.add_item(self.sel)

    async def cb(self, i: discord.Interaction):
        m = i.guild.get_member(self.sel.values[0].id)
        if not m:
            return await i.response.edit_message(content="ไม่พบคนนี้ในเซิร์ฟเวอร์", view=None)
        fn = act_grant if self.mode == "grant" else act_kick
        await i.response.edit_message(content=await fn(self.ch, self.owner_id, m), view=None)


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, i: discord.Interaction):
        owner = store.rooms.get(i.channel.id)
        if owner is None:
            await i.response.send_message("ห้องนี้ไม่ใช่ห้องส่วนตัว", ephemeral=True)
            return False
        if owner != i.user.id:
            await i.response.send_message("เจ้าของห้องเท่านั้น", ephemeral=True)
            return False
        return True

    async def refresh(self, i):
        try:
            await i.message.edit(embed=panel_embed(i.user, i.channel))
        except Exception:
            pass

    @discord.ui.button(label="ล็อค/ปลด", emoji="🔐", style=discord.ButtonStyle.primary, custom_id="vr:lock", row=0)
    async def b_lock(self, i, b):
        msg = await act_lock(i.channel, not is_locked(i.channel))
        await i.response.send_message(msg, ephemeral=True)
        await self.refresh(i)

    @discord.ui.button(label="แสดง/ซ่อน", emoji="👁️", style=discord.ButtonStyle.primary, custom_id="vr:hide", row=0)
    async def b_hide(self, i, b):
        msg = await act_hide(i.channel, not is_hidden(i.channel))
        await i.response.send_message(msg, ephemeral=True)
        await self.refresh(i)

    @discord.ui.button(label="Limit", emoji="👥", style=discord.ButtonStyle.secondary, custom_id="vr:limit", row=0)
    async def b_limit(self, i, b):
        await i.response.send_modal(LimitModal(i.channel, i.user.id))

    @discord.ui.button(label="ชื่อห้อง", emoji="✏️", style=discord.ButtonStyle.secondary, custom_id="vr:name", row=0)
    async def b_name(self, i, b):
        await i.response.send_modal(NameModal(i.channel, i.user.id))

    @discord.ui.button(label="Grant", emoji="✅", style=discord.ButtonStyle.success, custom_id="vr:grant", row=1)
    async def b_grant(self, i, b):
        await i.response.send_message("เลือกคนที่จะอนุญาต", view=PickView(i.channel, i.user.id, "grant"), ephemeral=True)

    @discord.ui.button(label="Kick", emoji="🚫", style=discord.ButtonStyle.danger, custom_id="vr:kick", row=1)
    async def b_kick(self, i, b):
        await i.response.send_message("เลือกคนที่จะเตะ", view=PickView(i.channel, i.user.id, "kick"), ephemeral=True)

    @discord.ui.button(label="ลบห้อง", emoji="🗑️", style=discord.ButtonStyle.danger, custom_id="vr:delete", row=1)
    async def b_delete(self, i, b):
        store.rooms.pop(i.channel.id, None)
        store.save()
        await i.response.send_message("กำลังลบห้อง...", ephemeral=True)
        await i.channel.delete()


# ----- สร้าง / ลบห้องอัตโนมัติ -----
async def create_room(member):
    guild = member.guild
    lobby = guild.get_channel(store.config["lobby"])
    cat = guild.get_channel(store.config["category"]) if store.config["category"] else lobby.category
    prof = store.profile(member.id)
    ow = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),  # สร้างแบบล็อค+ซ่อนเสมอ
        member: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, stream=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True,
                                              move_members=True, manage_permissions=True),
    }
    for uid in prof["bans"]:
        m = guild.get_member(uid)
        if m:
            ow[m] = discord.PermissionOverwrite(view_channel=False, connect=False)
    ch = await guild.create_voice_channel(
        name=prof["name"] or f"ห้องของ {member.display_name}",
        category=cat, user_limit=prof["limit"], overwrites=ow)
    store.rooms[ch.id] = member.id
    store.save()
    weblog.log_store.add(str(member.guild.id),"room_create",
        actor_name=member.display_name, actor_id=str(member.id),
        actor_avatar=str(member.display_avatar.url),
        user_name=member.display_name, user_id=str(member.id),
        channel=ch.name, channel_id=str(ch.id),
        desc=f"สร้างห้อง {ch.name}")
    await member.move_to(ch)
    if prof["status"]:
        await act_status(ch, member.id, prof["status"])
    await ch.send(embed=panel_embed(member, ch), view=PanelView())



@bot.event
async def on_interaction(interaction: discord.Interaction):
    if interaction.type == discord.InteractionType.application_command:
        ch_name = getattr(interaction.channel, "name", "DM") if interaction.channel else "DM"
        cmd_name = interaction.data.get("name", "?")
        weblog.log_store.add(str(interaction.guild_id) if interaction.guild_id else "dm","cmd",
            actor_name=interaction.user.display_name,
            actor_id=str(interaction.user.id),
            actor_avatar=str(interaction.user.display_avatar.url),
            user_name=interaction.user.display_name,
            user_id=str(interaction.user.id),
            channel=ch_name,
            channel_id=str(interaction.channel_id or ""),
            desc=f"/{cmd_name}")
    await bot.process_application_commands(interaction)

@bot.event
async def on_voice_state_update(member, before, after):
    # ตรวจ server mute/deafen เปลี่ยนแปลง
    if before.channel or after.channel:
        if before.mute != after.mute:
            act = "server_mute" if after.mute else "server_unmute"
            auditor = await _fetch_auditor(member.guild,
                discord.AuditLogAction.member_update, member.id)
            sname = auditor.display_name if auditor else "ไม่ทราบ"
            sav = str(auditor.display_avatar.url) if auditor else None
            weblog.log_store.add(str(member.guild.id), act,
                actor_name=sname, actor_avatar=sav,
                target_name=member.display_name, target_id=str(member.id),
                target_avatar=str(member.display_avatar.url),
                user_name=member.display_name, user_id=str(member.id),
                channel=(after.channel or before.channel).name if (after.channel or before.channel) else None,
                desc=f"{'ปิด' if after.mute else 'เปิด'}ไมค์แดง {member.display_name} (โดย {sname})")
        if before.deaf != after.deaf:
            act2 = "server_deaf" if after.deaf else "server_undeaf"
            auditor2 = await _fetch_auditor(member.guild,
                discord.AuditLogAction.member_update, member.id)
            sname2 = auditor2.display_name if auditor2 else "ไม่ทราบ"
            sav2 = str(auditor2.display_avatar.url) if auditor2 else None
            weblog.log_store.add(str(member.guild.id), act2,
                actor_name=sname2, actor_avatar=sav2,
                target_name=member.display_name, target_id=str(member.id),
                target_avatar=str(member.display_avatar.url),
                user_name=member.display_name, user_id=str(member.id),
                channel=(after.channel or before.channel).name if (after.channel or before.channel) else None,
                desc=f"{'ปิด' if after.deaf else 'เปิด'}หู {member.display_name} (โดย {sname2})")

    if after.channel and store.config["lobby"] and after.channel.id == store.config["lobby"]:
        try:
            await create_room(member)
        except Exception as e:
            print("create_room error:", e)
    elif after.channel and before.channel is None:
        # join (not lobby)
        weblog.log_store.add(str(member.guild.id),"join",
            actor_name=member.display_name, actor_id=str(member.id),
            actor_avatar=str(member.display_avatar.url),
            user_name=member.display_name, user_id=str(member.id),
            channel=after.channel.name, channel_id=str(after.channel.id),
            member_count=len(after.channel.members),
            desc=f"เข้าห้อง {after.channel.name} ({len(after.channel.members)} คน)")
    elif before.channel and after.channel is None:
        # leave (non-private)
        if before.channel.id not in store.rooms:
            weblog.log_store.add(str(member.guild.id),"leave",
                actor_name=member.display_name, actor_id=str(member.id),
                actor_avatar=str(member.display_avatar.url),
                user_name=member.display_name, user_id=str(member.id),
                channel=before.channel.name, channel_id=str(before.channel.id),
                member_count=len(before.channel.members),
                desc=f"ออกจากห้อง {before.channel.name} ({len(before.channel.members)} คน)")
    elif before.channel and after.channel and before.channel != after.channel:
        if after.channel.id != store.config.get("lobby",0):
            weblog.log_store.add(str(member.guild.id),"move",
                actor_name=member.display_name, actor_id=str(member.id),
                actor_avatar=str(member.display_avatar.url),
                user_name=member.display_name, user_id=str(member.id),
                src_channel=before.channel.name, dst_channel=after.channel.name,
                channel=after.channel.name, channel_id=str(after.channel.id),
                member_count=len(after.channel.members),
                desc=f"ย้ายจาก {before.channel.name} → {after.channel.name}")
    if before.channel and before.channel.id in store.rooms and not before.channel.members:
        cname=before.channel.name
        store.rooms.pop(before.channel.id, None)
        store.save()
        weblog.log_store.add(str(before.channel.guild.id),"room_delete",
            channel=cname, channel_id=str(before.channel.id),
            desc=f"ลบห้อง {cname} (ว่าง)")
        try:
            await before.channel.delete()
        except Exception:
            pass



# ─── ตรวจ server mute / deafen รายคน ──────────────────────────────────────
_mute_cache: dict[int, bool] = {}
_deaf_cache: dict[int, bool] = {}


async def _fetch_auditor(guild, action, target_id):
    try:
        async for e in guild.audit_logs(limit=3, action=action):
            if e.target and e.target.id == target_id:
                return e.user
    except Exception:
        pass
    return None


# ─── ลบ / แก้ไข ข้อความ ────────────────────────────────────────────────────
@bot.event
async def on_message_delete(message):
    if message.author.bot or not message.guild:
        return
    att_url = None
    for a in message.attachments:
        if any(a.filename.lower().endswith(x) for x in (".png",".jpg",".jpeg",".gif",".webp")):
            att_url = weblog.cached_img_url(a.id, a.url); break
    weblog.log_store.add(str(message.guild.id), "msg_delete",
        actor_name=message.author.display_name,
        actor_id=str(message.author.id),
        actor_avatar=str(message.author.display_avatar.url),
        user_name=message.author.display_name,
        user_id=str(message.author.id),
        channel=message.channel.name,
        channel_id=str(message.channel.id),
        content=message.content[:1000] if message.content else None,
        attachment_url=att_url,
        desc=f"ลบข้อความใน #{message.channel.name}")


@bot.event
async def on_message_edit(before, after):
    if before.author.bot or not before.guild:
        return
    if before.content == after.content:
        return
    weblog.log_store.add(str(before.guild.id), "msg_edit",
        actor_name=before.author.display_name,
        actor_id=str(before.author.id),
        actor_avatar=str(before.author.display_avatar.url),
        user_name=before.author.display_name,
        user_id=str(before.author.id),
        channel=before.channel.name,
        channel_id=str(before.channel.id),
        before=before.content[:800] if before.content else None,
        after=after.content[:800] if after.content else None,
        desc=f"แก้ไขข้อความใน #{before.channel.name}")


@bot.event
async def on_ready():
    for cid in list(store.rooms):
        ch = bot.get_channel(cid)
        if ch is None:
            store.rooms.pop(cid)
        elif not ch.members:
            store.rooms.pop(cid)
            try:
                await ch.delete()
            except Exception:
                pass
    store.save()
    await bot.change_presence(activity=discord.Streaming(name=STREAM_TEXT, url=STREAM_URL))
    for g in bot.guilds:
        weblog.set_server(str(g.id), g.name, str(g.icon.url) if g.icon else None)
    print(f"พร้อมใช้งาน: {bot.user}")


# ----- คำสั่งข้อความ (prefix) -----
async def owned_room(ctx):
    v = ctx.author.voice
    if not v or not v.channel or store.rooms.get(v.channel.id) != ctx.author.id:
        await ctx.send("ต้องอยู่ในห้องส่วนตัวของคุณเองก่อน")
        return None
    return v.channel


def room_cmd(*names):
    def deco(fn):
        return bot.hybrid_command(name=names[0], aliases=list(names[1:]),
                                  description=f"คำสั่งห้องส่วนตัว: {names[0]}")(fn)
    return deco


@room_cmd("vpermit", "vgrant")
async def c_grant(ctx, member: discord.Member):
    if ch := await owned_room(ctx):
        await ctx.send(await act_grant(ch, ctx.author.id, member))


@room_cmd("vkick")
async def c_kick(ctx, member: discord.Member):
    if ch := await owned_room(ctx):
        await ctx.send(await act_kick(ch, ctx.author.id, member))


@room_cmd("vban")
async def c_ban(ctx, member: discord.Member):
    if ch := await owned_room(ctx):
        await ctx.send(await act_ban(ch, ctx.author.id, member))


@room_cmd("vunban")
async def c_unban(ctx, member: discord.Member):
    if ch := await owned_room(ctx):
        await ctx.send(await act_unban(ch, ctx.author.id, member))


@room_cmd("vbanlist")
async def c_banlist(ctx):
    bans = store.profile(ctx.author.id)["bans"]
    await ctx.send("⛔ รายชื่อที่แบน:\n" + ("\n".join(f"• <@{u}> | {u}" for u in bans) if bans else "ไม่มี"))


@room_cmd("vlock")
async def c_lock(ctx):
    if ch := await owned_room(ctx):
        await ctx.send(await act_lock(ch, True))


@room_cmd("vunlock")
async def c_unlock(ctx):
    if ch := await owned_room(ctx):
        await ctx.send(await act_lock(ch, False))


@room_cmd("vghost", "vhide")
async def c_hide(ctx):
    if ch := await owned_room(ctx):
        await ctx.send(await act_hide(ch, True))


@room_cmd("vunghost", "vunhide", "vshow")
async def c_show(ctx):
    if ch := await owned_room(ctx):
        await ctx.send(await act_hide(ch, False))


@room_cmd("vlimit")
async def c_limit(ctx, n: int):
    if ch := await owned_room(ctx):
        await ctx.send(await act_limit(ch, ctx.author.id, n))


@room_cmd("vname")
async def c_name(ctx, *, name: str):
    if ch := await owned_room(ctx):
        await ctx.send(await act_name(ch, ctx.author.id, name))


@room_cmd("vstatus")
async def c_status(ctx, *, text: str):
    if ch := await owned_room(ctx):
        await ctx.send(await act_status(ch, ctx.author.id, text))






# ----- Recall: เมนูเลือกห้อง (slash) / ไอดีตรง (prefix) -----
async def _do_move(guild, src, dst, ctx_or_interaction):
    """ย้ายทุกคนจาก src → dst แล้ว return embed รายงาน"""
    import time as _time
    t0 = _time.perf_counter()
    ok, protected = [], []
    for m in list(src.members):
        if m.bot:
            continue
        (protected if has_protected(m) else ok).append(m)
    results = await asyncio.gather(*[m.move_to(dst) for m in ok], return_exceptions=True)
    moved  = [m for m, r in zip(ok, results) if not isinstance(r, Exception)]
    failed = [m for m, r in zip(ok, results) if     isinstance(r, Exception)]
    weblog.log_store.add(str(guild.id),"recall",
        src_channel=src.name, dst_channel=dst.name,
        channel=src.name, channel_id=str(src.id),
        count=len(moved),
        members=[{"id":str(m.id),"name":m.display_name,
                  "avatar":str(m.display_avatar.url)} for m in moved],
        desc=f"Recall: {src.name} → {dst.name} (ย้าย {len(moved)} คน)")
    secs = _time.perf_counter() - t0
    moved_lines = "\n".join(f"• {m.mention} | **{m.display_name}** | {m.id}" for m in moved)
    prot_lines  = "\n".join(f"• {m.display_name}" for m in protected)
    parts = [
        f"📢 ห้องต้นทาง\n{src.mention}  ( {src.id} )",
        f"📥 ห้องปลายทาง\n{dst.mention}  ( {dst.id} )",
        (f"✅ **ย้ายสำเร็จ ({len(moved)} คน)**\n{moved_lines}" if moved else "✅ ไม่มีคนให้ย้าย"),
    ]
    if protected:
        parts.append(f"🛡️ **ข้าม (มี role) ({len(protected)} คน)**\n{prot_lines}")
    if failed:
        parts.append(f"❌ **ไม่สำเร็จ ({len(failed)} คน)**")
    embed = discord.Embed(
        title="📣 รายงาน Recall",
        description="\n\n".join(parts)[:4000],
        color=discord.Color.yellow(),
    )
    embed.set_footer(text=f"⚡ เวลาที่ใช้: {secs:.2f} วินาที")
    return embed


class MoveView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.src_id: int = 0
        self.dst_id: int = 0

        src_sel = discord.ui.ChannelSelect(
            placeholder="📢 เลือกห้องต้นทาง",
            channel_types=[discord.ChannelType.voice],
            row=0,
        )
        dst_sel = discord.ui.ChannelSelect(
            placeholder="📥 เลือกห้องปลายทาง",
            channel_types=[discord.ChannelType.voice],
            row=1,
        )

        async def pick_src(i: discord.Interaction):
            self.src_id = src_sel.values[0].id
            await i.response.defer()

        async def pick_dst(i: discord.Interaction):
            self.dst_id = dst_sel.values[0].id
            await i.response.defer()

        src_sel.callback = pick_src
        dst_sel.callback = pick_dst
        self.add_item(src_sel)
        self.add_item(dst_sel)

    @discord.ui.button(label="✅ ยืนยันย้าย", style=discord.ButtonStyle.success, row=2)
    async def confirm(self, i: discord.Interaction, b: discord.ui.Button):
        if not self.src_id or not self.dst_id:
            return await i.response.send_message("เลือกห้องทั้งสองก่อน", ephemeral=True)
        src = i.guild.get_channel(self.src_id)
        dst = i.guild.get_channel(self.dst_id)
        if src is None or dst is None:
            return await i.response.send_message("ไม่พบห้องที่เลือก", ephemeral=True)
        await i.response.defer(ephemeral=True)
        embed = await _do_move(i.guild, src, dst, i)
        # ปิด picker ให้คนใช้เห็นว่าสำเร็จ
        await i.edit_original_response(content="✅ ดำเนินการแล้ว", view=None)
        # ส่งรายงานให้ทุกคนเห็น
        await i.channel.send(embed=embed)
        self.stop()

    @discord.ui.button(label="❌ ยกเลิก", style=discord.ButtonStyle.secondary, row=2)
    async def cancel(self, i: discord.Interaction, b: discord.ui.Button):
        await i.response.edit_message(content="ยกเลิกแล้ว", view=None)
        self.stop()


@bot.command(name="ย้าย")
@is_staff()
@commands.guild_only()
async def move_prefix(ctx, from_id: str, _to: str = None, to_id: str = None):
    """!ย้าย <ไอดีต้นทาง> to <ไอดีปลายทาง>"""
    if to_id is None:
        to_id = _to if (_to and _to.isdigit()) else None
    if not (from_id and to_id and from_id.isdigit() and to_id.isdigit()):
        return await ctx.send("รูปแบบ: `!ย้าย <ไอดีห้องต้นทาง> to <ไอดีห้องปลายทาง>`")
    src = ctx.guild.get_channel(int(from_id))
    dst = ctx.guild.get_channel(int(to_id))
    if src is None: return await ctx.send(f"ไม่พบห้องต้นทาง `{from_id}`")
    if dst is None: return await ctx.send(f"ไม่พบห้องปลายทาง `{to_id}`")
    if not isinstance(src, discord.VoiceChannel) or not isinstance(dst, discord.VoiceChannel):
        return await ctx.send("ต้องเป็นห้องเสียงเท่านั้น")
    embed = await _do_move(ctx.guild, src, dst, ctx)
    await ctx.send(embed=embed)


@bot.tree.command(name="ย้าย", description="ย้ายทุกคนจากห้องเสียงต้นทางไปปลายทาง (เฉพาะทีมงาน)")
@commands.guild_only()
async def move_slash(i: discord.Interaction):
    if not (i.user.guild_permissions.administrator or
            any(r.id in store.config["staff_roles"] for r in i.user.roles)):
        return await i.response.send_message("คุณไม่มีสิทธิ์ใช้คำสั่งนี้", ephemeral=True)
    await i.response.send_message(
        "เลือกห้องต้นทางและปลายทาง แล้วกดยืนยัน",
        view=MoveView(),
        ephemeral=True,
    )




# ─── บันทึก ban / timeout / คำสั่ง ────────────────────────────────────────
@bot.event
async def on_member_ban(guild, user):
    staff_name = "ไม่ทราบ"
    try:
        async for e in guild.audit_logs(limit=3, action=discord.AuditLogAction.ban):
            if e.target and e.target.id == user.id:
                staff_name = e.user.display_name
                break
    except Exception:
        pass
    uname=getattr(user,"display_name",user.name)
    weblog.log_store.add(str(guild.id),"ban",
        actor_name=staff_name, target_name=uname, target_id=str(user.id),
        user_name=uname, user_id=str(user.id),
        desc=f"แบน {uname} (โดย {staff_name})")


@bot.event
async def on_member_unban(guild, user):
    staff_name = "ไม่ทราบ"
    try:
        async for e in guild.audit_logs(limit=3, action=discord.AuditLogAction.unban):
            if e.target and e.target.id == user.id:
                staff_name = e.user.display_name
                break
    except Exception:
        pass
    uname2=getattr(user,"display_name",user.name)
    weblog.log_store.add(str(guild.id),"unban",
        actor_name=staff_name, target_name=uname2, target_id=str(user.id),
        user_name=uname2, user_id=str(user.id),
        desc=f"ปลดแบน {uname2} (โดย {staff_name})")


@bot.event
async def on_member_update(before, after):
    # ── role changes ──
    added = set(after.roles) - set(before.roles)
    removed = set(before.roles) - set(after.roles)
    if added or removed:
        auditor, av_url = None, None
        try:
            async for e in after.guild.audit_logs(limit=3, action=discord.AuditLogAction.member_role_update):
                if e.target and e.target.id == after.id:
                    auditor = e.user; av_url = str(e.user.display_avatar.url); break
        except Exception:
            pass
        sname = auditor.display_name if auditor else "ไม่ทราบ"
        for r in added:
            weblog.log_store.add(str(after.guild.id),"role_add",
                actor_name=sname, actor_avatar=av_url,
                target_name=after.display_name, target_id=str(after.id),
                target_avatar=str(after.display_avatar.url),
                user_name=after.display_name, user_id=str(after.id),
                desc=f"ให้ยศ @{r.name} แก่ {after.display_name} (โดย {sname})")
        for r in removed:
            weblog.log_store.add(str(after.guild.id),"role_remove",
                actor_name=sname, actor_avatar=av_url,
                target_name=after.display_name, target_id=str(after.id),
                target_avatar=str(after.display_avatar.url),
                user_name=after.display_name, user_id=str(after.id),
                desc=f"เอาคืนยศ @{r.name} จาก {after.display_name} (โดย {sname})")
    # ── nickname ──
    if before.nick != after.nick:
        weblog.log_store.add(str(after.guild.id),"nick_change",
            actor_name=after.display_name, actor_id=str(after.id),
            actor_avatar=str(after.display_avatar.url),
            user_name=after.display_name, user_id=str(after.id),
            before=before.nick or before.name,
            after=after.nick or after.name,
            desc=f"เปลี่ยนชื่อเล่น: {before.nick or before.name} → {after.nick or after.name}")
    if before.timed_out_until == after.timed_out_until:
        return
    if after.timed_out_until:
        staff_name = "ไม่ทราบ"
        try:
            async for e in after.guild.audit_logs(limit=3, action=discord.AuditLogAction.member_update):
                if e.target and e.target.id == after.id:
                    staff_name = e.user.display_name
                    break
        except Exception:
            pass
        until = after.timed_out_until.strftime("%d/%m %H:%M")
        weblog.log_store.add(str(after.guild.id),"timeout",
            actor_name=staff_name,
            actor_avatar=str(after.guild.get_member_named(staff_name).display_avatar.url) if after.guild.get_member_named(staff_name) else None,
            target_name=after.display_name, target_id=str(after.id),
            target_avatar=str(after.display_avatar.url),
            user_name=after.display_name, user_id=str(after.id),
            staff_name=staff_name,
            desc=f"หมดเวลา {after.display_name} จนถึง {until} (โดย {staff_name})")
    else:
        weblog.log_store.add(str(after.guild.id),"untimeout",
            target_name=after.display_name, target_id=str(after.id),
            target_avatar=str(after.display_avatar.url),
            user_name=after.display_name, user_id=str(after.id),
            desc=f"ยกเลิกหมดเวลา {after.display_name}")


@bot.before_invoke
async def log_cmd(ctx):
    ch_name = getattr(ctx.channel, "name", "DM")
    weblog.log_store.add(str(ctx.guild.id) if ctx.guild else "dm","cmd",
        actor_name=ctx.author.display_name, actor_id=str(ctx.author.id),
        actor_avatar=str(ctx.author.display_avatar.url),
        user_name=ctx.author.display_name, user_id=str(ctx.author.id),
        channel=ch_name, channel_id=str(ctx.channel.id),
        desc=f"!{ctx.command.qualified_name}")




@bot.event
async def on_command_error(ctx, err):
    if isinstance(err, (commands.CheckFailure, commands.MissingPermissions)):
        return await ctx.send("คุณไม่มีสิทธิ์ใช้คำสั่งนี้")
    if isinstance(err, (commands.MissingRequiredArgument, commands.BadArgument)):
        return await ctx.send("ใส่ข้อมูลไม่ครบหรือไม่ถูกต้อง เช่น `!vgrant @เพื่อน`")
    print("error:", err)



# ─── pre-cache ภาพก่อนโดนลบ ────────────────────────────────────────────────
@bot.event
async def on_message(message):
    if message.attachments and message.guild:
        for att in message.attachments:
            if any(att.filename.lower().endswith(x) for x in (".png",".jpg",".jpeg",".gif",".webp")):
                asyncio.create_task(weblog.download_image(att.id, att.url))
    await bot.process_commands(message)


# ─── เข้า / ออกเซิร์ฟ ──────────────────────────────────────────────────────
@bot.event
async def on_member_join(member):
    weblog.log_store.add(str(member.guild.id), "member_join",
        actor_name=member.display_name, actor_id=str(member.id),
        actor_avatar=str(member.display_avatar.url),
        user_name=member.display_name, user_id=str(member.id),
        desc=f"{member.display_name} เข้าเซิร์ฟเวอร์ ({member.guild.member_count} คน)")


@bot.event
async def on_member_remove(member):
    # ตรวจว่าถูกเตะหรือออกเอง
    action, staff_name, staff_av = "member_leave", None, None
    try:
        async for e in member.guild.audit_logs(limit=3, action=discord.AuditLogAction.kick):
            if e.target and e.target.id == member.id and time.time()-e.created_at.timestamp()<5:
                action = "member_kick"
                staff_name = e.user.display_name
                staff_av = str(e.user.display_avatar.url)
                break
    except Exception:
        pass
    weblog.log_store.add(str(member.guild.id), action,
        actor_name=staff_name or member.display_name,
        actor_avatar=staff_av or str(member.display_avatar.url),
        target_name=member.display_name if staff_name else None,
        target_id=str(member.id) if staff_name else None,
        target_avatar=str(member.display_avatar.url) if staff_name else None,
        user_name=member.display_name, user_id=str(member.id),
        desc=(f"เตะ {member.display_name} (โดย {staff_name})"
              if action=="member_kick" else f"{member.display_name} ออกเซิร์ฟเวอร์"))


# ─── ยศ / ชื่อเล่นเปลี่ยน ────────────────────────────────────────────────────
@bot.event
async def on_member_role_change(before_m, after_m):
    pass  # ใช้ on_member_update แทน — placeholder


# ─── channel create / delete ────────────────────────────────────────────────
@bot.event
async def on_guild_channel_create(channel):
    auditor, av_url = None, None
    try:
        async for e in channel.guild.audit_logs(limit=2, action=discord.AuditLogAction.channel_create):
            if e.target and e.target.id == channel.id:
                auditor = e.user; av_url = str(e.user.display_avatar.url); break
    except Exception:
        pass
    kind = {"text":"ข้อความ","voice":"เสียง","category":"หมวดหมู่"}.get(str(channel.type).split(".")[-1],"")
    weblog.log_store.add(str(channel.guild.id), "ch_create",
        actor_name=auditor.display_name if auditor else "ไม่ทราบ",
        actor_avatar=av_url,
        channel=channel.name,
        desc=f"สร้างช่อง{kind} #{channel.name}")


@bot.event
async def on_guild_channel_delete(channel):
    auditor, av_url = None, None
    try:
        async for e in channel.guild.audit_logs(limit=2, action=discord.AuditLogAction.channel_delete):
            if e.target and e.target.id == channel.id:
                auditor = e.user; av_url = str(e.user.display_avatar.url); break
    except Exception:
        pass
    weblog.log_store.add(str(channel.guild.id), "ch_delete",
        actor_name=auditor.display_name if auditor else "ไม่ทราบ",
        actor_avatar=av_url,
        channel=channel.name,
        desc=f"ลบช่อง #{channel.name}")


@bot.event
async def on_guild_join(guild):
    weblog.set_server(str(guild.id), guild.name, str(guild.icon.url) if guild.icon else None)


@bot.event
async def on_guild_remove(guild):
    weblog.remove_server(str(guild.id))


bot.run(TOKEN)
