# Google Cloud setup for YouTube captions (beginner walkthrough)

> **Agent: re-verify before you recommend.** Everything here was checked
> against official Google pages on **2026-09-22** (source URLs are given per
> step). Google renames menus and changes limits. Before walking a user
> through this, open the cited page for any step you are unsure about. If a
> label on the user's screen differs from this file, trust the screen and the
> current page, and tell the user what you changed.
>
> Items marked **UNVERIFIED** were not confirmed on an official Google page.
> Phrase them to the user as "if your screen looks different, ...".
>
> You (the agent) run the commands. The user does the clicking in their own
> browser. **Never ask the user to paste `client_secret.json` contents, tokens or
> keys into chat.** Ask them to say "done" after each step instead.

> **Console language.** The labels below are the English ones. If your Google
> console is in another language, switch it to English (account menu >
> language) or add `?hl=en` to the end of the page address, for example
> `https://console.cloud.google.com/projectcreate?hl=en`. (Adding `?hl=en` is
> a common Google convention; if your screen differs, use the language
> setting.)
>
> **Which Python?** Commands use `python3`. On Windows use `py -3` (or
> `python`), never `python3`.

## What you are building, and why

To upload captions, this tool must act as you on YouTube. Google requires
that any program doing this be registered as an "app" inside a Google Cloud
**project**. You make your own tiny private app, once. Why your own and not a
shared one: quota (see the end of this file) is counted per project, so your
own project gives you your own daily allowance.

