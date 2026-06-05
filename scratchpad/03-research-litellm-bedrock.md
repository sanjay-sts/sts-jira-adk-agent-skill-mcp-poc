# 03 — Research: LiteLLM × AWS Bedrock (Claude Sonnet 4.6)

**Date:** 2026-05-27
**Method:** AWS Bedrock model card (live), LiteLLM Bedrock docs + Context7 (`/berriai/litellm`).
**Scope:** Verify the exact Bedrock model ID and the LiteLLM model string, plus credential
precedence for the SSO setup.

---

## ⚠️ HEADLINE: the reference's model ID is wrong and will fail at runtime

**Reference default:** `bedrock/anthropic.claude-sonnet-4-6-20250929-v1:0`

This is wrong on **two** counts, per the [AWS Claude Sonnet 4.6 model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html):

1. **The ID suffix doesn't exist.** Sonnet 4.6's canonical base model ID is
   **`anthropic.claude-sonnet-4-6`** — *no* `-YYYYMMDD-v1:0` suffix. (That suffix pattern
   was used by older 4.x IDs and is still used by e.g. Haiku 4.5
   `…claude-haiku-4-5-20251001-v1:0`, but Sonnet 4.6 dropped it.) The date `20250929` is
   also nonsensical: the model **launched 2026-02-17**.
2. **`us-east-1` has no In-Region inference for this model.** The regional table shows
   us-east-1 = ❌ In-Region, ✅ Geo, ✅ Global. Newer Claude models reject on-demand calls
   to the *base* ID with: *"on-demand throughput isn't supported, retry with an inference
   profile."*

### ✅ Correct model string for our setup (us-east-1, SSO)
```
bedrock/us.anthropic.claude-sonnet-4-6
```
- `us.` = the **Geo US** cross-region inference profile (covers us-east-1/-2, us-west-2, ca-*).
- Alternatives: `bedrock/global.anthropic.claude-sonnet-4-6` (Global, max availability),
  or `eu.` / `au.` / `jp.` for other geos.
- To force LiteLLM's Converse route explicitly: `bedrock/converse/us.anthropic.claude-sonnet-4-6`
  (LiteLLM auto-selects Converse for Claude on Bedrock anyway).

**Change in our build:** set this as the `BEDROCK_MODEL_ID` default in `agent.py` **and**
in `.env.example`.

---

## Model facts (for sizing prompts / SKILL injection budget)
- Context window: **1M tokens**; Max output: **64K**; Reasoning: supported.
- Knowledge cutoff: **Aug 2025**. Launch: **2026-02-17**.
- Prompt caching supported (system/messages/tools; 5-min & 1-hour TTL) — useful later for
  the always-injected SKILL.md block, not required for Phase 1.

---

## LiteLLM version constraint
ADK 2.0.0 pins **`litellm>=1.83.7,<=1.83.14`** (via the `[litellm]` extra). That range is
recent enough to parse `us.`/`global.` inference-profile IDs correctly (the old parsing bugs
were 2025 / claude-3.7-era). We do **not** separately pin litellm — let `google-adk[litellm]`
drive it.

---

## Credentials & region (SSO path)
LiteLLM → boto3 standard provider chain. Precedence:
1. Env: `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN`
2. `AWS_PROFILE` (e.g. `mainadmin` with an active SSO session) ← our default
3. EC2/ECS instance metadata

Region from `AWS_REGION` (default `us-east-1`). Operational notes:
- `aws sso login --profile mainadmin` before running; SSO session expiry surfaces as
  `ExpiredTokenException` from Bedrock → re-login. (Already in the reference README's
  troubleshooting table — keep it.)
- Bedrock `bedrock:InvokeModel` AccessDenied = the model isn't enabled in the account →
  enable Claude Sonnet 4.6 in the Bedrock console.
- LiteLLM also accepts `aws_region_name`, `aws_profile_name` as explicit params if we ever
  want to avoid env reliance.

---

## Sources
- AWS Bedrock — Claude Sonnet 4.6 model card — https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html
- "Claude Sonnet 4.6 now available in Amazon Bedrock" — https://aws.amazon.com/about-aws/whats-new/2026/02/claude-sonnet-4.6-available-in-amazon-bedrock/
- LiteLLM AWS Bedrock docs — https://docs.litellm.ai/docs/providers/bedrock
- LiteLLM #9780 (regional inference profile IDs) — https://github.com/BerriAI/litellm/issues/9780
