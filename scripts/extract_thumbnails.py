"""Extract thumbnail candidate frames from a video and arrange them into a comparison gallery.

Pausing a player and screenshotting gives uneven quality (whatever resolution is
playing) and player UI pollution. This script has ffmpeg pull frames directly at
source quality (up to 1080p, to keep downloads reasonable) -- source decoding,
not a screen capture.

    # 1) extract bursts around candidate timestamps (a few frames per point, --fps per second)
    python3 extract_thumbnails.py grab --source "<url_or_local.mp4>" \
        --timestamps "0:06,2:38,2:51,3:03,4:53,5:01,5:08,7:30,8:33" \
        --out /tmp/thumb_candidates

    # 2) You (or your agent) look through each burst folder, pick the best frame of
    #    each, and copy it into a picks/ folder named like "01_label.png".

    # 3) bundle the picks into a base64-inline HTML gallery (publishing it as an
    #    artifact avoids upload timeouts and lets you compare everything at once)
    python3 extract_thumbnails.py gallery --picks-dir /tmp/thumb_candidates/picks \
        --out /tmp/thumb_candidates/gallery.html --title "Video title thumbnail candidates"

For a URL, `grab` downloads the best video-only stream with yt-dlp (1080p cap by
default, adjust with --max-height) into a temporary mp4 and extracts frames from
it; a local file is used as-is. When extracting several points in one session,
pass --keep-source to reuse the download (no re-download each time).

Only the standard library plus ffmpeg/ffprobe (and yt-dlp for downloads) are used.
JPEG resizing for the gallery uses ffmpeg (-vf scale) on every OS (no macOS-only sips).
"""
from __future__ import annotations

import argparse
import base64
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402


def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def parse_ts(ts: str) -> str:
    """Validate only -- ffmpeg -ss accepts MM:SS, HH:MM:SS and plain seconds."""
    if not re.match(r"^(\d+:)?\d{1,2}:\d{2}(\.\d+)?$|^\d+(\.\d+)?$", ts):
        raise SystemExit(f"Bad timestamp format: {ts} (examples: 90, 1:30, 1:02:03)")
    return ts


def ts_slug(ts: str) -> str:
    return ts.replace(":", "").replace(".", "_")


def download_source(url: str, out_dir: Path, max_height: int) -> Path:
    dest = out_dir / "source.mp4"
    if dest.exists():
        return dest
    ytdlp = C.find_ytdlp()
    if not ytdlp:
        raise SystemExit("yt-dlp not found. Install it (pip install yt-dlp, winget/choco/brew install yt-dlp) and "
                         "open a new terminal, or pass a local video file instead of a URL.")
    fmt = f"bestvideo[height<={max_height}][ext=mp4]/bestvideo[height<={max_height}]"
    # -P + a bare template: the folder is never parsed as an output template (a '%' or ':' in the path is safe)
    cmd = [*ytdlp, "-f", fmt, "-P", C.media_arg(out_dir), "-o", "source.%(ext)s", "--", url]
    print(f"$ {C.format_cmd(cmd)}", file=sys.stderr)
    try:
        subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL)
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"yt-dlp failed (exit {e.returncode}); the message above says why.") from None
    if not dest.exists():
        # The extension may not be mp4 -- find the file that was actually downloaded
        cands = list(out_dir.glob("source.*"))
        if not cands:
            raise SystemExit("Download failed: no source.* file was produced")
        dest = cands[0]
    return dest


