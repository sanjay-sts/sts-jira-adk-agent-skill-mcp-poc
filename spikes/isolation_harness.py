"""Phase 2 isolation harness — the rigorous cross-talk proof (Level 2/3, against a running agent).

Drives the forwarding agent's /chat concurrently with multiple users' real tokens and asserts
no cross-talk on either surface. This is the verdict the 3-human demo can't give (humans can't
produce the overlap/volume a race needs).

Ground truth, two layers per request:
  • server layer  — ChatResponse.user_id is the SERVER-resolved Atlassian accountId; assert it
                    equals the calling token's expected accountId.
  • agent layer   — the agent is asked to echo its accountId via atlassianUserInfo (which uses
                    the forwarded bearer); assert the response contains the OWN accountId and
                    NONE of the other users' accountIds.

Cases:
  A. concurrent multi-user        — many overlapping requests across users; zero crossing.
  B. sequential residual          — A then B then A on the same warm process; each stays itself.
  C. hijack                       — B's token on A's conversation_id; resolves to B, never A.
  D. multi-conversation history   — A says a secret in conv1; conv2 (same user) must not know it.

Config (secrets — do NOT commit): spikes/harness_config.json (see .example). Needs >=2 users
with DIFFERENT accountIds (ideally unequal permissions, so a leak is detectable).

    cp spikes/harness_config.example.json spikes/harness_config.json   # then fill in tokens
    just serve            # agent on :8080 (with AWS creds), in another terminal
    uv run python spikes/isolation_harness.py
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass

import httpx

WHOAMI = (
    "Call the atlassianUserInfo tool and reply with ONLY my Atlassian accountId "
    "(the account_id value), nothing else."
)


@dataclass(frozen=True)
class User:
    label: str
    token: str
    account_id: str


@dataclass(frozen=True)
class Config:
    agent_url: str
    users: list[User]


def load_config() -> Config:
    path = os.environ.get("HARNESS_CONFIG", "spikes/harness_config.json")
    with open(path) as f:
        raw = json.load(f)
    users = [User(u["label"], u["token"], str(u["account_id"])) for u in raw["users"]]
    if len({u.account_id for u in users}) < 2:
        raise SystemExit("Need >=2 users with DIFFERENT accountIds (a leak must be detectable).")
    return Config(agent_url=raw.get("agent_url", "http://127.0.0.1:8080").rstrip("/"), users=users)


async def _chat(
    http: httpx.AsyncClient, cfg: Config, user: User, message: str, conversation_id: str
) -> dict[str, str]:
    r = await http.post(
        f"{cfg.agent_url}/chat",
        json={"user_id": user.label, "message": message, "conversation_id": conversation_id},
        headers={"Authorization": f"Bearer {user.token}"},
    )
    r.raise_for_status()
    return r.json()


def _check_identity(resp: dict[str, str], caller: User, others: list[User]) -> list[str]:
    """Return a list of failure strings (empty == clean) for an identity-probe response."""
    fails: list[str] = []
    if resp.get("user_id") != caller.account_id:
        fails.append(
            f"server-resolved user_id={resp.get('user_id')!r} != caller {caller.account_id!r}"
        )
    body = resp.get("response", "") or ""
    if caller.account_id not in body:
        fails.append(f"own accountId {caller.account_id!r} missing from agent response")
    for o in others:
        if o.account_id != caller.account_id and o.account_id in body:
            fails.append(
                f"LEAK: {o.label}'s accountId {o.account_id!r} in {caller.label}'s response"
            )
    return fails


async def test_a_concurrent(http: httpx.AsyncClient, cfg: Config, rounds: int = 10) -> list[str]:
    """Many overlapping identity probes across all users at once."""
    async def one(user: User) -> list[str]:
        resp = await _chat(http, cfg, user, WHOAMI, uuid.uuid4().hex)
        others = [u for u in cfg.users if u is not user]
        return _check_identity(resp, user, others)

    tasks = [one(u) for u in cfg.users for _ in range(rounds)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    fails: list[str] = []
    for r in results:
        if isinstance(r, BaseException):
            fails.append(f"request error: {r!r}")
        else:
            fails.extend(r)
    return fails


async def test_b_sequential(http: httpx.AsyncClient, cfg: Config) -> list[str]:
    """A, then B, then A again — catches residual state on a warm process."""
    a, b = cfg.users[0], cfg.users[1]
    fails: list[str] = []
    for user in (a, b, a):
        resp = await _chat(http, cfg, user, WHOAMI, uuid.uuid4().hex)
        fails.extend(_check_identity(resp, user, [a, b]))
    return fails


async def test_c_hijack(http: httpx.AsyncClient, cfg: Config) -> list[str]:
    """B presents a valid token but names A's conversation_id — must resolve to B, never A."""
    a, b = cfg.users[0], cfg.users[1]
    a_conv = uuid.uuid4().hex
    await _chat(http, cfg, a, WHOAMI, a_conv)  # seed A's conversation
    resp = await _chat(http, cfg, b, WHOAMI, a_conv)  # B tries to ride A's conversation_id
    fails: list[str] = []
    if resp.get("user_id") != b.account_id:
        fails.append(f"hijack: resolved to {resp.get('user_id')!r}, expected B {b.account_id!r}")
    if a.account_id in (resp.get("response", "") or ""):
        fails.append(f"hijack LEAK: A's accountId {a.account_id!r} appeared for B")
    return fails


async def test_d_history(http: httpx.AsyncClient, cfg: Config) -> list[str]:
    """Same user, two conversations: a secret in conv1 must not surface in conv2."""
    a = cfg.users[0]
    secret = "ZULU-" + uuid.uuid4().hex[:6]
    conv1, conv2 = uuid.uuid4().hex, uuid.uuid4().hex
    await _chat(http, cfg, a, f"Remember this secret code for later: {secret}.", conv1)
    ask = "What secret code did I give you earlier? If none, say NONE."
    resp = await _chat(http, cfg, a, ask, conv2)
    body = resp.get("response", "") or ""
    if secret in body:
        return [f"history LEAK: secret {secret!r} from conv1 surfaced in conv2"]
    return []


async def main() -> int:
    cfg = load_config()
    print(f"Isolation harness → {cfg.agent_url}  ({len(cfg.users)} users)")
    async with httpx.AsyncClient(timeout=300.0) as http:
        suites = [
            ("A concurrent multi-user", test_a_concurrent(http, cfg)),
            ("B sequential residual", test_b_sequential(http, cfg)),
            ("C hijack", test_c_hijack(http, cfg)),
            ("D multi-conversation history", test_d_history(http, cfg)),
        ]
        any_fail = False
        for name, coro in suites:
            try:
                fails = await coro
            except Exception as e:
                fails = [f"suite crashed: {e!r}"]
            if fails:
                any_fail = True
                print(f"\n[FAIL] {name}")
                for f in fails:
                    print(f"   - {f}")
            else:
                print(f"[PASS] {name}")

    verdict = "RESULT: FAIL — cross-talk detected" if any_fail else "RESULT: PASS — no cross-talk"
    print("\n" + verdict)
    return 1 if any_fail else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
