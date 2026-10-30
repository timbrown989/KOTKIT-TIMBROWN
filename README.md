# KOTKIT-TIMBROWN

## Automatic TikTok posting

The workflow in `.github/workflows/tiktok-post.yml` posts newly added `.mp4`,
`.mov`, and `.webm` files under `videos/` when pushed to `main`. It uses TikTok
Direct Post with `FILE_UPLOAD` and `SELF_ONLY` privacy.

Before enabling it, configure a TikTok developer app with the Content Posting
API product and Direct Post enabled, and request and approve the `video.publish`
scope. Complete OAuth for the connected account, then add these repository
Actions secrets under **Settings > Secrets and variables > Actions**:

- `TIKTOK_CLIENT_KEY`
- `TIKTOK_CLIENT_SECRET`
- `TIKTOK_ACCESS_TOKEN`
- `TIKTOK_REFRESH_TOKEN`

The workflow refreshes the access token before posting. If TikTok rotates the
refresh token, the run needs a `TIKTOK_SECRETS_UPDATE_TOKEN` secret containing a
fine-grained token with **Secrets: read and write** permission for this
repository so it can save the replacement tokens. Without it, the workflow
fails clearly and requests OAuth/token rotation.

Unaudited apps can post only privately (`SELF_ONLY`) to private accounts. Use
H.264 MP4 where possible: 23-60 FPS, 360-4096 px, up to 10 minutes and 4 GB.
After audit, expect a per-creator daily posting cap of about 15 posts per
24 hours, plus separate endpoint rate limits. TikTok may enforce additional
account- and app-specific limits.