def grab(args: argparse.Namespace) -> None:
    out_dir = C.expand_path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = C.require_exe("ffmpeg")

    if is_url(args.source):
        video_path = download_source(args.source, out_dir, args.max_height)
    else:
        video_path = C.expand_path(args.source)
        if not video_path.exists():
            raise SystemExit(f"Local file not found: {video_path}")

    timestamps = [parse_ts(t.strip()) for t in args.timestamps.split(",") if t.strip()]
    if not timestamps:
        raise SystemExit("--timestamps is empty")

    for ts in timestamps:
        slug = ts_slug(ts)
        burst_dir = out_dir / slug
        burst_dir.mkdir(exist_ok=True)
        # the output pattern is relative to burst_dir (cwd), so a '%' in the folder name cannot break the
        # %02d pattern; the filter holds only fps=, never a path
        cmd = [
            ffmpeg, "-y", "-ss", ts, "-i", C.media_arg(video_path),
            "-t", str(args.burst), "-vf", f"fps={args.fps}",
            "-q:v", "2", f"{slug}_%02d.png",
            "-loglevel", "error",
        ]
        print(f"$ {C.format_cmd(cmd)}", file=sys.stderr)
        try:
            subprocess.run(cmd, check=True, cwd=str(burst_dir), stdin=subprocess.DEVNULL)
        except subprocess.CalledProcessError as e:
            raise SystemExit(f"ffmpeg failed (exit {e.returncode}) at {ts}; the message above says why.") from None
        n = len(list(burst_dir.glob("*.png")))
        print(f"{ts} -> {burst_dir} ({n} frames)")

    if not args.keep_source and is_url(args.source):
        print(f"Source kept at {video_path} -- pass --keep-source to reuse it, "
              f"or delete it yourself", file=sys.stderr)

    print(f"\nNext: look through each folder, copy the best frame of each into "
          f"{out_dir}/picks/ as '01_label.png' etc., then run the gallery command.")


