from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton

MAIN = [
    ["✂️ Cut Video", "🎵 Audio Only"],
    ["📱 Make Short (9:16)", "🔴 Live Stream Clip"],
    ["🔥 Auto Highlights", "📑 Chapter Cut"],
    ["🔍 Keyword Search", "📥 Download Full"],
    ["📜 My History", "⭐ Favorites"],
    ["🔗 My Links", "⚙️ Settings"],
    ["📊 My Stats", "❓ Help"],
    ["🏓 Ping/Status", "🎨 Subtitle Studio"],
    ["📤 Upload Video + Subs"],
]
CANCEL_KB = ReplyKeyboardMarkup([["❌ Cancel"]], resize_keyboard=True)
def main_kb(): return ReplyKeyboardMarkup(MAIN, resize_keyboard=True)

def preview_kb(quality, user):
    rows = []
    qs = [("360", "360p"), ("480", "480p"), ("720", "720p"),
          ("1080", "1080p"), ("best", "🏆 Best"), ("audio", "🎵 MP3")]
    rows.append([InlineKeyboardButton(("✅ " if q == quality else "") + label,
                                       callback_data=f"q:{q}") for q, label in qs[:2]])
    rows.append([InlineKeyboardButton(("✅ " if q == quality else "") + label,
                                       callback_data=f"q:{q}") for q, label in qs[2:4]])
    rows.append([InlineKeyboardButton(("✅ " if q == quality else "") + label,
                                       callback_data=f"q:{q}") for q, label in qs[4:]])
    rows.append([InlineKeyboardButton(f"📱 Shorts: {'ON' if user['shorts'] else 'OFF'}", callback_data="toggle:shorts"),
                 InlineKeyboardButton(f"💬 Subs: {'ON' if user['subs'] else 'OFF'}", callback_data="toggle:subs")])
    rows.append([InlineKeyboardButton("✅ Start Job", callback_data="conf:start"),
                 InlineKeyboardButton("❌ Cancel", callback_data="conf:cancel")])
    return InlineKeyboardMarkup(rows)

def settings_kb(user):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Quality: {user['quality']}", callback_data="set:quality:cycle")],
        [InlineKeyboardButton(f"Delivery: {user['delivery']}", callback_data="set:delivery:cycle")],
        [InlineKeyboardButton(f"Shorts: {'ON' if user['shorts'] else 'OFF'}", callback_data="toggle:shorts"),
         InlineKeyboardButton(f"Subs: {'ON' if user['subs'] else 'OFF'}", callback_data="toggle:subs")],
        [InlineKeyboardButton(f"Reframe: {user['reframe']}", callback_data="set:reframe:cycle"),
         InlineKeyboardButton(f"SubStyle: {user['sub_style']}", callback_data="set:sub_style:cycle")],
        [InlineKeyboardButton(f"SubLang: {user['sub_lang']}", callback_data="set:sub_lang:cycle"),
         InlineKeyboardButton(f"Lang: {user['bot_lang']}", callback_data="set:bot_lang:cycle")],
        [InlineKeyboardButton("🎨 Subtitle Studio (fonts, colours, word highlight…)", callback_data="sub:open")],
        [InlineKeyboardButton("❌ Close", callback_data="conf:cancel")],
    ])

def reframe_kb(current):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(("✅ " if current == x else "") + lbl, callback_data=f"set:reframe:{x}")
         for x, lbl in [("blur", "🌫️ Blur"), ("crop", "✂️ Crop"), ("zoom", "🔍 Zoom")]],
    ])

def subs_kb(current):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(("✅ " if current == x else "") + lbl, callback_data=f"set:subs:{x}")
         for x, lbl in [("off", "Off"), ("classic", "Classic"), ("bold", "Bold Yellow"), ("minimal", "Minimal")]],
    ])

def history_kb(rows):
    buttons = []
    for r in rows[:10]:
        buttons.append([InlineKeyboardButton(
            f"🔁 Re-cut {r['start']}s→{r['end']}s · {r['quality']}", callback_data=f"re:{r['id']}")])
    return InlineKeyboardMarkup(buttons) if buttons else None


def after_clip_kb(hid, video=True, has_srt=False):
    """Buttons shown under every finished clip."""
    rows = [[InlineKeyboardButton("⭐ Favorite", callback_data=f"fav:{hid}"),
             InlineKeyboardButton("📋 Metadata", callback_data=f"meta:{hid}")]]
    if video:
        rows.append([InlineKeyboardButton("🐢 0.5x", callback_data=f"spd:{hid}:0.5"),
                     InlineKeyboardButton("⚡ 1.5x", callback_data=f"spd:{hid}:1.5"),
                     InlineKeyboardButton("🚀 2x", callback_data=f"spd:{hid}:2.0")])
    if has_srt:
        rows.append([InlineKeyboardButton("📄 Export .srt", callback_data=f"srt:{hid}")])
    return InlineKeyboardMarkup(rows)