# youtube-caption-translator

유튜브 다국어 자막 자동화 스킬

[English README](README.md)

영상 속 말을 외국어 유튜브 자막 트랙으로 만들어 올려 주는 AI 에이전트용
스킬입니다. [Claude Code](https://claude.com/claude-code) 같은 코딩 에이전트에서
쓸 수 있습니다. 개발자가 아니라 유튜버를 위해 만들었습니다. 설정은 에이전트가
쉬운 말로 안내하고, 명령어도 에이전트가 대신 실행합니다.

편집 프로그램에서 뽑은 자막은 말을 짧은 조각으로 잘라 놓습니다. 그 조각을
그대로 1:1로 번역하면 문장이 중간에 뚝 끊깁니다. 이 스킬은 **조각을 온전한
문장으로 합친 뒤에 번역**하고, 원래 조각의 경계에 맞춰 타임코드를 다시 배치합니다.
그다음 기계적 검사와 사람의 뉘앙스 검수를 거친 뒤에만 업로드합니다.

## 자막 원본은 영상마다 따로 정합니다

```
                     ┌── 편집기에서 뽑은 SRT가 있다 ──────── 항상 이걸 먼저 씁니다 (타이밍 최고, 무료)
내 영상 하나하나 ────┤
                     └── SRT가 없다 ── 무료: 편집기 자동자막 또는 유튜브 자동자막 (이쪽에서 확인 못 함)
                                       ── 또는 클라우드 음성인식 (Deepgram / OpenAI whisper-1 / Soniox)
                                       ── 또는 로컬 음성인식 (무료, 내 컴퓨터에서 실행)
                                              │
                                              ▼
            문장 단위로 번역 ─▶ 기계 검사 ─▶ 검수 표 ─▶ 유튜브 업로드
                                                    (또는 "direct" 모드: 검사 통과 즉시 업로드)
```

섞여 있어도 정상입니다. 예를 들어 영상 4개 중 1개만 SRT가 있고 3개는 없는 경우입니다.

## 빠른 시작

1. 에이전트의 스킬 폴더에 파일을 받습니다. **방법 A: git**

   macOS / Linux (Claude Code):

   ```bash
   git clone https://github.com/gyujeongion/youtube-caption-translator.git ~/.claude/skills/youtube-caption-translator
   ```

   Windows (PowerShell, Claude Code):

   ```powershell
   git clone https://github.com/gyujeongion/youtube-caption-translator.git "$env:USERPROFILE\.claude\skills\youtube-caption-translator"
   ```

   **방법 B: git이 없을 때.** GitHub 페이지에서 **Code > Download ZIP**을 눌러
   받고, 압축을 푼 폴더를 Windows는
   `%USERPROFILE%\.claude\skills\youtube-caption-translator`, macOS / Linux는
   `~/.claude/skills/youtube-caption-translator` 위치에 둡니다. 그 폴더 바로
   안에 `SKILL.md`가 있어야 합니다.

   Codex 같은 다른 에이전트는 자기만의 스킬 폴더를 씁니다. 그 에이전트의 스킬
   폴더를 쓰거나 [AGENTS.md](AGENTS.md)를 보세요.

2. 에이전트에게 이렇게 말합니다.

   > youtube-caption-translator 설정해 줘

3. 에이전트가 묻는 네 가지 질문에 답하면 됩니다. 나머지는 에이전트가 하고,
   브라우저에서 눌러야 할 때만 알려 줍니다.

OAuth, 할당량 유닛, SRT 같은 낯선 말은 [용어집](references/setup-wizard.md#glossary)에
쉬운 말로 정리해 두었습니다(영어 문서입니다).

## 설정 마법사가 하는 일

에이전트가 먼저 점검표(`scripts/check_setup.py`)를 돌리고, 이어서 묻습니다.

1. **언어** - 영상에서 쓰는 말, 그리고 번역할 언어.
2. **SRT가 없는 영상은 어떻게 할지** - 아무것도 안 함(SRT만 사용), 클라우드
   음성인식, 로컬 음성인식 중 하나. 편집기 SRT가 있으면 영상마다 항상 그것을
   먼저 씁니다.
3. **공개 모드** - `review`(업로드마다 내가 승인, 첫 영상들에 추천) 또는
   `direct`(검사를 통과하면 바로 업로드, 설정 때의 선택이 곧 승인).
4. **유튜브 채널** - 자막을 붙일 채널.

그다음 구글 클라우드 설정(프로젝트 만들기, YouTube Data API 켜기, 동의 화면
설정, 데스크톱 OAuth 클라이언트 만들기, 앱 게시, 로그인)을 토큰이 생길 때까지
함께 진행합니다. 음성인식 키가 필요하거나 로컬 모델을 골라야 하면 그것도
도와줍니다. 자세한 내용은 [references/setup-wizard.md](references/setup-wizard.md)에
있습니다.

## 솔직한 한계

- **하루 할당량.** 새 구글 클라우드 프로젝트는 기본으로 하루 10,000 유닛을 받고,
  자막 트랙 하나를 추가할 때 400 유닛이 들고, 이 도구는 기존 트랙을 먼저
  조회(50)하므로 새 트랙은 약 450, 교체는 약 500 유닛입니다. 그래서 **하루에
  새 트랙 약 22개**가 상한입니다. 예: 영상 4개 x 2개 언어 = 트랙 8개, 약 3,600
  유닛이라 하루에 됩니다. 에이전트가 작업 전에 예상치를 보여 줍니다. 할당량은
  태평양 시간 자정에 초기화되며 한국 시간으로는 대략 16시~17시입니다(서머타임에
  따라 다름). 수치는 2026-09-22에 구글 문서로 확인했습니다.
- **구글 로그인이 번거롭습니다.** 나만 쓰는 구글 클라우드 앱을 직접 만듭니다.
  이 앱에는 "확인되지 않은 앱" 경고가 뜨는데, 개인용이라면 정상입니다. 클라이언트
  시크릿은 만들 때 **딱 한 번만** 내려받을 수 있습니다. 일부 화면 문구는 공식
  문서로 확인하지 못해서, 안내에 "화면이 다르면..."이라고 적어 두었습니다.
  [references/google-cloud-setup.md](references/google-cloud-setup.md)를 보세요.
- **토큰이 그래도 만료될 수 있습니다.** 앱을 게시하면 테스트 상태의 7일 만료는
  사라지지만, 확인되지 않은 앱의 토큰이 영원히 유지된다고 구글이 명시하지는
  않습니다. 업로드가 `invalid_grant`로 실패하면 로그인 단계를 다시 하면 됩니다.
- **음성인식 정확도는 이쪽에서 측정하지 않았습니다.** 한국어에서 어느 업체가
  가장 낫다고 검증된 자료는 없습니다. 짧은 영상 하나로 두 엔진을 돌려 비교해
  보세요. 결과는 최종본이 아니라 읽고 고쳐야 할 초안입니다.
- **OpenAI는 `whisper-1`만 됩니다.** OpenAI의 최신 음성 모델은 타임스탬프를
  돌려주지 않아서 자막을 만들 수 없습니다.
- **Soniox는 무료 크레딧이 없습니다**(2025-10-27 종료). Deepgram은 카드 없이
  $200 크레딧을 줍니다. Deepgram의 다국어 혼합 모드에는 한국어가 없습니다.
- **성능이 낮은 컴퓨터의 로컬 모델은 품질이 나쁠 수 있습니다.** 특히 메모리
  4GB에서 한국어는 그렇습니다.
- **가격과 모델 이름은 바뀝니다.** 참고 문서에는 "verified 2026-09-22"를 찍어
  두었고, 에이전트는 무언가를 추천하기 전에 출처 URL을 다시 확인하도록 되어
  있습니다.
- 번역은 [SKILL.md](SKILL.md)를 따르는 AI 에이전트가 합니다. 주어가 생략된
  문장이나 화면 밖 맥락에서는 여전히 틀릴 수 있습니다. 그래서 review 모드가
  있습니다.

## 자격 증명 안전

- **API 키를 에이전트 채팅에 붙여 넣지 마세요.** 에이전트가 알려주는
  `check_setup.py --set-key <업체>` 명령을 본인 터미널에서 직접 실행하면
  (Windows는 Git Bash가 아니라 PowerShell) 입력이 보이지 않는 프롬프트가 뜨고,
  키는 비공개 파일에 저장됩니다(macOS와 Linux는 권한 600, Windows는 파일 권한을
  따로 제한하지 않으므로 사용자 프로필 폴더 안에 두세요). 키는 거기에만
  입력하세요.
- 설정 폴더를 Dropbox, Google Drive, OneDrive 폴더 안에 두지 마세요. 키가
  클라우드로 동기화됩니다.
- 키, 토큰, `client_secret.json`은 설정 폴더(`$YTCAPTION_HOME`가 있으면 그
  경로, 없으면 `~/.claude/credentials/`, Windows는
  `%USERPROFILE%\.claude\credentials\`)에 저장됩니다. git 저장소 밖이고
  `.gitignore`로도 막혀 있습니다. 절대 커밋하지 마세요.
- 이 도구가 요청하는 유튜브 권한은 자막 등을 수정하고 삭제할 수 있는 넓은
  권한입니다(구글 표현: "See, edit, and permanently delete your YouTube videos,
  ratings, comments and captions"). 토큰 파일은 비밀번호처럼 지키세요.
- 업로드는 공개 내용을 바꿉니다. `review` 모드에서는 내가 승인하기 전에는
  아무것도 올라가지 않습니다.

## 준비물

- Python 3.10 이상 (핵심 스크립트는 표준 라이브러리만 사용, pip 설치 불필요).
  문서에는 `python3`라고 적혀 있지만 **Windows에서는 `py -3`(또는 `python`)을 쓰고
  `python3`는 쓰지 마세요**(Microsoft Store 스텁일 수 있음).
- `ffmpeg` / `ffprobe`. Windows에서는 `choco install ffmpeg`로 설치하거나
  https://ffmpeg.org/download.html 에서 받은 뒤, **새 터미널을 열어야** PATH가
  갱신됩니다. (winget 패키지 이름은 확인하지 못해서 적지 않았습니다.)
- `yt-dlp` (선택. URL에서 썸네일을 뽑을 때, 또는 프레임 추출 전에 영상을 먼저
  내려받을 때. 프레임 추출은 로컬 파일만 됩니다)
- 채널을 소유한 구글 계정, 그리고 설정 중에 만드는 구글 클라우드 프로젝트
- 선택: 음성인식 API 키, 또는 로컬 런타임(whisper.cpp, mlx-whisper,
  faster-whisper). 로컬 런타임은 물어보지 않고는 설치하지 않습니다.
- macOS, Windows, Linux에서 동작합니다.

## 다른 에이전트에서 쓰기

`SKILL.md` 자동 로딩은 Claude Code의 방식입니다. [AGENTS.md](AGENTS.md)는 다른
에이전트(Codex CLI 등)가 `SKILL.md`를 읽도록 안내하는 짧은 연결 파일입니다.
다른 에이전트는 각자의 스킬 폴더를 씁니다. 스크립트는 평범한 Python이라
단독으로도 쓸 수 있습니다.

## 스크립트만 따로 쓰기

명령은 `python3`로 적었습니다. Windows에서는 `py -3`(또는 `python`)을 쓰세요.
`<작업 폴더>`는 쓸 수 있는 아무 폴더이고, `-o`나 `--out`에 적는 폴더는 미리
만들어져 있어야 합니다. 명령은 한 줄로 적었습니다(PowerShell은 줄 이음에 백틱,
cmd는 `^`를 씁니다). 공백이 든 경로는 따옴표로 감싸세요. 스크립트는 따옴표 친
경로와 `%USERPROFILE%`를 받습니다. **PowerShell 5.1에서는 출력을 `>`로
리다이렉트하지 마세요**(UTF-16으로 저장됨). 스크립트의 `-o` / `--out` 옵션을
쓰세요. 입력 SRT는 UTF-8이어야 합니다(BOM 있는 UTF-8과 UTF-16은 허용, cp949
같은 ANSI 저장본은 거부되며 안내가 나옵니다. 메모장: 파일 > 다른 이름으로 저장 >
인코딩: UTF-8).

```bash
# 사전 점검표, 그리고 키를 안전하게 저장하는 방법
python3 scripts/check_setup.py
python3 scripts/check_setup.py --set-key deepgram
python3 scripts/check_setup.py --set-pref publish_mode=review
python3 scripts/check_setup.py --show-prefs

# 이 컴퓨터가 로컬 음성인식을 얼마나 돌릴 수 있는지
python3 scripts/detect_hardware.py --json

# SRT가 없다면 만들기 (엔진: deepgram | openai | soniox | local)
python3 scripts/transcribe.py --input video.mp4 --engine deepgram --lang ko --out source.ko.srt --dry-run
# 화자가 여러 명이면 --diarize 추가 (Deepgram, Soniox만. source.ko.speakers.json을 씀. Deepgram의 화자 분리는 유료 추가 기능이고 가격은 확인하지 못함)
# --yes: 처음 한 번의 로컬 모델 다운로드 허용, --force: 기존 --out 파일 덮어쓰기

# 채널 인증 (채널마다 한 번)
python3 scripts/reauth_channel.py --token my_channel_token.json --secret client_secret.json --expect-channel UCxxxxxxxxxxxxxxxxxxxxxx

# 업로드 전에 번역 SRT를 원본과 대조해 검증
python3 scripts/verify_srt.py source.ko.srt translation.en.srt

# 번역 전에 끊긴 발화 찾기 (한국어 원본만 해당)
python3 scripts/flag_incomplete.py source.ko.srt

# 화자/청자가 애매한 블록용 컨텍스트 팩: 만들고, speaker_map.json을 채우고, 확인
python3 scripts/context_pack.py build --video video.mp4 --srt source.ko.srt --out "<작업 폴더>/pack" --auto --crops left,right
python3 scripts/context_pack.py check --map "<작업 폴더>/pack/speaker_map.json" --srt source.ko.srt

# 원본/번역 대조 검수 표
python3 scripts/align_pairs.py source.ko.srt translation.en.srt -o "<작업 폴더>/review.md"

# 자막 트랙 업로드 또는 갱신
python3 scripts/upload_caption.py --video VIDEO_ID --srt translation.en.srt --lang en --name English --draft
# --token은 생략 가능: 설정의 token_file, 없으면 my_channel_token.json

# 언어별 제목 설정 (--title 반복 가능, --en-title X는 --title "en=X"와 같음)
python3 scripts/set_localization.py --video VIDEO_ID --title "en=Your English Title" --title "ja=あなたの日本語タイトル"

# 썸네일 후보 뽑기
python3 scripts/extract_thumbnails.py grab --source VIDEO.mp4 --timestamps "0:06,2:38,3:03" --out "<작업 폴더>/thumbs"
python3 scripts/extract_thumbnails.py gallery --picks-dir "<작업 폴더>/thumbs/picks" --out "<작업 폴더>/thumbs/gallery.html"
```

토큰 파일을 파일명만 적으면 설정 폴더 기준으로 찾고, 절대 경로를 줘도 됩니다.

## 왜 합친 다음에 번역하나

원본 SRT가 이렇게 잘려 있다고 해 봅시다.

```
1) "My approach is,"
2) "since this recipe is called Bibimbap,"
3) "the point is to show"
```

조각별로 번역하면 각각은 문장처럼 읽히지만 하나하나가 다 틀립니다. 이 스킬은
블록 전체를 먼저 읽고 한 문장으로 합친 다음에 번역합니다. 합친 블록의 타임코드는
원래 조각의 경계에 그대로 맞춰 두기 때문에, `verify_srt.py`가 새로 지어낸
타임코드가 없다는 것을 확인할 수 있습니다.

## 구성

| 경로 | 내용 |
|---|---|
| [SKILL.md](SKILL.md) | 에이전트가 따르는 작업 절차: Phase 0 설정, Ⓐ 단계(자막 원본 얻기), ⓪~⑥ 단계 |
| [AGENTS.md](AGENTS.md) | 스킬 자동 로딩이 없는 에이전트용 연결 파일 |
| [references/setup-wizard.md](references/setup-wizard.md) | 첫 실행 질문 스크립트 |
| [references/google-cloud-setup.md](references/google-cloud-setup.md) | 구글 클라우드 안내, 할당량, 문제 해결 |
| [references/stt-cloud.md](references/stt-cloud.md) | 클라우드 음성인식 업체, 키 발급, 비용 |
| [references/stt-local.md](references/stt-local.md) | 로컬 음성인식 사양표, 런타임, 주의점 |
| [references/context-pack.md](references/context-pack.md) | 컨텍스트 팩: 컨택트 시트, 화자 지도 형식, 선별 점수, 한계 |
| `scripts/check_setup.py` | 사전 점검, 키 안전 입력, 설정값 |
| `scripts/detect_hardware.py` | 하드웨어 요약과 로컬 모델 추천 |
| `scripts/transcribe.py` | 음성인식으로 SRT 만들기 (클라우드 또는 로컬). 선택 옵션 `--diarize`는 화자 파일 생성 (Deepgram, Soniox) |
| `scripts/frames_at.py` | 영상 프레임으로 화자와 상황 파악 |
| `scripts/context_pack.py` | 애매한 블록의 컨택트 시트와 화자 지도 양식, `check`로 미해결 목록 확인 |
| `scripts/flag_incomplete.py` | 원본 SRT의 끊긴 발화 찾기 |
| `scripts/verify_srt.py` | 타임코드와 줄 길이 기계 검증 |
| `scripts/align_pairs.py` | 원본/번역 검수 표 |
| `scripts/reauth_channel.py` | 채널 인증과 토큰 저장 |
| `scripts/upload_caption.py` | 자막 트랙 생성 또는 갱신 |
| `scripts/set_localization.py` | 언어별 영상 제목 |
| `scripts/extract_thumbnails.py` | 썸네일 후보 프레임과 갤러리 |

## 라이선스

MIT. [LICENSE](LICENSE)를 보세요.
