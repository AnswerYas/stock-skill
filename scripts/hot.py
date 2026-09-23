#!/usr/bin/env python3
"""Aggregate A-share hot lists and clues for why attention is rising.

Platforms the script can read without a login:
- Tonghuashun hot-stock list (rank change, concept tags, popularity tags)
- Eastmoney popularity rank
- Eastmoney news titles
- Weibo related hot-query text

Xueqiu is behind a WAF in this environment, so columns and posts are not fetched
here. Weibo returns related hot queries, not column articles.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

THS_URL = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
EM_RANK_URL = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
EM_NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp"
WEIBO_URL = "https://weibo.com/ajax/side/search"
QUOTE_URL = "https://qt.gtimg.cn/q="
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


class DataError(Exception):
    pass


def fetch_bytes(url: str, data: bytes | None = None, headers: dict | None = None) -> bytes:
    request_headers = {
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
    }
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, data=data, headers=request_headers)
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.read()
        except Exception as exc:  # noqa: BLE001 - surface network failures to the agent
            last_error = exc
            time.sleep(0.4 * (attempt + 1))
    raise DataError(f"请求失败: {last_error}")


def fetch_json(url: str, data: dict | None = None, headers: dict | None = None) -> dict:
    body = None
    if data is not None:
        body = json.dumps(data).encode()
        headers = dict(headers or {})
        headers["Content-Type"] = "application/json"
    payload = fetch_bytes(url, body, headers)
    try:
        return json.loads(payload.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise DataError("接口返回的不是 JSON") from exc


def code6(raw: str) -> str:
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) < 6:
        raise DataError(f"无法识别的代码: {raw}")
    return digits[-6:]


def ths_hot(limit: int) -> list[dict]:
    query = urllib.parse.urlencode(
        {"stock_type": "a", "type": "day", "list_type": "normal"}
    )
    payload = fetch_json(
        f"{THS_URL}?{query}",
        headers={"Referer": "https://eq.10jqka.com.cn/"},
    )
    rows = ((payload.get("data") or {}).get("stock_list")) or []
    items = []
    for row in rows[:limit]:
        tag = row.get("tag") or {}
        concepts = tag.get("concept_tag") or []
        try:
            rank_change = int(row.get("hot_rank_chg") or 0)
        except (TypeError, ValueError):
            rank_change = 0
        change = row.get("rise_and_fall")
        items.append(
            {
                "code": code6(str(row.get("code") or "")),
                "name": row.get("name") or "",
                "ths_rank": row.get("order"),
                "ths_rank_change": rank_change,
                "ths_pct": round(float(change), 2) if change is not None else None,
                "concepts": concepts,
                "popularity_tag": tag.get("popularity_tag") or "",
            }
        )
    if not items:
        raise DataError("同花顺热股为空")
    return items


def eastmoney_hot(limit: int) -> list[dict]:
    payload = fetch_json(
        EM_RANK_URL,
        data={"appId": "appId_PC", "globalId": "web", "pageNo": 1, "pageSize": limit},
        headers={"Referer": "https://guba.eastmoney.com/rank/"},
    )
    rows = payload.get("data") or []
    items = []
    for row in rows[:limit]:
        items.append(
            {
                "code": code6(str(row.get("sc") or "")),
                "em_rank": row.get("rk"),
                "em_rc": row.get("rc"),
                "em_his_rc": row.get("hisRc"),
            }
        )
    if not items:
        raise DataError("东方财富人气榜为空")
    return items


def tencent_symbol(code: str) -> str:
    if code.startswith(("43", "82", "83", "87", "92")):
        prefix = "bj"
    elif code.startswith(("5", "6", "9")):
        prefix = "sh"
    else:
        prefix = "sz"
    return f"{prefix}{code}"


def fill_names(rows: list[dict]) -> None:
    for row in rows:
        if row.get("name") or not row.get("code"):
            continue
        try:
            text = fetch_bytes(QUOTE_URL + tencent_symbol(row["code"])).decode("gbk", "replace")
        except DataError:
            continue
        fields = text.split("~")
        if len(fields) > 1:
            row["name"] = fields[1]


def eastmoney_news(name: str) -> list[dict]:
    param = {
        "uid": "",
        "keyword": name,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": 2,
                "preTag": "",
                "postTag": "",
            }
        },
    }
    query = urllib.parse.urlencode({"cb": "", "param": json.dumps(param, ensure_ascii=False)})
    payload = fetch_json(f"{EM_NEWS_URL}?{query}", headers={"Referer": "https://so.eastmoney.com/"})
    rows = ((payload.get("result") or {}).get("cmsArticleWebOld")) or []
    news = []
    for row in rows[:2]:
        title = (row.get("title") or "").strip()
        if not title:
            continue
        news.append(
            {
                "title": title,
                "date": row.get("date") or "",
                "source": "东方财富",
            }
        )
    return news


def weibo_queries(name: str) -> list[str]:
    query = urllib.parse.urlencode({"q": name})
    payload = fetch_json(
        f"{WEIBO_URL}?{query}",
        headers={"Referer": "https://weibo.com/"},
    )
    rows = ((payload.get("data") or {}).get("hotquery")) or []
    queries = []
    for row in rows:
        text = (row.get("suggestion") or "").strip()
        if text and name in text:
            queries.append(text)
        if len(queries) >= 3:
            break
    return queries


def merge(ths_rows: list[dict], em_rows: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for row in ths_rows:
        merged[row["code"]] = {
            "code": row["code"],
            "name": row["name"],
            "platforms": ["同花顺"],
            "ths_rank": row["ths_rank"],
            "ths_rank_change": row["ths_rank_change"],
            "ths_pct": row["ths_pct"],
            "concepts": row["concepts"],
            "popularity_tag": row["popularity_tag"],
            "em_rank": None,
            "em_rc": None,
            "em_his_rc": None,
        }
    for row in em_rows:
        item = merged.get(row["code"])
        if item is None:
            item = {
                "code": row["code"],
                "name": "",
                "platforms": [],
                "ths_rank": None,
                "ths_rank_change": None,
                "ths_pct": None,
                "concepts": [],
                "popularity_tag": "",
                "em_rank": None,
                "em_rc": None,
                "em_his_rc": None,
            }
            merged[row["code"]] = item
        if "东方财富" not in item["platforms"]:
            item["platforms"].append("东方财富")
        item["em_rank"] = row["em_rank"]
        item["em_rc"] = row["em_rc"]
        item["em_his_rc"] = row["em_his_rc"]
    rows = list(merged.values())
    rows.sort(
        key=lambda item: (
            -len(item["platforms"]),
            item["ths_rank"] if item["ths_rank"] is not None else 999,
            item["em_rank"] if item["em_rank"] is not None else 999,
        )
    )
    return rows


def attach_reasons(rows: list[dict], count: int) -> None:
    for row in rows[:count]:
        name = row["name"]
        reasons: dict[str, object] = {}
        errors = []
        if not name:
            row["heat"] = {"news": [], "weibo_queries": [], "errors": ["缺少名称，未检索观点"]}
            continue
        try:
            reasons["news"] = eastmoney_news(name)
        except DataError as exc:
            reasons["news"] = []
            errors.append(f"东方财富新闻: {exc}")
        try:
            reasons["weibo_queries"] = weibo_queries(name)
        except DataError as exc:
            reasons["weibo_queries"] = []
            errors.append(f"微博: {exc}")
        reasons["errors"] = errors
        row["heat"] = reasons


def main() -> int:
    parser = argparse.ArgumentParser(description="汇总同花顺、东方财富热股，并附新闻与微博热搜线索")
    parser.add_argument("--limit", type=int, default=20, help="每个榜单最多取多少只")
    parser.add_argument("--reasons", type=int, default=8, help="为前 N 只补新闻和微博热搜，0 表示不补")
    parser.add_argument("--name", action="append", default=[], help="只为这些名称补热度线索，可重复")
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 50:
        print("limit 范围为 1 到 50", file=sys.stderr)
        return 2
    if args.reasons < 0 or args.reasons > args.limit:
        print("reasons 范围为 0 到 limit", file=sys.stderr)
        return 2

    errors = []
    ths_rows: list[dict] = []
    em_rows: list[dict] = []
    try:
        ths_rows = ths_hot(args.limit)
    except DataError as exc:
        errors.append({"platform": "同花顺", "error": str(exc)})
    try:
        em_rows = eastmoney_hot(args.limit)
    except DataError as exc:
        errors.append({"platform": "东方财富", "error": str(exc)})

    rows = merge(ths_rows, em_rows)
    fill_names(rows)
    if args.name:
        by_name = {row["name"]: row for row in rows if row["name"]}
        rows = []
        for name in args.name:
            rows.append(
                by_name.get(name)
                or {
                    "code": "",
                    "name": name,
                    "platforms": [],
                    "ths_rank": None,
                    "ths_rank_change": None,
                    "ths_pct": None,
                    "concepts": [],
                    "popularity_tag": "",
                    "em_rank": None,
                    "em_rc": None,
                    "em_his_rc": None,
                }
            )
        attach_reasons(rows, len(rows))
    elif args.reasons:
        attach_reasons(rows, args.reasons)

    json.dump(
        {
            "note": "雪球页面有风控，脚本不返回雪球专栏。微博字段是相关热搜词，不是专栏正文。热度不是入选条件。",
            "hot": rows,
            "errors": errors,
        },
        sys.stdout,
        ensure_ascii=False,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0 if rows else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DataError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