def resize_jpeg(src: Path, dst: Path, width: int, quality: int) -> None:
    """Resize to `width` px wide (never upscale, keeps aspect) and save as JPEG via ffmpeg.

    quality is 1-100 (higher = better); mapped to ffmpeg's -q:v scale (2 best .. 31 worst).
    """
    qv = max(2, min(31, round(2 + (100 - quality) * 29 / 99)))
    subprocess.run(
        [C.require_exe("ffmpeg"), "-y", "-loglevel", "error", "-i", C.media_arg(src),
         "-vf", f"scale='min({width},iw)':-2", "-q:v", str(qv), C.media_arg(dst)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
    )


def gallery(args: argparse.Namespace) -> None:
    picks_dir = C.expand_path(args.picks_dir)
    imgs = sorted(picks_dir.glob("*.png")) + sorted(picks_dir.glob("*.jpg"))
    if not imgs:
        raise SystemExit(f"No images in the picks folder: {picks_dir}")

    notes = {}
    if args.notes:
        try:
            notes = C.read_json(C.expand_path(args.notes))
        except (OSError, ValueError) as e:
            raise SystemExit(f"cannot read --notes file: {e}") from None

    tmp_jpeg_dir = picks_dir / "_gallery_jpeg"
    tmp_jpeg_dir.mkdir(exist_ok=True)

    cards = []
    for i, img in enumerate(imgs, 1):
        jpeg_path = tmp_jpeg_dir / f"{img.stem}.jpg"
        try:
            resize_jpeg(img, jpeg_path, args.width, args.quality)
        except subprocess.CalledProcessError:
            raise SystemExit(f"ffmpeg could not convert {img}") from None
        b64 = base64.b64encode(jpeg_path.read_bytes()).decode()
        label = html.escape(img.stem)
        note = html.escape(notes.get(img.name) or notes.get(img.stem) or "")
        cards.append(f'''
    <article class="card">
      <div class="thumb"><img src="data:image/jpeg;base64,{b64}" alt="{label}" loading="lazy"></div>
      <div class="meta">
        <div class="rank">#{i:02d}</div>
        <h3>{label}</h3>
        {f'<p>{note}</p>' if note else ''}
      </div>
    </article>''')

    title = html.escape(args.title)
    page = f'''<!doctype html>
<meta charset="utf-8">
<title>{title}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,500;12..96,700&family=IBM+Plex+Sans+KR:wght@400;500&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
:root{{
  --bg:#0F0D10; --surface:#18151A; --surface-alt:#1E1A20;
  --text:#F2EEE7; --text-muted:#A99FB0; --text-faint:#6E6577;
  --border:#2B2530; --accent:#FF4D6D;
  --mono:'IBM Plex Mono',ui-monospace,monospace;
  --sans-kr:'IBM Plex Sans KR','IBM Plex Sans',system-ui,sans-serif;
  --display:'Bricolage Grotesque',var(--sans-kr);
}}
@media (prefers-color-scheme: light){{
  :root:not([data-theme="dark"]){{
    --bg:#F7F4F0; --surface:#FFFFFF; --surface-alt:#FBF8F4;
    --text:#1D1820; --text-muted:#6E6577; --text-faint:#A99FB0;
    --border:#E7E1EA; --accent:#E23E5C;
  }}
}}
:root[data-theme="light"]{{
  --bg:#F7F4F0; --surface:#FFFFFF; --surface-alt:#FBF8F4;
  --text:#1D1820; --text-muted:#6E6577; --text-faint:#A99FB0;
  --border:#E7E1EA; --accent:#E23E5C;
}}
*{{box-sizing:border-box;}}
body{{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans-kr);line-height:1.5;}}
.wrap{{max-width:1180px;margin:0 auto;padding:32px 22px 70px;}}
h1{{font-family:var(--display);font-weight:700;font-size:28px;margin:0 0 22px;text-wrap:balance;}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:14px;overflow:hidden;}}
.thumb{{aspect-ratio:16/9;background:var(--surface-alt);overflow:hidden;}}
.thumb img{{width:100%;height:100%;object-fit:cover;display:block;}}
.meta{{padding:12px 14px 14px;}}
.rank{{font-family:var(--mono);font-size:11px;color:var(--text-faint);}}
h3{{font-size:14.5px;font-weight:600;margin:2px 0 0;font-family:var(--display);}}
.meta p{{font-size:12.5px;color:var(--text-muted);margin:6px 0 0;line-height:1.5;}}
</style>
<div class="wrap">
  <h1>{title}</h1>
  <div class="grid">{"".join(cards)}
  </div>
</div>
'''
    out_path = C.expand_path(args.out)
    C.write_text_lf(out_path, page)  # UTF-8 + <meta charset>: Windows browsers otherwise read a file:// page as cp949/cp1252
    C.remove_tree(tmp_jpeg_dir)
    print(f"✓ {out_path} ({len(imgs)} images, {len(page)/1024:.0f}KB)")


def main():
    C.setup_utf8_io()
    ap = argparse.ArgumentParser(description="Extract thumbnail candidate frames and build a comparison gallery.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("grab", help="extract burst frames per timestamp")
    g.add_argument("--source", required=True, help="YouTube URL or local video path")
    g.add_argument("--timestamps", required=True, help='comma-separated, e.g. "0:06,2:38,3:03"')
    g.add_argument("--out", required=True)
    g.add_argument("--burst", type=float, default=2.0, help="seconds extracted per point")
    g.add_argument("--fps", type=float, default=6.0, help="frames per second within each burst")
    g.add_argument("--max-height", type=int, default=1080, help="download resolution cap (height in px)")
    g.add_argument("--keep-source", action="store_true")
    g.set_defaults(func=grab)

    gal = sub.add_parser("gallery", help="turn the picks folder into a base64-inline HTML gallery")
    gal.add_argument("--picks-dir", required=True)
    gal.add_argument("--out", required=True)
    gal.add_argument("--title", default="Thumbnail Candidates")
    gal.add_argument("--notes", help='optional JSON {"file.png": "one-line note"}')
    gal.add_argument("--width", type=int, default=640)
    gal.add_argument("--quality", type=int, default=78)
    gal.set_defaults(func=gallery)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
