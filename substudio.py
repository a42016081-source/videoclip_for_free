"""Subtitle Studio: inline-keyboard UI to control every subtitle option, with a live preview image."""
import logging
import shutil
import uuid
from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup as Markup
from config import TMP_DIR
from database import get_user, set_user
import subtitles as S

log = logging.getLogger(__name__)

# key -> (menu label, picker title)
FIELDS = [
    ("style", "🎨 Style"), ("font", "🔤 Font"),
    ("size", "📏 Size"), ("pos", "📍 Position"),
    ("words", "🔢 Words/line"), ("case", "Aa Case"),
    ("hi", "🌈 Highlight"), ("color", "🎯 Text color"),
    ("outline", "🖊 Outline"), ("bg", "🔲 Background"),
    ("anim", "✨ Animation"),
]
_LABELS = {
    "size": {"s": "S (small)", "m": "M", "l": "L", "xl": "XL (huge)"},
    "pos": {"top": "⬆️ Top", "middle": "⏺ Middle", "safe": "🛡 Safe (above Shorts UI)", "bottom": "⬇️ Bottom"},
    "case": {"normal": "Normal", "upper": "UPPERCASE", "lower": "lowercase", "title": "Title Case"},
    "outline": {"thin": "Thin", "normal": "Normal", "thick": "Thick", "xthick": "Extra thick"},
    "bg": {"none": "None", "shadow": "Shadow", "box": "Dark box"},
    "anim": {"none": "None", "pop": "Pop-in", "fade": "Fade-in"},
}


def _choices(key):
    if key == "style":
        return list(S.STYLE_NAMES)
    if key == "font":
        return ["auto", *S.available_fonts()]
    if key == "size":
        return ["auto", *S.SIZES]
    if key == "pos":
        return ["auto", *S.POSITIONS]
    if key == "words":
        return ["auto", *[str(n) for n in S.WORDS]]
    if key == "case":
        return ["auto", *S.CASES]
    if key == "hi":
        return ["auto", "video", *[c for c in S.COLORS if c != "black"]]     # "video" = colours picked from the video
    if key == "color":
        return ["auto", *[c for c in S.COLORS if c != "black"]]
    if key == "outline":
        return ["auto", *S.OUTLINES]
    if key == "bg":
        return ["auto", *S.BGS]
    if key == "anim":
        return ["auto", *S.ANIMS]
    return []


def _pretty(key, val) -> str:
    if val == "auto":
        return "auto (style default)"
    if key == "style":
        return S.PRESETS[val]["label"]
    if val == "video":
        return "🎬 From video (auto colours)"
    if key in ("hi", "color"):
        return f"{S.COLOR_EMOJI.get(val, '')} {val}"
    return _LABELS.get(key, {}).get(val, str(val))


def _current(user, key):
    if key == "style":
        return user.get("sub_style") or "classic"
    return str(S.load_cfg(user.get("sub_cfg")).get(key) or "auto")


def _short(key, val) -> str:
    if val == "auto":
        return "auto"
    if key == "style":
        return S.PRESETS[val]["label"].split(" (")[0]
    if val == "video":
        return "🎬video"
    if key in ("hi", "color"):
        return f"{S.COLOR_EMOJI.get(val, '')}{val}"
    return _LABELS.get(key, {}).get(val, val).split(" (")[0]


def _text(user) -> str:
    style = _current(user, "style")
    eff = S.resolve(style, user.get("sub_cfg"), True)
    return (f"🎨 Subtitle Studio\n\n"
            f"Style: {S.PRESETS[style]['label']}\n"
            f"Now: {eff['font']} · {eff['pos']} · {eff['words']} word(s) · {eff['case']} · "
            f"text {eff['color']} / highlight {'🎬 from video' if eff['auto'] else eff['hi']}\n"
            f"Subtitles: {'ON ✅' if user.get('subs') else 'OFF ❌ (turn ON to burn them in)'}\n\n"
            "Pick any option below. \"auto\" = the style's own default. Use 👁 Preview to see it.")


