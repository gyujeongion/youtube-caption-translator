#!/usr/bin/env python3
"""Extract frames at subtitle-block times from a LOCAL video file to confirm speakers and context by eye.

Use it before translating to build a speaker map (--sample), and when the speaker of a
particular block is ambiguous (--at). With --crop it cuts out each person so you can
see mouth movement.

  # sample evenly across the whole video to learn the people, places and framing
  python3 frames_at.py --video V.mp4 --sample 20 --out /tmp/f

  # specific moments (SRT timecodes or seconds)
  python3 frames_at.py --video V.mp4 --at 4:25.4,9:12,552 --out /tmp/f

  # crop the left/right person's face to tell who is speaking
  python3 frames_at.py --video V.mp4 --at 4:26 --crop left,right --out /tmp/f

--video must be a local file (URLs are not supported; download the video first).
"""
import argparse, json, os, re, shutil, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _common as C  # noqa: E402

def to_sec(v):
    v = v.strip()
    if re.fullmatch(r'\d+(\.\d+)?', v):
        return float(v)
    m = re.fullmatch(r'(?:(\d+):)?(\d+):(\d+)(?:[.,](\d+))?', v)
    if not m:
        raise SystemExit(f"Cannot parse time value: {v}")
    h, mi, s, frac = m.groups()
    out = int(h or 0) * 3600 + int(mi) * 60 + int(s)
    if frac:
        out += float('0.' + frac)
    return out

def probe(video):
    # shutil.which is PATHEXT-aware (ffprobe.exe / .cmd shims on Windows); a missing tool surfaces as
    # FileNotFoundError from subprocess. media_arg() makes the path absolute so it can never look like an option.
    exe = shutil.which('ffprobe') or 'ffprobe'
    r = subprocess.run(
        [exe, '-v', 'error', '-select_streams', 'v:0',
         '-show_entries', 'stream=width,height', '-show_entries', 'format=duration',
         '-of', 'json', C.media_arg(video)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', check=True,
        stdin=subprocess.DEVNULL)
    d = json.loads(r.stdout)
    st = d['streams'][0]
    return int(st['width']), int(st['height']), float(d['format']['duration'])

def crop_filter(name, w, h):
    """Pick a person region by splitting the frame into thirds. Ratio-based, not pixel-based (works for any resolution)."""
    boxes = {
        'left':   (0.00, 0.15, 0.42, 0.75),
        'center': (0.30, 0.15, 0.40, 0.75),
        'right':  (0.55, 0.15, 0.45, 0.75),
        'full':   (0.00, 0.00, 1.00, 1.00),
    }
    if name not in boxes:
        raise SystemExit(f"crop name must be one of {list(boxes)}: {name}")
    x, y, cw, ch = boxes[name]
    return f"crop={int(w*cw)}:{int(h*ch)}:{int(w*x)}:{int(h*y)}"

def grab(video, sec, path, vf):
    # the filter string holds only crop/scale numbers, never a path (colons/backslashes in paths break filters)
    exe = shutil.which('ffmpeg') or 'ffmpeg'
    try:
        subprocess.run([exe, '-nostdin', '-v', 'error', '-ss', f'{sec:.3f}',
                        '-i', C.media_arg(video), '-frames:v', '1', '-vf', vf, '-y', C.media_arg(path)],
                       check=True, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise SystemExit(C.ffmpeg_missing_message()) from None
    except subprocess.CalledProcessError as e:
        raise SystemExit(f"ffmpeg failed (exit {e.returncode}) while extracting a frame at {sec:.3f}s; "
                         "the message above says why.") from None

def main():
    C.setup_utf8_io()
    ap = argparse.ArgumentParser(description="Extract frames at given times from a local video file.")
    ap.add_argument('--video', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--at', help='comma-separated times (mm:ss.mmm or seconds)')
    ap.add_argument('--sample', type=int, help='sample this many frames evenly across the whole video')
    ap.add_argument('--crop', default='full',
                    help='comma-separated: full,left,center,right (default full)')
    ap.add_argument('--width', type=int, default=560, help='output width in pixels (default 560)')
    a = ap.parse_args()
    a.video = str(C.expand_path(a.video)) if not re.match(r"(?i)^https?://", a.video) else a.video
    a.out = str(C.expand_path(a.out))

    if re.match(r"(?i)^https?://", a.video):
        raise SystemExit("--video must be a LOCAL file, not a URL. Download the video first "
                         "(for example with yt-dlp), then pass the file path.")
    if not os.path.isfile(a.video):
        raise SystemExit(f"Video file not found: {a.video}")

    try:
        w, h, dur = probe(a.video)
    except FileNotFoundError:
        raise SystemExit(C.ffmpeg_missing_message()) from None
    except (subprocess.CalledProcessError, ValueError, KeyError, IndexError) as e:
        raise SystemExit(f"ffprobe could not read the video: {a.video} ({type(e).__name__})") from None
    os.makedirs(a.out, exist_ok=True)

    times = []
    if a.sample:
        step = dur / (a.sample + 1)
        times += [step * (i + 1) for i in range(a.sample)]
    if a.at:
        times += [to_sec(v) for v in a.at.split(',') if v.strip()]
    if not times:
        raise SystemExit('one of --at or --sample is required')

    made = []
    for sec in sorted(set(times)):
        for c in [x.strip() for x in a.crop.split(',') if x.strip()]:
            vf = f"{crop_filter(c, w, h)},scale={a.width}:-1"
            name = f"{int(sec//60):02d}m{sec%60:06.3f}s".replace('.', '_')
            path = os.path.join(a.out, f"{name}_{c}.jpg")
            grab(a.video, sec, path, vf)
            made.append(path)

    print(f"Saved {len(made)} frames: {a.out}")
    for p in made:
        print(" ", p)

if __name__ == '__main__':
    main()
