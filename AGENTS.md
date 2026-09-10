# AGENTS.md — ytcaption

This repo is a [Claude Code skill](https://claude.com/claude-code)
(`SKILL.md` with frontmatter) that Claude Code auto-discovers. Other
agents (Codex, etc.) don't read `SKILL.md` automatically — this file is
the bridge.

## When to use this

Any task involving: translating an editor-exported SRT (CapCut, Premiere)
into another language, uploading a YouTube caption track, localizing a
video title per-locale, or extracting thumbnail candidates from a video.

## What to do

Read **[SKILL.md](SKILL.md)** in full before starting. It is the actual
workflow spec — pipeline steps ⓪ through ⑥, the merge-before-translate
rule, the mechanical/nuance verification gates, and the upload/localization
CLI usage. Follow it as written; don't skip ⓪ (frame inspection) or ②
(mechanical verification) even under time pressure — both exist because
skipping them produces silently wrong captions.

The `scripts/` directory is plain-stdlib Python — run any script directly:

```bash
python3 scripts/verify_srt.py source.srt translation.srt
```

## Guardrails

- Steps ④ (`upload_caption.py`) and ⑤ (`set_localization.py`) mutate
  public YouTube metadata. Get explicit user approval before running
  either — SKILL.md says this too, but it bears repeating here since this
  file is what agents without skill auto-loading will actually read first.
- Never commit OAuth tokens or `client_secret.json` — see `.gitignore`.
