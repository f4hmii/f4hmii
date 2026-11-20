#!/usr/bin/env python3
"""Refresh data/contributions.json + data/repos.json for the profile cards.

Runs daily from .github/workflows/profile-cards.yml. Uses the GraphQL API when a
token is present (contributionsCollection), otherwise scrapes the public
contribution calendar HTML so the script also works locally without secrets.
"""
import datetime
import json
import os
import pathlib
import re
import urllib.request

USER = "f4hmii"
FEATURED = ["backend_pkm_tim8", "todo_closing", "TA_gestureEats", "PBO"]
ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "contributions.json"
REPOS_OUT = ROOT / "data" / "repos.json"

QUERY = """query($login:String!){
  user(login:$login){
    contributionsCollection{
      contributionCalendar{
        totalContributions
        weeks{ contributionDays{ date contributionCount contributionLevel } }
      }
    }
  }
}"""

LEVELS = {
    "NONE": 0,
    "FIRST_QUARTILE": 1,
    "SECOND_QUARTILE": 2,
    "THIRD_QUARTILE": 3,
    "FOURTH_QUARTILE": 4,
}


def graphql(user, token):
    body = json.dumps({"query": QUERY, "variables": {"login": user}}).encode()
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=body,
        headers={
            "Authorization": "bearer " + token,
            "Content-Type": "application/json",
            "User-Agent": "profile-cards",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    cal = payload["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    days = []
    for week in cal["weeks"]:
        for d in week["contributionDays"]:
            days.append(
                {
                    "date": d["date"],
                    "count": d["contributionCount"],
                    "level": LEVELS.get(d["contributionLevel"], 0),
                }
            )
    return days, cal["totalContributions"]


def scrape(user):
    req = urllib.request.Request(
        "https://github.com/users/%s/contributions" % user,
        headers={"User-Agent": "profile-cards"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode("utf-8", "replace")

    days = {}
    by_id = {}
    for cell in re.finditer(r"<td\b[^>]*class=\"ContributionCalendar-day\"[^>]*>", html):
        tag = cell.group(0)
        date = re.search(r'data-date="(\d{4}-\d{2}-\d{2})"', tag)
        level = re.search(r'data-level="(\d)"', tag)
        cid = re.search(r'id="(contribution-day-component-[\d-]+)"', tag)
        if date and cid:
            by_id[cid.group(1)] = date.group(1)
            days[date.group(1)] = {
                "date": date.group(1),
                "count": 0,
                "level": int(level.group(1)) if level else 0,
            }

    # Cells are emitted in day-of-week order, so map each tooltip to its date by id.
    tooltip_total = 0
    for tip in re.finditer(
        r'<tool-tip[^>]*for="(contribution-day-component-[\d-]+)"[^>]*>([^<]*)</tool-tip>', html
    ):
        date = by_id.get(tip.group(1))
        m = re.match(r"(\d+|No) contribution", tip.group(2).strip())
        count = 0 if not m or m.group(1) == "No" else int(m.group(1))
        tooltip_total += count
        if date:
            days[date]["count"] = count

    ordered = sorted(days.values(), key=lambda d: d["date"])
    assert sum(d["count"] for d in ordered) == tooltip_total, "day/tooltip total mismatch"
    header = re.search(r">\s*(\d+)\s*contributions\s*in the last year", html)
    if header and int(header.group(1)) != tooltip_total:
        print("warning: calendar total %d != page header %s" % (tooltip_total, header.group(1)), flush=True)
    return ordered, tooltip_total


def repos(user, names, token):
    headers = {"User-Agent": "profile-cards", "Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = "bearer " + token
    out = []
    for name in names:
        try:
            req = urllib.request.Request(
                "https://api.github.com/repos/%s/%s" % (user, name), headers=headers
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.load(r)
            out.append(
                {
                    "name": d["name"],
                    "description": d.get("description") or "",
                    "language": d.get("language") or "",
                    "stars": d["stargazers_count"],
                    "forks": d["forks_count"],
                    "pushed_at": d["pushed_at"],
                    "url": d["html_url"],
                }
            )
        except Exception as exc:  # noqa: BLE001 - a renamed repo must not break the run
            print("skipping repo %s: %s" % (name, exc), flush=True)
    return out


def main():
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    source = "graphql"
    try:
        if not token:
            raise RuntimeError("no token")
        days, total = graphql(USER, token)
    except Exception as exc:  # noqa: BLE001 - fall back to the public calendar
        print("graphql unavailable (%s); scraping public calendar" % exc, flush=True)
        source = "html"
        days, total = scrape(USER)

    assert days, "no contribution days parsed"
    assert sum(d["count"] for d in days) == total, "count mismatch"
    data = {
        "user": USER,
        "source": source,
        "generated_at": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "total": total,
        "days": days,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=1) + "\n")
    print("wrote %s: %d days, %d contributions (%s)" % (OUT, len(days), total, source))

    featured = repos(USER, FEATURED, token)
    assert featured, "no featured repos fetched"
    REPOS_OUT.write_text(json.dumps({"repos": featured}, indent=1) + "\n")
    print("wrote %s: %s" % (REPOS_OUT, ", ".join(r["name"] for r in featured)))


if __name__ == "__main__":
    main()
