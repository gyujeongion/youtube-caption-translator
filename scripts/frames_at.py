#!/usr/bin/env python3
"""영상에서 자막 블록 시점의 프레임을 뽑아 화자·상황을 눈으로 확정한다.

번역 전에 화자 지도를 만들 때(--sample), 그리고 특정 블록의 화자가
갈릴 때(--at) 쓴다. 크롭을 주면 인물별로 잘라 입 모양까지 본다.

  # 전체를 균등 샘플링해 등장인물·장소·구도 파악
  python3 frames_at.py --video V.mp4 --sample 20 --out /tmp/f

  # 특정 시점 (SRT 타임코드 또는 초)
  python3 frames_at.py --video V.mp4 --at 4:25.4,9:12,552 --out /tmp/f

  # 좌/우 인물 얼굴만 크롭해 누가 말하는지 판별
  python3 frames_at.py --video V.mp4 --at 4:26 --crop left,right --out /tmp/f
"""
import argparse, json, os, re, subprocess, sys

def to_sec(v):
    v = v.strip()
    if re.fullmatch(r'\d+(\.\d+)?', v):
        return float(v)
    m = re.fullmatch(r'(?:(\d+):)?(\d+):(\d+)(?:[.,](\d+))?', v)
    if not m:
        raise SystemExit(f"시간 형식을 못 읽음: {v}")
    h, mi, s, frac = m.groups()
    out = int(h or 0) * 3600 + int(mi) * 60 + int(s)
    if frac:
        out += float('0.' + frac)
    return out

def probe(video):
    r = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
         '-show_entries', 'stream=width,height', '-show_entries', 'format=duration',
         '-of', 'json', video],
        capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    st = d['streams'][0]
    return int(st['width']), int(st['height']), float(d['format']['duration'])

def crop_filter(name, w, h):
    """화면을 세로로 3등분해 인물 영역을 잡는다. 4K 기준이 아니라 비율 기준."""
    boxes = {
        'left':   (0.00, 0.15, 0.42, 0.75),
        'center': (0.30, 0.15, 0.40, 0.75),
        'right':  (0.55, 0.15, 0.45, 0.75),
        'full':   (0.00, 0.00, 1.00, 1.00),
    }
    if name not in boxes:
        raise SystemExit(f"crop 이름은 {list(boxes)} 중 하나여야 함: {name}")
    x, y, cw, ch = boxes[name]
    return f"crop={int(w*cw)}:{int(h*ch)}:{int(w*x)}:{int(h*y)}"

def grab(video, sec, path, vf):
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-ss', f'{sec:.3f}',
                    '-i', video, '-frames:v', '1', '-vf', vf, '-y', path], check=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--video', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--at', help='쉼표로 구분한 시점 (mm:ss.mmm 또는 초)')
    ap.add_argument('--sample', type=int, help='영상 전체를 이 장수로 균등 샘플링')
    ap.add_argument('--crop', default='full',
                    help='쉼표 구분: full,left,center,right (기본 full)')
    ap.add_argument('--width', type=int, default=560, help='출력 가로 픽셀 (기본 560)')
    a = ap.parse_args()

    w, h, dur = probe(a.video)
    os.makedirs(a.out, exist_ok=True)

    times = []
    if a.sample:
        step = dur / (a.sample + 1)
        times += [step * (i + 1) for i in range(a.sample)]
    if a.at:
        times += [to_sec(v) for v in a.at.split(',') if v.strip()]
    if not times:
        raise SystemExit('--at 또는 --sample 중 하나는 있어야 함')

    made = []
    for sec in sorted(set(times)):
        for c in [x.strip() for x in a.crop.split(',') if x.strip()]:
            vf = f"{crop_filter(c, w, h)},scale={a.width}:-1"
            name = f"{int(sec//60):02d}m{sec%60:06.3f}s".replace('.', '_')
            path = os.path.join(a.out, f"{name}_{c}.jpg")
            grab(a.video, sec, path, vf)
            made.append(path)

    print(f"{len(made)}장 저장: {a.out}")
    for p in made:
        print(" ", p)

if __name__ == '__main__':
    main()