End result: a file `client_secret.json` (the app's ID card) and a token file
(your permission), both stored outside any git repository.

## Before you start

- Use the Google account that **owns** the YouTube channel (or the account
  the channel is managed from). It is the account that will grant permission.
- The video you want to caption must **already be uploaded to YouTube**
  (private or unlisted is fine). Captions are added to an existing video.
  Its **video ID** is the part after `v=` in the watch address, the part after
  `youtu.be/` in a share link, or the ID in the address of the video's
  details page in YouTube Studio.
- One person, one project. Do not share the project with strangers: an
  unverified app is limited to 100 new users in total over the project's whole
  lifetime, and that count cannot be reset.
  [source](https://support.google.com/cloud/answer/15549945)

## Step 1 — Create a project

1. Open <https://console.cloud.google.com/projectcreate> (or in the console:
   Menu > IAM & Admin > Create a Project).
2. Give it any name, for example `youtube-captions`. Click **Create**.
3. Make sure the new project is the one selected at the top of the page.

Why: everything below (the API, the app, the quota) hangs off this project.
Source (verified 2026-09-22):
<https://developers.google.com/workspace/guides/create-project>

## Step 2 — Turn on the YouTube Data API v3

1. Menu > **APIs & Services** > **Library**.
2. Search for **YouTube Data API v3**, open it, click **Enable**.
3. To double-check, open the **Enabled APIs** page; it should be listed.

Why: the project may not talk to YouTube until this switch is on.
Sources (verified 2026-09-22):
<https://developers.google.com/workspace/guides/enable-apis>,
<https://developers.google.com/youtube/v3/guides/auth/installed-apps>,
<https://developers.google.com/youtube/v3/getting-started>

## Step 3 — Set up the "Google Auth platform" (consent screen)

**Order to follow:** Branding, then Audience (External only, no test users),
then Data Access, then Clients (Step 4), then **Publish app** (Step 6).

Menu > **Google Auth platform**. If it asks you to get started, do so. The
left side has these sections: **Branding**, **Audience**, **Data Access**,
**Clients**. (If your screen differs, look for "OAuth consent screen" under
APIs & Services; older console versions used that name. That naming is not
confirmed on the current pages.)

Source (verified 2026-09-22):
<https://developers.google.com/workspace/guides/configure-oauth-consent>

**3a. Branding.** Fill in "App name" (anything, for example `My Caption Tool`)
and "User support email" (your email). Accept Google's "API Services: User
Data Policy" if asked.

**3b. Audience.** Choose **External**.
Why: "Internal" only works for projects that belong to a Google Workspace
organization. A normal personal Google account has none, so External is the
only choice. Also add a developer contact email if asked.
Source: <https://support.google.com/cloud/answer/15549945>

**3c. Test users: skip this.** You will publish the app in Step 6, so you do
not need to add test users. (Adding yourself as a test user is only needed if
you stay in Testing; see the note in Step 6.)

**3d. Data Access.** Add this one scope:

```
https://www.googleapis.com/auth/youtube.force-ssl
```

Why this one: it is the scope that YouTube's caption methods accept, and it
also covers title localization (`videos.update`). Google says to pick the
minimum scopes you need. Sources (verified 2026-09-22):
<https://developers.google.com/youtube/v3/docs/captions/insert>,
<https://developers.google.com/youtube/v3/docs/videos/update>

Google describes this scope as "See, edit, and permanently delete your
YouTube videos, ratings, comments and captions". That is broad, which is
why you keep your token and secret file private.
Source: <https://developers.google.com/youtube/v3/guides/auth/installed-apps>

## Step 4 — Create a Desktop client and DOWNLOAD THE SECRET RIGHT AWAY

1. Google Auth platform > **Clients** > **Create client**
   (direct link: <https://console.developers.google.com/auth/clients>).
2. Application type: **Desktop app**. Give it a name. Click **Create**.
3. **Immediately click the download button in the "OAuth client created"
   dialog** and save the file as `client_secret.json`. (The exact button
   label is UNVERIFIED on official pages; if your screen differs, look for a
   download icon or "Download JSON".)
4. **Rename and move the file.** The downloaded file has a long generated
   name (it looks like `client_secret_12345-abc....json`). Move it to the
   config folder and name it `client_secret.json` (the config folder is
   `$YTCAPTION_HOME` if set, otherwise `~/.claude/credentials/`, on Windows
   `%USERPROFILE%\.claude\credentials\`). The agent runs this for the user;
   replace the file name with the real one.

   Windows PowerShell:
   ```powershell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.claude\credentials" | Out-Null
   Move-Item "$env:USERPROFILE\Downloads\client_secret_XXXX.json" "$env:USERPROFILE\.claude\credentials\client_secret.json"
   ```
   macOS / Linux:
   ```bash
   mkdir -p ~/.claude/credentials
   mv ~/Downloads/client_secret_XXXX.json ~/.claude/credentials/client_secret.json
   ```
   (If the browser saves downloads elsewhere, use that folder. If
   `YTCAPTION_HOME` is set, use that folder instead of `.claude/credentials`,
   and never a Dropbox, Google Drive or OneDrive folder.)

**Why the urgency:** for clients created since April 2025, Google shows the
full client secret **only once**, at creation. If you close the dialog
without downloading, you cannot view it again. The fix is to create a new
secret or a new client (Step 4 again).
Source (verified 2026-09-22): <https://support.google.com/cloud/answer/15549257>

A Desktop client asks for nothing else (no web address needed). Google notes
desktop apps "cannot keep secrets", so this file is not confidential in the
same way as a password, but treat it as private anyway.
Source: <https://developers.google.com/identity/protocols/oauth2/native-app>

## Step 5 — (skip) Verification

Do **not** submit the app for Google verification. Google's guidance for an
app used only by you (fewer than 100 users) is that you can keep using it
without verification and click through the "unverified app" warning when you
sign in.
Source (verified 2026-09-22): <https://support.google.com/cloud/answer/13464323>

## Step 6 — Click "Publish app" (do it now, before logging in)

Do this **immediately**, before Step 7, so the first token you create is not
a 7-day one.

1. Google Auth platform > **Audience** > **Publish app**.
2. Confirm. The status changes from "Testing" to "In production".

**Why this matters:** while an External app is in Testing, permission from a
test user **expires after seven days**, including for the refresh token that
this tool stores. That would break your uploads every week. After publishing,
the seven-day rule for Testing no longer applies.
Sources (verified 2026-09-22): <https://support.google.com/cloud/answer/15549945>,
<https://developers.google.com/identity/protocols/oauth2>

**What we do NOT know (UNVERIFIED):** Google documents the 7-day limit only
for Testing. It does not explicitly guarantee that tokens for an unverified
app that uses a sensitive scope then last indefinitely. Other reasons a
token can still die: you revoke access, it goes unused for 6 months, or the
100-refresh-tokens-per-account-per-client cap is reached. If uploads fail
with `invalid_grant` later, just re-run Step 7.
Source: <https://developers.google.com/identity/protocols/oauth2>

If the user prefers to stay in Testing: it works, but they must re-run Step
7 about once a week.

## Step 7 — Authorize the channel (agent runs this)

```bash
python3 scripts/reauth_channel.py --token my_channel_token.json --secret client_secret.json
```

Add `--expect-channel UCxxxxxxxxxxxxxxxxxxxxxx` (the channel's ID) if the
user manages more than one channel: the script then refuses to save the
token if the login ended up on a different channel. Use the file names stored
in prefs (`token_file`, `client_secret`).

**Run it in the background, or with a 10-minute timeout.** The script opens
the browser, listens on `http://127.0.0.1:<port>` and waits **up to 10
minutes** for the user to finish (it ignores stray requests). An agent shell's
default 120-second timeout would kill it while the user is still clicking.
Tell the user: "You have about 10 minutes for the browser steps."

A browser window opens. Tell the user what to expect:

1. Pick the Google account that owns the channel. If a channel or brand
   account picker appears, pick the channel that owns the videos. (How this
   picker looks is UNVERIFIED on official pages; if your screen differs,
   choose the account or channel that owns the videos.)
2. A warning appears that the app is **unverified** or not verified by Google.
   This is expected for a personal app. Look for an option to continue or
   show advanced details and proceed to your own app. (The exact button
   wording, for example "Advanced" then "Go to <app name> (unsafe)", is
   UNVERIFIED on official pages; if your screen differs, follow the on-screen
   way to continue.)
3. On the permission screen, keep the YouTube permission **ticked** and click
   Allow. (If a "denied" error appears, it means Allow was not clicked;
   re-run and try again.)
4. The browser says it is done; the terminal script saves the token.

The redirect is `http://127.0.0.1:<port>` (a local address on your own
computer). Old copy-paste-code
flows no longer work, so no code needs to be typed anywhere.
Source: <https://developers.google.com/youtube/v3/guides/auth/installed-apps>

## Step 8 — Check that it works

```bash
python3 scripts/check_setup.py
```

Expect `[OK     ]` for the client secret and the token, and the **channel
title and ID printed**. Ask the user: "Is that your channel?" If not, delete
the token file and repeat Step 7, choosing the correct account or channel.

Windows PowerShell:
```powershell
Remove-Item "$env:USERPROFILE\.claude\credentials\my_channel_token.json"
```
macOS / Linux:
```bash
rm ~/.claude/credentials/my_channel_token.json
```
(Use your `token_file` name and your config folder if they differ.)

## Daily quota — what it means for you

Source (verified 2026-09-22): <https://developers.google.com/youtube/v3/determine_quota_cost>
and the per-method pages linked in the table.

- The default is **10,000 units per day** for the YouTube endpoints used here.
  It resets at midnight **Pacific Time**. Every request costs at least 1 unit,
  even a failed one.
- Costs (verified 2026-09-22):

| Action | Units | Source |
|---|---|---|
| Add a caption track (`captions.insert`) | 400 | <https://developers.google.com/youtube/v3/docs/captions/insert> |
| Replace an existing caption track (`captions.update`) | 450 | <https://developers.google.com/youtube/v3/docs/captions/update> |
| List caption tracks (`captions.list`) | 50 | <https://developers.google.com/youtube/v3/docs/captions/list> |
| Delete a caption track (`captions.delete`) | 50 | <https://developers.google.com/youtube/v3/docs/captions/delete> |
| Set localized title (`videos.update`) | 50 | <https://developers.google.com/youtube/v3/docs/videos/update> |
| Read video info (`videos.list`) | 1 | <https://developers.google.com/youtube/v3/determine_quota_cost> |

`captions.download` is not in Google's cost table (UNVERIFIED); this tool
does not use it.

- **How this tool spends units (our arithmetic, not a Google statement):**
  `upload_caption.py` first calls `captions.list` (50) and then inserts (400)
  or updates (450). So a **new track is about 450 units** and a **replaced
  track about 500**. Running `upload_caption.py --list` yourself before an
  upload costs another 50, and every `check_setup.py` run spends 1 unit
  (`channels.list`). A localized title update (`videos.update`) is about 50.
  At 10,000 units per day that is about **22 new tracks per day**.
- **Worked example:** 4 videos x 2 languages = 8 new tracks = 8 x 450 = 3,600
  units, plus about 50 per localized title update (4 x 50 = 200 if you
  localize each title once). About 3,800 units in total, well inside one
  day. Five languages on one video is about 5 x 450 = 2,250 units.
- **Before every batch, tell the user the estimate** and split large batches
  across days. When the day's units run out, uploads fail with
  `quotaExceeded`; wait for the reset.
  Midnight Pacific Time is approximately 16:00-17:00 in Korea (16:00 in
  summer daylight time, 17:00 in winter; approximate, check the date).
- Check usage: the **Quotas** page in the Google Cloud console (general path:
  IAM & Admin > Quotas & System Limits). The YouTube-specific console path is
  UNVERIFIED; if your screen differs, use the general path.
  Source: <https://docs.cloud.google.com/docs/quotas/view-manage>
- More quota: fill in Google's "YouTube API Services - Audit and Quota
  Extension Form" (<https://support.google.com/youtube/contact/yt_api_form>).
  Google says you must first complete an audit of your compliance with its
  terms
  (<https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits>).
  For a personal tool this is usually not worth it. Whether the generic
  Cloud "Edit quota" form works for YouTube is UNVERIFIED.
- Quota is per project, so each person using this tool with their own
  project gets their own 10,000 per day.
- Note: Google recently split some buckets (`search.list` and `videos.insert`
  now have their own limits, and default upload cost fell to about 100 units).
  Neither is used by this tool. Source:
  <https://developers.google.com/youtube/v3/revision_history>

## YouTube-side requirements and limits

- Phone verification of the channel unlocks long uploads, custom thumbnails
  and similar features
  (<https://support.google.com/youtube/answer/171664>). Whether the caption
  API requires a verified channel is UNVERIFIED (no official statement
  found either way). If uploads are refused and nothing else explains it,
  checking whether the channel is phone-verified is a reasonable guess, not
  a confirmed cause.
- You can only caption videos on the channel you authorized. Captions on
  someone else's video should be expected to return 403 `forbidden`
  (inference, not stated in Google's docs).
- Caption file limit: 100 MB.
  Source: <https://developers.google.com/youtube/v3/docs/captions/insert>
- `snippet.isDraft=true` makes a track not publicly visible (this is what
  `upload_caption.py --draft` uses).
  Source: <https://developers.google.com/youtube/v3/docs/captions>
- Setting a localized title needs the video's default language to be set,
  otherwise the error `defaultLanguageNotSet` appears. (`set_localization.py`
  handles this; see SKILL.md step ⑤.)
  Source: <https://developers.google.com/youtube/v3/docs/videos/update>
- A service account cannot be used here: the Data API allows them only for
  content owners (multi-channel partners).
  Source: <https://developers.google.com/youtube/registering_an_application>

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| "Access blocked ... has not completed the Google verification process" (error 403 `access_denied`) | App is in Testing and the signing-in account is not in Test users | Add the account under Google Auth platform > Audience > Test users, or Publish app (Step 6). The exact error text comes from community reports; UNVERIFIED on official pages. Official basis: <https://support.google.com/cloud/answer/15549945> |
| Worked, then failed about a week later with `invalid_grant` | App still in Testing: 7-day expiry | Publish app (Step 6), then re-run Step 7. <https://support.google.com/cloud/answer/15549945> |
| `invalid_grant` much later | Access revoked, unused 6 months, or too many tokens for that account and client (oldest is silently cancelled) | Re-run Step 7 and keep only the newest token. <https://developers.google.com/identity/protocols/oauth2> |
| `redirect_uri_mismatch` | Wrong client type, or an old flow | Create a **Desktop app** client (not Web); do not use copy-paste-code flows. <https://developers.google.com/identity/protocols/oauth2/native-app> |
| `error=access_denied` in the browser after clicking | The request was declined | Re-run Step 7 and click Allow with the YouTube permission ticked (the checkbox detail is UNVERIFIED). <https://developers.google.com/youtube/v3/guides/auth/installed-apps> |
| "Unverified app" warning | Sensitive scope, no verification | Expected for personal use; continue (Step 7 point 2). <https://support.google.com/cloud/answer/13464323> |
| 403 `quotaExceeded` | The day's 10,000 units are used up | Wait for the midnight-Pacific reset, upload fewer languages per day, or request an extension (see Quota). <https://developers.google.com/youtube/v3/determine_quota_cost> |
| 403 `forbidden` on caption calls | Wrong scope, wrong account or channel, or not the video's owner | Re-authorize with the account that owns the channel (Step 7). <https://developers.google.com/youtube/v3/docs/captions/insert> |
| 409 `captionExists` | A track with that language and name already exists | This tool updates the existing track instead; or use a different track name. <https://developers.google.com/youtube/v3/docs/captions/insert> |
| Lost `client_secret.json` | Secrets are only viewable at creation (clients made after April 2025) | Create a new secret or a new client (Step 4). <https://support.google.com/cloud/answer/15549257> |

## Honest summary of what is not confirmed

1. Whether Google officially labels `youtube.force-ssl` as "sensitive" (very
   likely, but only third-party sources said so).
2. The exact label of the secret-download button and of the "continue
   anyway" buttons on the unverified-app warning.
3. A written guarantee that tokens do not expire after 7 days once published.
4. Whether captions require a phone-verified channel.
5. How the brand-channel picker behaves at consent.
6. The YouTube-specific quota page path in the console.
