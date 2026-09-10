"""Control per-locale title display: keep the default title as-is, and add an
en localization so only English-locale viewers see a different title.

    python3 set_localization.py --video <videoId> \
        --en-title "Shooting a DJ Set on Top of Seoul (ENG SUB)" \
        --token youtube_token.json

    python3 set_localization.py --video <videoId> --show   # inspect current state only

Default behavior:
- Corrects `defaultLanguage` to the original spoken language (default: ko).
  If a channel's settings have defaultLanguage set to en while the actual
  title is Korean, "en locale" gets misread as "defaultLanguage" and English
  viewers end up seeing the original Korean title instead of the localized
  one -- the opposite of what you want. Use --keep-default-language to skip
  this correction if you're sure defaultLanguage is already set correctly.
- Does NOT auto-append any suffix (like "(ENG SUB)") to the en title --
  you decide the exact wording and pass it in whole, since title tone is a
  judgment call this script shouldn't make for you.
- Leaves the description untouched by default. Pass --en-description to
  replace only the en-locale description.

Stdlib only, no external packages. --token follows the same convention as
upload_caption.py (bare filename resolved against ~/.claude/credentials/,
or an absolute path).
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request

from upload_caption import access_token  # reuse auth logic from the same folder

API = "https://www.googleapis.com/youtube/v3/videos"


def get_video(at: str, video_id: str) -> dict:
    u = f"{API}?" + urllib.parse.urlencode(
        {"part": "snippet,localizations", "id": video_id})
    req = urllib.request.Request(u, headers={"Authorization": f"Bearer {at}"})
    items = json.load(urllib.request.urlopen(req))["items"]
    if not items:
        raise SystemExit(f"Video not found: {video_id}")
    return items[0]


def put_video(at: str, body: dict) -> dict:
    u = f"{API}?" + urllib.parse.urlencode({"part": "snippet,localizations"})
    req = urllib.request.Request(
        u, data=json.dumps(body).encode(), method="PUT",
        headers={"Authorization": f"Bearer {at}", "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Update failed {e.code}: {e.read().decode()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--token", required=True, help="token filename or path (see reauth_channel.py)")
    ap.add_argument("--en-title", help="title shown only in the en locale")
    ap.add_argument("--en-description", help="en-locale description (omit to keep existing)")
    ap.add_argument("--original-language", default="ko",
                     help="original spoken language; defaultLanguage is corrected to this (default: ko)")
    ap.add_argument("--keep-default-language", action="store_true",
                     help="skip the defaultLanguage correction")
    ap.add_argument("--show", action="store_true", help="only print current state, then exit")
    args = ap.parse_args()

    at = access_token(args.token)
    it = get_video(at, args.video)
    sn = it["snippet"]
    loc = it.get("localizations", {})

    print(f"default title: {sn['title']}")
    print(f"defaultLanguage: {sn.get('defaultLanguage')}  "
          f"defaultAudioLanguage: {sn.get('defaultAudioLanguage')}")
    if loc:
        print("localizations:", json.dumps(loc, ensure_ascii=False))
    if args.show:
        return

    if not args.en_title and not args.en_description:
        raise SystemExit("need --en-title or --en-description (use --show to only inspect)")

    en_loc = dict(loc.get("en", {}))
    if args.en_title:
        en_loc["title"] = args.en_title
    en_loc.setdefault("description", sn["description"])
    if args.en_description:
        en_loc["description"] = args.en_description

    body = {
        "id": args.video,
        "snippet": {
            "title": sn["title"],
            "description": sn["description"],
            "categoryId": sn["categoryId"],
            "tags": sn.get("tags", []),
            "defaultLanguage": sn.get("defaultLanguage") or args.original_language,
            "defaultAudioLanguage": sn.get("defaultAudioLanguage") or args.original_language,
        },
        "localizations": {**loc, "en": en_loc},
    }
    if not args.keep_default_language:
        body["snippet"]["defaultLanguage"] = args.original_language

    res = put_video(at, body)
    s2 = res["snippet"]
    print("---")
    print(f"defaultLanguage -> {s2.get('defaultLanguage')}  "
          f"defaultAudioLanguage -> {s2.get('defaultAudioLanguage')}")
    print("localizations:", json.dumps(res.get("localizations", {}), ensure_ascii=False))


if __name__ == "__main__":
    main()
