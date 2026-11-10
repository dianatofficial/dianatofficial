#!/usr/bin/env python3
"""
Dynamically fetches live GitHub profile statistics via GraphQL API and
regenerates sleek, self-hosted SVG cards (`assets/stats-and-languages.svg`
and `assets/contributions-graph.svg`) with zero third-party server dependencies.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import math
import os
import re
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT_DIR / "assets"
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME", "dianatofficial")

# Sleek dark-neon palette overrides for popular languages
LANG_PALETTE = {
    "TypeScript": "#38bdf8",
    "Python": "#10b981",
    "HTML": "#f59e0b",
    "CSS": "#a855f7",
    "Go": "#22d3ee",
    "JavaScript": "#eab308",
    "Jupyter Notebook": "#fb923c",
    "Shell": "#94a3b8",
    "Dockerfile": "#0ea5e9",
}


def get_token() -> str:
    """Resolve GitHub token from env (Actions) or local `gh` CLI."""
    token = os.environ.get("GH_STATS_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token.strip()
    return subprocess.check_output(["gh", "auth", "token"], text=True).strip()


def graphql(query: str, variables: dict[str, Any] | None = None, token: str = "") -> dict[str, Any]:
    """Execute a GitHub GraphQL request and return parsed JSON data."""
    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": f"{GITHUB_USERNAME}-profile-updater",
        },
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if "errors" in body:
        raise RuntimeError(f"GitHub GraphQL errors: {body['errors']}")
    return body["data"]


def fetch_profile_data(username: str, token: str) -> dict[str, Any]:
    """Fetch complete user stats, all owned public repos, and multi-year contributions."""
    summary_query = """
    query($login: String!) {
      user(login: $login) {
        name
        login
        repositoriesPublicNonFork: repositories(privacy: PUBLIC, ownerAffiliations: OWNER, isFork: false) {
          totalCount
        }
        repositoriesPublicAll: repositories(privacy: PUBLIC, ownerAffiliations: OWNER) {
          totalCount
        }
        repositoriesContributedTo(includeUserRepositories: true, contributionTypes: [COMMIT, PULL_REQUEST, ISSUE, REPOSITORY]) {
          totalCount
        }
        pullRequestsMerged: pullRequests(states: MERGED) {
          totalCount
        }
        pullRequestsAll: pullRequests {
          totalCount
        }
        issues {
          totalCount
        }
        contributionsCollection {
          totalCommitContributions
          restrictedContributionsCount
          contributionYears
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                contributionCount
                date
              }
            }
          }
        }
      }
    }
    """
    user = graphql(summary_query, {"login": username}, token)["user"]

    # Paginate all public owned non-fork repositories for accurate stars & language weights
    repos: list[dict[str, Any]] = []
    cursor: str | None = None
    repo_query = """
    query($login: String!, $cursor: String) {
      user(login: $login) {
        repositories(first: 100, after: $cursor, privacy: PUBLIC, ownerAffiliations: OWNER, isFork: false) {
          pageInfo {
            hasNextPage
            endCursor
          }
          nodes {
            name
            stargazerCount
            languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
              edges {
                size
                node {
                  name
                  color
                }
              }
            }
          }
        }
      }
    }
    """
    while True:
        page = graphql(repo_query, {"login": username, "cursor": cursor}, token)["user"]["repositories"]
        repos.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    # Aggregate multi-year total contributions & commits
    years = user["contributionsCollection"]["contributionYears"]
    all_time_contributions = 0
    all_time_commits = 0
    year_query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          totalCommitContributions
          restrictedContributionsCount
          contributionCalendar {
            totalContributions
          }
        }
      }
    }
    """
    for yr in years:
        cc_yr = graphql(
            year_query,
            {"login": username, "from": f"{yr}-01-01T00:00:00Z", "to": f"{yr}-12-31T23:59:59Z"},
            token,
        )["user"]["contributionsCollection"]
        all_time_contributions += cc_yr["contributionCalendar"]["totalContributions"]
        all_time_commits += cc_yr["totalCommitContributions"] + cc_yr["restrictedContributionsCount"]

    return {
        "user": user,
        "repos": repos,
        "all_time_contributions": all_time_contributions,
        "all_time_commits": all_time_commits,
    }


