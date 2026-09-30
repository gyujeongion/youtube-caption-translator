"""Control per-locale title display: keep the default title as-is, and add
localized titles so only viewers of that locale see a different title.

    python3 set_localization.py --video <videoId> \
        --title en="Shooting a DJ Set on Top of Seoul (ENG SUB)" \
        --title ja="ソウルの屋上でDJセットを撮影 (日本語字幕)" \
        [--token my_channel_token.json]

    python3 set_localization.py --video <videoId> --locs locs.json   # many languages at once
        # locs.json = {"ja": {"title": "...", "description": "..."}, "es": {"title": "..."}}

    python3 set_localization.py --video <videoId> --en-title "..."   # alias for --title en="..."
    python3 set_localization.py --video <videoId> --show             # inspect current state only

--title LANG=TEXT is repeatable (one per language). --token is OPTIONAL: it
defaults to the prefs `token_file`, else my_channel_token.json.

Default behavior:
- Corrects `defaultLanguage` to the original spoken language (--original-language,
  default: prefs `source_language`, else ko).
  If a channel's settings have defaultLanguage set to en while the actual
  title is Korean, "en locale" gets misread as "defaultLanguage" and English
  viewers end up seeing the original Korean title instead of the localized
  one -- the opposite of what you want. Use --keep-default-language to skip
  this correction if you're sure defaultLanguage is already set correctly.
- Does NOT auto-append any suffix (like "(ENG SUB)") to a localized title --
  you decide the exact wording and pass it in whole, since title tone is a
  judgment call this script shouldn't make for you.
- Leaves descriptions untouched by default. --description LANG=TEXT (repeatable),
  a "description" key in --locs, or --en-description replace that locale's
  description only.

Stdlib only, no external packages. --token follows the same convention as
upload_caption.py (bare filename resolved against $YTCAPTION_HOME or ~/.claude/credentials/,
or an absolute path).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402
from upload_caption import access_token, raise_for_http  # noqa: E402  (reuse auth logic from the same folder)

API = "https://www.googleapis.com/youtube/v3/videos"


def get_video(at: str, video_id: str) -> dict:
    u = f"{API}?" + urllib.parse.urlencode(
        {"part": "snippet,localizations", "id": video_id})
    req = urllib.request.Request(u, headers={"Authorization": f"Bearer {at}"})
    try:
        items = json.load(urllib.request.urlopen(req, timeout=60))["items"]
    except urllib.error.HTTPError as e:
        raise_for_http(e, "Video lookup", 300)
        raise  # unreachable
    if not items:
        raise SystemExit(f"Video not found: {video_id}")
    return items[0]


def put_video(at: str, body: dict) -> dict:
    u = f"{API}?" + urllib.parse.urlencode({"part": "snippet,localizations"})
    req = urllib.request.Request(
        u, data=json.dumps(body).encode(), method="PUT",
        headers={"Authorization": f"Bearer {at}", "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        raise_for_http(e, "Update", 2000)
        raise  # unreachable


def parse_titles(pairs: list[str], en_title: str | None) -> dict[str, str]:
    """['en=Title', 'ja=...'] (+ optional --en-title alias) -> {lang: title}. Raises ValueError."""
    out: dict[str, str] = {}
    items = list(pairs or [])
    if en_title:
        items.append(f"en={en_title}")
    for item in items:
        lang, sep, text = item.partition("=")
        lang, text = lang.strip(), text.strip()
        if not sep or not text:
            raise ValueError(f"--title expects LANG=TEXT, got: {item!r}")
        lang = C.validate_pref("source_language", lang)  # language-code check
        if lang in out:
            raise ValueError(f"title for '{lang}' given more than once")
        out[lang] = text
    return out


def parse_descriptions(pairs: list[str]) -> dict[str, str]:
    """['ja=...'] -> {lang: description}. Raises ValueError."""
    out: dict[str, str] = {}
    for item in pairs or []:
        lang, sep, text = item.partition("=")
        lang, text = lang.strip(), text.replace("\\n", "\n").strip()
        if not sep or not text:
            raise ValueError(f"--description expects LANG=TEXT, got: {item!r}")
        lang = C.validate_pref("source_language", lang)
        if lang in out:
            raise ValueError(f"description for '{lang}' given more than once")
        out[lang] = text
    return out


def load_locs(path: str) -> tuple[dict[str, str], dict[str, str]]:
    """--locs JSON {lang: {"title": ..., "description": ...}} -> (titles, descriptions). Raises ValueError."""
    try:
        data = json.loads(C.read_text_file(C.expand_path(path)))
    except FileNotFoundError:
        raise ValueError(f"--locs file not found: {path}") from None
    except json.JSONDecodeError as e:
        raise ValueError(f"--locs is not valid JSON: {e}") from None
    if not isinstance(data, dict):
        raise ValueError("--locs must be a JSON object keyed by language code")
    titles: dict[str, str] = {}
    descs: dict[str, str] = {}
    for lang, entry in data.items():
        lang = C.validate_pref("source_language", str(lang).strip())
        if not isinstance(entry, dict) or not str(entry.get("title", "")).strip():
            raise ValueError(f"--locs entry for '{lang}' needs a non-empty \"title\"")
        title = str(entry["title"]).strip()
        if len(title) > 100:
            raise ValueError(f"'{lang}' title is {len(title)} characters; YouTube allows 100")
        titles[lang] = title
        if str(entry.get("description", "")).strip():
            descs[lang] = str(entry["description"])
    return titles, descs


def build_body(video_id: str, sn: dict, loc: dict, titles: dict[str, str], en_description: str | None,
               original_language: str, keep_default_language: bool,
               descriptions: dict[str, str] | None = None) -> dict:
    new_loc = {**loc}
    for lang, title in titles.items():
        entry = dict(loc.get(lang, {}))
        entry["title"] = title
        entry.setdefault("description", sn["description"])
        new_loc[lang] = entry
    descs = dict(descriptions or {})
    if en_description:
        descs["en"] = en_description
    for lang, desc in descs.items():
        entry = dict(new_loc.get(lang, loc.get(lang, {})))
        entry.setdefault("title", sn["title"])
        entry["description"] = desc
        new_loc[lang] = entry
    body = {
        "id": video_id,
        "snippet": {
            "title": sn["title"],
            "description": sn["description"],
            "categoryId": sn["categoryId"],
            "tags": sn.get("tags", []),
            "defaultLanguage": sn.get("defaultLanguage") or original_language,
            "defaultAudioLanguage": sn.get("defaultAudioLanguage") or original_language,
        },
        "localizations": new_loc,
    }
    if not keep_default_language:
        body["snippet"]["defaultLanguage"] = original_language
    return body


def main(argv: list[str] | None = None) -> int:
    C.setup_utf8_io()
    ap = argparse.ArgumentParser(description="Set per-locale video titles without touching the default title.")
    ap.add_argument("--video", required=True)
    ap.add_argument("--token", default=None,
                    help="token filename or path (default: prefs token_file, else my_channel_token.json)")
    ap.add_argument("--title", action="append", metavar="LANG=TEXT", default=[],
                    help="localized title, repeatable: --title en=\"...\" --title ja=\"...\"")
    ap.add_argument("--en-title", help="alias for --title en=TEXT")
    ap.add_argument("--en-description", help="en-locale description (omit to keep existing)")
    ap.add_argument("--description", action="append", metavar="LANG=TEXT", default=[],
                    help="localized description, repeatable (\\n becomes a newline)")
    ap.add_argument("--locs", metavar="FILE.json",
                    help='many locales at once: {"ja": {"title": "...", "description": "..."}, ...}')
    ap.add_argument("--original-language", default=None,
                    help="original spoken language; defaultLanguage is corrected to this "
                         "(default: prefs source_language, else ko)")
    ap.add_argument("--keep-default-language", action="store_true",
                    help="skip the defaultLanguage correction")
    ap.add_argument("--show", action="store_true", help="only print current state, then exit")
    args = ap.parse_args(argv)

    original = args.original_language or C.load_prefs().get("source_language") or "ko"
    try:
        titles = parse_titles(args.title, args.en_title)
        descriptions = parse_descriptions(args.description)
        if args.locs:
            loc_titles, loc_descs = load_locs(args.locs)
            for lang in loc_titles:
                if lang in titles:
                    raise ValueError(f"title for '{lang}' given both in --locs and --title")
            titles.update(loc_titles)
            descriptions = {**loc_descs, **descriptions}
    except ValueError as e:
        raise SystemExit(f"error: {e}")

    at = access_token(args.token or C.default_token_name())
    it = get_video(at, args.video)
    sn = it["snippet"]
    loc = it.get("localizations", {})

    print(f"default title: {sn['title']}")
    print(f"defaultLanguage: {sn.get('defaultLanguage')}  "
          f"defaultAudioLanguage: {sn.get('defaultAudioLanguage')}")
    if loc:
        print("localizations:", json.dumps(loc, ensure_ascii=False))
    if args.show:
        return 0

    if not titles and not args.en_description and not descriptions:
        raise SystemExit("need --title LANG=TEXT, --locs FILE, --description or --en-description; "
                         "use --show to only inspect")

    body = build_body(args.video, sn, loc, titles, args.en_description, original, args.keep_default_language,
                      descriptions)
    res = put_video(at, body)
    s2 = res["snippet"]
    print("---")
    print(f"defaultLanguage -> {s2.get('defaultLanguage')}  "
          f"defaultAudioLanguage -> {s2.get('defaultAudioLanguage')}")
    print("localizations:", json.dumps(res.get("localizations", {}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