def main_kb(user) -> Markup:
    rows, row = [], []
    for key, label in FIELDS:
        row.append(Btn(f"{label}: {_short(key, _current(user, key))}"[:40], callback_data=f"sub:menu:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([Btn(f"💬 Subs: {'ON' if user.get('subs') else 'OFF'}", callback_data="sub:tsubs"),
                 Btn(f"📱 Shorts: {'ON' if user.get('shorts') else 'OFF'}", callback_data="sub:tshorts")])
    rows.append([Btn("👁 Preview", callback_data="sub:preview"), Btn("♻️ Reset", callback_data="sub:reset")])
    rows.append([Btn("✖ Close", callback_data="conf:cancel")])
    return Markup(rows)


def picker_kb(user, key) -> Markup:
    cur = _current(user, key)
    opts = _choices(key)
    per_row = 2 if key in ("style", "pos", "font") else 3
    rows, row = [], []
    for v in opts:
        mark = "✅ " if v == cur else ""
        label = S.PRESETS[v]["label"] if key == "style" else _pretty(key, v).replace(" (style default)", "")
        row.append(Btn((mark + label)[:30], callback_data=f"sub:set:{key}:{v}"))
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([Btn("⬅️ Back", callback_data="sub:main")])
    return Markup(rows)


async def open_studio(message, uid):
    user = get_user(uid)
    await message.reply_text(_text(user), reply_markup=main_kb(user))


async def _show(q, user, picker=None):
    text = _text(user) if picker is None else dict(FIELDS)[picker] + " — choose one:"
    kb = main_kb(user) if picker is None else picker_kb(user, picker)
    try:
        await q.edit_message_text(text, reply_markup=kb)
    except Exception:                      # "message is not modified" etc.
        pass


async def _preview(q, uid):
    user = get_user(uid)
    d = TMP_DIR / ("prev_" + uuid.uuid4().hex)
    d.mkdir(parents=True, exist_ok=True)
    try:
        style = _current(user, "style")
        png = await S.render_preview(d / "p.png", style, user.get("sub_cfg"), vertical=bool(user.get("shorts")))
        with open(png, "rb") as f:
            await q.message.reply_photo(f, caption=f"👁 {S.PRESETS[style]['label']} — sample "
                                                    f"({'9:16' if user.get('shorts') else '16:9'})")
    except Exception as e:
        log.warning("preview failed: %s", e)
        await q.message.reply_text(f"❌ Preview nahi ban paya: {str(e)[:200]}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def studio_callback(update, ctx):
    """Handles every callback starting with `sub:`."""
    q = update.callback_query
    uid = update.effective_user.id
    parts = (q.data or "").split(":")
    act = parts[1] if len(parts) > 1 else ""
    user = get_user(uid)

    if act == "open":
        return await q.message.reply_text(_text(user), reply_markup=main_kb(user))
    if act == "main":
        return await _show(q, user)
    if act == "menu" and len(parts) == 3 and parts[2] in dict(FIELDS):
        return await _show(q, user, parts[2])
    if act == "set" and len(parts) == 4 and parts[2] in dict(FIELDS):
        key, val = parts[2], parts[3]
        if val not in _choices(key):
            return
        if key == "style":
            set_user(uid, sub_style=val)
        else:
            cfg = S.load_cfg(user.get("sub_cfg"))
            if val == "auto":
                cfg.pop(key, None)
            else:
                cfg[key] = val
            set_user(uid, sub_cfg=S.dump_cfg(cfg))
        return await _show(q, get_user(uid))
    if act in ("tsubs", "tshorts"):
        col = "subs" if act == "tsubs" else "shorts"
        set_user(uid, **{col: 0 if user.get(col) else 1})
        return await _show(q, get_user(uid))
    if act == "reset":
        set_user(uid, sub_cfg="")
        return await _show(q, get_user(uid))
    if act == "preview":
        return await _preview(q, uid)