def fetch_achievements(username: str) -> list[str]:
    """Fetch currently unlocked public achievement names from GitHub profile."""
    try:
        req = urllib.request.Request(
            f"https://github.com/{username}?tab=achievements",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            page_html = resp.read().decode("utf-8", errors="ignore")
        names = re.findall(r'alt="Achievement:\s*([^"]+)"', page_html)
        unique: list[str] = []
        for name in names:
            clean = name.strip()
            if clean and clean not in unique:
                unique.append(clean)
        return unique
    except Exception:
        return ["Pair Extraordinaire", "YOLO", "Quickdraw"]


def format_compact(n: int) -> str:
    if n >= 1000:
        return f"{n:,}"
    return str(n)


def build_stats_and_languages_svg(data: dict[str, Any], sync_date: str) -> str:
    user = data["user"]
    repos = data["repos"]

    total_stars = sum(r["stargazerCount"] for r in repos)
    total_commits = data["all_time_commits"]
    merged_prs = user["pullRequestsMerged"]["totalCount"]
    total_prs = user["pullRequestsAll"]["totalCount"]
    contributed_to = user["repositoriesContributedTo"]["totalCount"]

    # Language breakdown across public non-fork repos
    lang_bytes: dict[str, int] = {}
    lang_colors: dict[str, str] = {}
    for repo in repos:
        for edge in repo["languages"]["edges"]:
            name = edge["node"]["name"]
            lang_bytes[name] = lang_bytes.get(name, 0) + edge["size"]
            lang_colors[name] = LANG_PALETTE.get(name, edge["node"].get("color") or "#64748b")

    sorted_langs = sorted(lang_bytes.items(), key=lambda kv: kv[1], reverse=True)
    top4 = sorted_langs[:4]
    top4_total = sum(v for _, v in top4) or 1
    raw_pcts = [(byte_count / top4_total) * 100.0 for _, byte_count in top4]
    rounded_pcts = [round(p, 1) for p in raw_pcts]
    if rounded_pcts:
        diff = round(100.0 - sum(rounded_pcts), 1)
        rounded_pcts[0] = round(rounded_pcts[0] + diff, 1)

    radius = 48.0
    circumference = 2.0 * math.pi * radius
    circles_svg = []
    legend_svg = []

    offset = 0.0
    for idx, (lang_name, byte_count) in enumerate(top4):
        pct = rounded_pcts[idx]
        arc_len = (byte_count / top4_total) * circumference
        color = lang_colors[lang_name]
        circles_svg.append(
            f'      <circle cx="0" cy="0" r="48" fill="none" stroke="{color}" stroke-width="20" '
            f'stroke-dasharray="{arc_len:.2f} {circumference:.2f}" stroke-dashoffset="{-offset:.2f}" transform="rotate(-90)" />'
        )
        offset += arc_len

        y_row = idx * 26
        safe_lang = html.escape(lang_name)
        legend_svg.append(
            f'      <rect x="0" y="{y_row}" width="12" height="12" rx="3" fill="{color}" />\n'
            f'      <text x="20" y="{y_row + 11}" fill="#f1f5f9" font-family="sans-serif" font-size="13" font-weight="500">{safe_lang}</text>\n'
            f'      <text x="165" y="{y_row + 11}" fill="#94a3b8" font-family="sans-serif" font-size="12" text-anchor="end">{pct:.1f}%</text>'
        )

    circles_block = "\n".join(circles_svg)
    legend_block = "\n".join(legend_svg)

    return f"""<svg width="100%" height="200" viewBox="0 0 840 200" fill="none" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="cardGrad" x1="0" y1="0" x2="405" y2="200" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#0e1322" />
      <stop offset="100%" stop-color="#080b14" />
    </linearGradient>
    <linearGradient id="borderLeft" x1="0" y1="0" x2="405" y2="200" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#38bdf8" stop-opacity="0.5" />
      <stop offset="100%" stop-color="#8b5cf6" stop-opacity="0.2" />
    </linearGradient>
    <linearGradient id="borderRight" x1="0" y1="0" x2="405" y2="200" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#8b5cf6" stop-opacity="0.5" />
      <stop offset="100%" stop-color="#10b981" stop-opacity="0.2" />
    </linearGradient>
    <linearGradient id="octoGlow" x1="0" y1="0" x2="100" y2="100" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#8b5cf6" stop-opacity="0.3" />
      <stop offset="100%" stop-color="#38bdf8" stop-opacity="0.08" />
    </linearGradient>
  </defs>

  <!-- ================= LEFT CARD: STATS ================= -->
  <g transform="translate(2, 2)">
    <rect width="405" height="196" rx="14" fill="url(#cardGrad)" stroke="url(#borderLeft)" stroke-width="1.2" />
    <text x="25" y="34" fill="#38bdf8" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="15" font-weight="700">Engineering Stats</text>
    <text x="380" y="33" fill="#64748b" font-family="sans-serif" font-size="10" text-anchor="end">Live Sync: {sync_date}</text>
    <line x1="25" y1="44" x2="380" y2="44" stroke="#1e293b" stroke-width="1" />

    <text x="25" y="74" fill="#94a3b8" font-family="sans-serif" font-size="13">⭐ Total Stars Earned:</text>
    <text x="210" y="74" fill="#f8fafc" font-family="sans-serif" font-size="13" font-weight="700">{format_compact(total_stars)}</text>

    <text x="25" y="100" fill="#94a3b8" font-family="sans-serif" font-size="13">📦 Total Commits:</text>
    <text x="210" y="100" fill="#f8fafc" font-family="sans-serif" font-size="13" font-weight="700">{format_compact(total_commits)}</text>

    <text x="25" y="126" fill="#94a3b8" font-family="sans-serif" font-size="13">🔀 Merged Pull Requests:</text>
    <text x="210" y="126" fill="#38bdf8" font-family="sans-serif" font-size="13" font-weight="700">{format_compact(merged_prs)} / {format_compact(total_prs)}</text>

    <text x="25" y="152" fill="#94a3b8" font-family="sans-serif" font-size="13">🐛 Issues Tracked:</text>
    <text x="210" y="152" fill="#c084fc" font-family="sans-serif" font-size="13" font-weight="700">{user["issues"]["totalCount"]}</text>

    <text x="25" y="178" fill="#94a3b8" font-family="sans-serif" font-size="13">🌐 Contributed Repos:</text>
    <text x="210" y="178" fill="#f8fafc" font-family="sans-serif" font-size="13" font-weight="700">{contributed_to}</text>

    <g transform="translate(295, 68)">
      <circle cx="45" cy="45" r="42" fill="url(#octoGlow)" />
      <path d="M45 15C28.43 15 15 28.43 15 45C15 58.26 23.6 69.49 35.53 73.46C37.03 73.73 37.58 72.81 37.58 72.01C37.58 71.3 37.55 69.42 37.54 66.92C29.2 68.73 27.44 62.89 27.44 62.89C26.08 59.43 24.12 58.51 24.12 58.51C21.4 56.65 24.32 56.69 24.32 56.69C27.33 56.9 28.91 59.78 28.91 59.78C31.58 64.36 35.92 63.04 37.63 62.27C37.9 60.34 38.67 59.02 39.52 58.27C32.86 57.51 25.86 54.94 25.86 43.46C25.86 40.19 27.03 37.51 28.95 35.42C28.64 34.66 27.62 31.62 29.25 27.5C29.25 27.5 31.77 26.69 37.5 30.58C39.9 29.91 42.45 29.58 45 29.57C47.55 29.58 50.1 29.91 52.5 30.58C58.23 26.69 60.75 27.5 60.75 27.5C62.38 31.62 61.36 34.66 61.05 35.42C62.97 37.51 64.14 40.19 64.14 43.46C64.14 54.98 57.12 57.5 50.44 58.24C51.52 59.17 52.48 61 52.48 63.78C52.48 67.76 52.44 70.97 52.44 71.95C52.44 72.76 52.98 73.71 54.51 73.41C66.41 69.41 75 58.22 75 45C75 28.43 61.57 15 45 15Z" fill="#a855f7" fill-opacity="0.75" />
    </g>
  </g>

  <!-- ================= RIGHT CARD: LANGUAGES ================= -->
  <g transform="translate(433, 2)">
    <rect width="405" height="196" rx="14" fill="url(#cardGrad)" stroke="url(#borderRight)" stroke-width="1.2" />
    <text x="25" y="34" fill="#a855f7" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="15" font-weight="700">Top Languages by Code</text>
    <text x="380" y="33" fill="#64748b" font-family="sans-serif" font-size="10" text-anchor="end">{len(repos)} Public Repos</text>
    <line x1="25" y1="44" x2="380" y2="44" stroke="#1e293b" stroke-width="1" />

    <g transform="translate(95, 118)">
      <circle cx="0" cy="0" r="48" fill="none" stroke="#1e293b" stroke-width="20" />
{circles_block}
      <text x="0" y="4" fill="#f8fafc" font-family="sans-serif" font-size="11" font-weight="700" text-anchor="middle">STACK</text>
    </g>

    <g transform="translate(185, 68)">
{legend_block}
    </g>
  </g>
</svg>
"""


def smooth_bezier_path(points: list[tuple[float, float]]) -> str:
    """Create a smooth cubic SVG spline through a list of (x, y) coordinates."""
    if len(points) < 2:
        return ""
    d = [f"M {points[0][0]:.1f} {points[0][1]:.1f}"]
    for i in range(len(points) - 1):
        p0 = points[i - 1] if i > 0 else points[i]
        p1 = points[i]
        p2 = points[i + 1]
        p3 = points[i + 2] if i + 2 < len(points) else points[i + 1]
        c1x = p1[0] + (p2[0] - p0[0]) / 6.0
        c1y = p1[1] + (p2[1] - p0[1]) / 6.0
        c2x = p2[0] - (p3[0] - p1[0]) / 6.0
        c2y = p2[1] - (p3[1] - p1[1]) / 6.0
        # Clamp Y within graph floor/ceiling so curves stay crisp
        c1y = max(56.0, min(160.0, c1y))
        c2y = max(56.0, min(160.0, c2y))
        d.append(f"C {c1x:.1f} {c1y:.1f}, {c2x:.1f} {c2y:.1f}, {p2[0]:.1f} {p2[1]:.1f}")
    return " ".join(d)


def build_contributions_graph_svg(data: dict[str, Any], achievements: list[str], sync_date: str) -> str:
    user = data["user"]
    display_name = html.escape(user.get("name") or GITHUB_USERNAME)
    public_non_fork = user["repositoriesPublicNonFork"]["totalCount"]
    all_time_contribs = data["all_time_contributions"]
    year_contribs = user["contributionsCollection"]["contributionCalendar"]["totalContributions"]

    # Bucket last 12 months from contributionCalendar
    monthly_totals: dict[str, int] = {}
    for week in user["contributionsCollection"]["contributionCalendar"]["weeks"]:
        for day in week["contributionDays"]:
            ym = day["date"][:7]
            monthly_totals[ym] = monthly_totals.get(ym, 0) + day["contributionCount"]

    sorted_months = sorted(monthly_totals.items())[-12:]
    if not sorted_months:
        sorted_months = [(sync_date[:7], 0)]

    # Use square-root scaling so an extreme burst day doesn't flatten normal active months
    values = [v for _, v in sorted_months]
    max_val = max(values) or 1
    scaled = [math.sqrt(v / max_val) for v in values]

    x_start, x_end = 395.0, 775.0
    y_bottom, y_top = 160.0, 66.0
    n = len(scaled)
    points: list[tuple[float, float]] = []
    for idx, s in enumerate(scaled):
        x = x_start if n == 1 else x_start + (idx / (n - 1)) * (x_end - x_start)
        y = y_bottom - s * (y_bottom - y_top)
        points.append((x, y))

    stroke_d = smooth_bezier_path(points)
    fill_d = f"{stroke_d} L {points[-1][0]:.1f} {y_bottom:.1f} L {points[0][0]:.1f} {y_bottom:.1f} Z"

    peak_idx = max(range(len(values)), key=lambda i: values[i])
    peak_x, peak_y = points[peak_idx]
    peak_val = values[peak_idx]

    # Axis month labels (first, ~1/3, ~2/3, last)
    label_indices = sorted({0, n // 3, (2 * n) // 3, n - 1})
    month_labels_svg = []
    for i in label_indices:
        ym = sorted_months[i][0]
        lbl = dt.datetime.strptime(ym, "%Y-%m").strftime("%b '%y")
        if i == n - 1:
            lbl = "Now"
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        month_labels_svg.append(
            f'    <text x="{points[i][0]:.1f}" y="180" fill="#64748b" font-family="sans-serif" font-size="10" text-anchor="{anchor}">{lbl}</text>'
        )
    month_labels_block = "\n".join(month_labels_svg)

    achievements_text = " · ".join(achievements) if achievements else "Open-Source Contributor"
    if len(achievements_text) > 46:
        achievements_text = " · ".join(achievements[:3])

    return f"""<svg width="100%" height="220" viewBox="0 0 840 220" fill="none" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="bgGrad2" x1="0" y1="0" x2="840" y2="220" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#0b0f19" />
      <stop offset="100%" stop-color="#070a12" />
    </linearGradient>
    <linearGradient id="borderGrad2" x1="0" y1="0" x2="840" y2="220" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#8b5cf6" stop-opacity="0.5" />
      <stop offset="50%" stop-color="#38bdf8" stop-opacity="0.3" />
      <stop offset="100%" stop-color="#10b981" stop-opacity="0.4" />
    </linearGradient>
    <linearGradient id="waveFill" x1="0" y1="55" x2="0" y2="165" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#a855f7" stop-opacity="0.5" />
      <stop offset="65%" stop-color="#6366f1" stop-opacity="0.18" />
      <stop offset="100%" stop-color="#0b0f19" stop-opacity="0.0" />
    </linearGradient>
    <linearGradient id="waveStroke" x1="395" y1="0" x2="775" y2="0" gradientUnits="userSpaceOnUse">
      <stop offset="0%" stop-color="#38bdf8" />
      <stop offset="50%" stop-color="#a855f7" />
      <stop offset="100%" stop-color="#10b981" />
    </linearGradient>
  </defs>

  <rect x="2" y="2" width="836" height="216" rx="14" fill="url(#bgGrad2)" stroke="url(#borderGrad2)" stroke-width="1.2" />

  <text x="32" y="38" fill="#38bdf8" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif" font-size="18" font-weight="700">{GITHUB_USERNAME} ({display_name})</text>
  <text x="808" y="36" fill="#64748b" font-family="sans-serif" font-size="11" font-weight="600" text-anchor="end">12-Month Velocity · Updated {sync_date}</text>
  <line x1="32" y1="48" x2="808" y2="48" stroke="#1e293b" stroke-width="1" />

  <!-- Left Stats Summary -->
  <g transform="translate(32, 72)">
    <text x="0" y="16" fill="#c084fc" font-family="sans-serif" font-size="14">🐙</text>
    <text x="24" y="16" fill="#f1f5f9" font-family="sans-serif" font-size="13" font-weight="600">{all_time_contribs:,} Total Contributions ({year_contribs:,} 1Y)</text>

    <text x="0" y="44" fill="#38bdf8" font-family="sans-serif" font-size="14">📦</text>
    <text x="24" y="44" fill="#f1f5f9" font-family="sans-serif" font-size="13" font-weight="600">{public_non_fork} Original Public Repositories</text>

    <text x="0" y="72" fill="#10b981" font-family="sans-serif" font-size="14">⚙️</text>
    <text x="24" y="72" fill="#f1f5f9" font-family="sans-serif" font-size="13" font-weight="600">Distributed Systems &amp; AI Architecture</text>

    <text x="0" y="100" fill="#f59e0b" font-family="sans-serif" font-size="14">🏆</text>
    <text x="24" y="100" fill="#f8fafc" font-family="sans-serif" font-size="13" font-weight="600">{html.escape(achievements_text)}</text>
  </g>

  <!-- Right Live Contribution Curve -->
  <g transform="translate(0, 0)">
    <line x1="395" y1="66" x2="775" y2="66" stroke="#1e293b" stroke-dasharray="3 3" stroke-width="1" />
    <line x1="395" y1="113" x2="775" y2="113" stroke="#1e293b" stroke-dasharray="3 3" stroke-width="1" />
    <line x1="395" y1="160" x2="775" y2="160" stroke="#1e293b" stroke-width="1" />

    <path d="{fill_d}" fill="url(#waveFill)" />
    <path d="{stroke_d}" fill="none" stroke="url(#waveStroke)" stroke-width="2.5" stroke-linecap="round" />

    <circle cx="{peak_x:.1f}" cy="{peak_y:.1f}" r="4.5" fill="#c084fc" />
    <circle cx="{peak_x:.1f}" cy="{peak_y:.1f}" r="9" fill="#c084fc" fill-opacity="0.28" />

{month_labels_block}

    <text x="782" y="70" fill="#64748b" font-family="sans-serif" font-size="10" text-anchor="start">{peak_val:,}</text>
    <text x="782" y="164" fill="#64748b" font-family="sans-serif" font-size="10" text-anchor="start">0</text>
  </g>
</svg>
"""


def main() -> None:
    token = get_token()
    sync_date = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    data = fetch_profile_data(GITHUB_USERNAME, token)
    achievements = fetch_achievements(GITHUB_USERNAME)

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    (ASSETS_DIR / "stats-and-languages.svg").write_text(
        build_stats_and_languages_svg(data, sync_date), encoding="utf-8"
    )
    (ASSETS_DIR / "contributions-graph.svg").write_text(
        build_contributions_graph_svg(data, achievements, sync_date), encoding="utf-8"
    )
    print(f"Updated SVGs cleanly for {GITHUB_USERNAME} ({sync_date}).")
    print(f"  Stars: {sum(r['stargazerCount'] for r in data['repos'])}")
    print(f"  Commits: {data['all_time_commits']}")
    print(f"  Merged PRs: {data['user']['pullRequestsMerged']['totalCount']}")
    print(f"  Contributed repos: {data['user']['repositoriesContributedTo']['totalCount']}")
    print(f"  All-time contributions: {data['all_time_contributions']}")
    print(f"  Achievements: {achievements}")


if __name__ == "__main__":
    main()
