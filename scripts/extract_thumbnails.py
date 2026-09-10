"""영상에서 썸네일 후보 프레임을 뽑아 대조 갤러리로 정리한다.

플레이어 일시정지 캡처는 화질 불규칙(재생 해상도 그대로)·재생바 UI 오염이 있다.
이 스크립트는 원본 소스 화질(최대 1080p, 다운로드 용량 고려)로 ffmpeg가 직접
프레임을 뽑는다 — 화면 캡처가 아니라 소스 디코딩.

    # ① 후보 지점 버스트 추출 (지점마다 --fps 장수만큼 몇 프레임씩)
    python3 extract_thumbnails.py grab --source "<url_or_local.mp4>" \
        --timestamps "0:06,2:38,2:51,3:03,4:53,5:01,5:08,7:30,8:33" \
        --out /tmp/thumb_candidates

    # ② Claude(또는 사람)가 각 burst 폴더를 Read로 훑어보고 최고 프레임 골라서
    #    picks/ 폴더에 "숫자_라벨.png" 이름으로 복사해둔다.

    # ③ 고른 것들을 base64 인라인 HTML 갤러리로 묶는다 (Artifact로 publish하면
    #    다운로드 업로드 타임아웃 없이 한 번에 대조 가능)
    python3 extract_thumbnails.py gallery --picks-dir /tmp/thumb_candidates/picks \
        --out /tmp/thumb_candidates/gallery.html --title "영상 제목 썸네일 후보"

`grab`은 URL이면 yt-dlp로 video-only 최고화질(기본 1080p 상한, --max-height로
조절)을 받아 임시 mp4로 저장한 뒤 프레임을 뽑는다. 로컬 파일이면 그대로 쓴다.
같은 세션에서 여러 지점을 뽑을 거면 --keep-source로 다운로드본을 재사용한다
(매번 재다운로드하지 않음).

외부 패키지 없이 표준 라이브러리 + ffmpeg/ffprobe/yt-dlp(다운로드 시)만 쓴다.
갤러리의 JPEG 리사이즈는 macOS `sips`를 쓴다(다른 OS면 ffmpeg -vf scale로 대체).
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from pathlib import Path


def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def parse_ts(ts: str) -> str:
    """검증만 한다 — ffmpeg -ss 는 MM:SS/HH:MM:SS/초 다 받는다."""
    if not re.match(r"^(\d+:)?\d{1,2}:\d{2}(\.\d+)?$|^\d+(\.\d+)?$", ts):
        raise SystemExit(f"타임스탬프 형식 이상함: {ts} (예: 90, 1:30, 1:02:03)")
    return ts


def ts_slug(ts: str) -> str:
    return ts.replace(":", "").replace(".", "_")


def download_source(url: str, out_dir: Path, max_height: int) -> Path:
    dest = out_dir / "source.mp4"
    if dest.exists():
        return dest
    fmt = f"bestvideo[height<={max_height}][ext=mp4]/bestvideo[height<={max_height}]"
    cmd = ["yt-dlp", "-f", fmt, "-o", str(dest), url]
    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    subprocess.run(cmd, check=True)
    if not dest.exists():
        # 확장자가 mp4가 아닐 수 있음 — 실제로 받아진 파일 찾기
        cands = list(out_dir.glob("source.*"))
        if not cands:
            raise SystemExit("다운로드 실패: source.* 파일이 없음")
        dest = cands[0]
    return dest


def grab(args: argparse.Namespace) -> None:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if is_url(args.source):
        video_path = download_source(args.source, out_dir, args.max_height)
    else:
        video_path = Path(args.source).expanduser()
        if not video_path.exists():
            raise SystemExit(f"로컬 파일 없음: {video_path}")

    timestamps = [parse_ts(t.strip()) for t in args.timestamps.split(",") if t.strip()]
    if not timestamps:
        raise SystemExit("--timestamps 비어있음")

    for ts in timestamps:
        slug = ts_slug(ts)
        burst_dir = out_dir / slug
        burst_dir.mkdir(exist_ok=True)
        cmd = [
            "ffmpeg", "-y", "-ss", ts, "-i", str(video_path),
            "-t", str(args.burst), "-vf", f"fps={args.fps}",
            "-q:v", "2", str(burst_dir / f"{slug}_%02d.png"),
            "-loglevel", "error",
        ]
        print(f"$ {' '.join(cmd)}", file=sys.stderr)
        subprocess.run(cmd, check=True)
        n = len(list(burst_dir.glob("*.png")))
        print(f"{ts} → {burst_dir} ({n}장)")

    if not args.keep_source and is_url(args.source):
        print(f"원본은 {video_path} 에 남겨둠 — 재사용하려면 --keep-source, "
              f"지울 거면 직접 rm", file=sys.stderr)

    print(f"\n다음: 각 폴더를 Read로 훑어보고 제일 나은 프레임을 "
          f"{out_dir}/picks/ 에 '01_라벨.png' 식으로 복사한 뒤 gallery 커맨드 실행.")


def resize_jpeg(src: Path, dst: Path, width: int, quality: int) -> None:
    subprocess.run(
        ["sips", "-Z", str(width), str(src), "--setProperty", "formatOptions",
         str(quality), "--out", str(dst)],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def gallery(args: argparse.Namespace) -> None:
    picks_dir = Path(args.picks_dir)
    imgs = sorted(picks_dir.glob("*.png")) + sorted(picks_dir.glob("*.jpg"))
    if not imgs:
        raise SystemExit(f"picks 폴더에 이미지 없음: {picks_dir}")

    notes = {}
    if args.notes:
        notes = json.loads(Path(args.notes).read_text())

    tmp_jpeg_dir = picks_dir / "_gallery_jpeg"
    tmp_jpeg_dir.mkdir(exist_ok=True)

    cards = []
    for i, img in enumerate(imgs, 1):
        jpeg_path = tmp_jpeg_dir / f"{img.stem}.jpg"
        if img.suffix.lower() == ".png":
            resize_jpeg(img, jpeg_path, args.width, args.quality)
        else:
            resize_jpeg(img, jpeg_path, args.width, args.quality)
        b64 = base64.b64encode(jpeg_path.read_bytes()).decode()
        label = img.stem
        note = notes.get(img.name) or notes.get(img.stem) or ""
        cards.append(f'''
    <article class="card">
      <div class="thumb"><img src="data:image/jpeg;base64,{b64}" alt="{label}" loading="lazy"></div>
      <div class="meta">
        <div class="rank">#{i:02d}</div>
        <h3>{label}</h3>
        {f'<p>{note}</p>' if note else ''}
      </div>
    </article>''')

    html = f'''<title>{args.title}</title>
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
  <h1>{args.title}</h1>
  <div class="grid">{"".join(cards)}
  </div>
</div>
'''
    out_path = Path(args.out)
    out_path.write_text(html, encoding="utf-8")
    print(f"✓ {out_path} ({len(imgs)}장, {len(html)/1024:.0f}KB)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("grab", help="타임스탬프별 버스트 프레임 추출")
    g.add_argument("--source", required=True, help="YouTube URL 또는 로컬 영상 경로")
    g.add_argument("--timestamps", required=True, help='"0:06,2:38,3:03" 콤마구분')
    g.add_argument("--out", required=True)
    g.add_argument("--burst", type=float, default=2.0, help="지점당 추출 구간(초)")
    g.add_argument("--fps", type=float, default=6.0, help="구간 내 초당 프레임")
    g.add_argument("--max-height", type=int, default=1080, help="다운로드 상한 해상도")
    g.add_argument("--keep-source", action="store_true")
    g.set_defaults(func=grab)

    gal = sub.add_parser("gallery", help="picks 폴더를 base64 인라인 HTML 갤러리로")
    gal.add_argument("--picks-dir", required=True)
    gal.add_argument("--out", required=True)
    gal.add_argument("--title", default="Thumbnail Candidates")
    gal.add_argument("--notes", help='{"파일명.png": "한줄메모"} JSON (선택)')
    gal.add_argument("--width", type=int, default=640)
    gal.add_argument("--quality", type=int, default=78)
    gal.set_defaults(func=gallery)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
