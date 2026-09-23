#!/usr/bin/env python3
"""Screen A-shares for the trend path: above MA5 and a strong trend.

K-lines come from Tencent (forward-adjusted). The optional gainers pool comes
from Sina. Prints JSON to stdout. Logic and earnings are not computed here.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request

KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
QUOTE_URL = "https://qt.gtimg.cn/q="
SUGGEST_URL = "https://suggest3.sinajs.cn/suggest/type=11,12,13,14,15&key="
POOL_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "Market_Center.getHQNodeData"
)
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


class DataError(Exception):
    pass


def fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": UA,
            "Referer": "https://finance.sina.com.cn/",
        },
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.read()
        except Exception as exc:  # noqa: BLE001 - surface network failures to the agent
            last_error = exc
            time.sleep(0.4 * (attempt + 1))
    raise DataError(f"请求失败: {last_error}")


def fetch_text(url: str, encoding: str = "utf-8") -> str:
    return fetch_bytes(url).decode(encoding, "replace")


def normalize_code(raw: str) -> str:
    text = raw.strip().upper().replace(" ", "")
    for suffix in (".SH", ".SZ", ".BJ"):
        text = text.removesuffix(suffix)
    if text.startswith(("SH", "SZ", "BJ")) and text[2:].isdigit():
        text = text[2:]
    if not text.isdigit() or len(text) != 6:
        raise DataError(f"无法识别的股票代码: {raw}")
    return text


def tencent_symbol(code: str) -> str:
    if code.startswith(("43", "82", "83", "87", "92")):
        prefix = "bj"
    elif code.startswith(("5", "6", "9")):
        prefix = "sh"
    else:
        prefix = "sz"
    return f"{prefix}{code}"


def lookup_name(name: str) -> str:
    text = fetch_text(SUGGEST_URL + urllib.parse.quote(name), encoding="gbk")
    marker = 'suggestvalue="'
    if marker not in text:
        raise DataError(f"没有找到股票: {name}")
    payload = text.split(marker, 1)[1].split('"', 1)[0]
    for part in payload.split(";"):
        fields = part.split(",")
        if len(fields) >= 3 and fields[2].isdigit() and len(fields[2]) == 6:
            return fields[2]
    raise DataError(f"没有找到股票: {name}")


def fetch_security_name(symbol: str) -> str:
    text = fetch_text(QUOTE_URL + symbol, encoding="gbk")
    fields = text.split("~")
    if len(fields) < 2:
        return ""
    return fields[1]


def fetch_bars(code: str, limit: int) -> tuple[str, list[dict]]:
    symbol = tencent_symbol(code)
    query = urllib.parse.urlencode({"param": f"{symbol},day,,,{limit},qfq"})
    payload = json.loads(fetch_text(f"{KLINE_URL}?{query}"))
    node = ((payload.get("data") or {}).get(symbol)) or {}
    raw_rows = node.get("qfqday") or node.get("day") or []
    rows = []
    for item in raw_rows:
        if len(item) < 6:
            continue
        rows.append(
            {
                "date": item[0],
                "close": float(item[2]),
                "volume": float(item[5]),
            }
        )
    if len(rows) < 25:
        raise DataError(f"{code} K 线不足，无法计算 MA20")
    return fetch_security_name(symbol), rows


def sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def evaluate(code: str, limit: int) -> dict:
    name, bars = fetch_bars(code, limit)
    closes = [bar["close"] for bar in bars]
    ma5_now = sma(closes, 5)
    ma10_now = sma(closes, 10)
    ma20_now = sma(closes, 20)
    ma5_prev = sma(closes[:-5], 5)
    ma20_prev = sma(closes[:-5], 20)
    days_above = 0
    for offset in range(1, 6):
        history = closes[: len(closes) - 5 + offset]
        ma5_then = sma(history, 5)
        if ma5_then is not None and history[-1] > ma5_then:
            days_above += 1

    up_volume = 0.0
    down_volume = 0.0
    for index, bar in enumerate(bars[-10:]):
        absolute = len(bars) - 10 + index
        if absolute == 0:
            continue
        previous = bars[absolute - 1]["close"]
        if bar["close"] > previous:
            up_volume += bar["volume"]
        elif bar["close"] < previous:
            down_volume += bar["volume"]

    close = closes[-1]
    above_ma5 = ma5_now is not None and close > ma5_now
    ma5_up = ma5_now is not None and ma5_prev is not None and ma5_now > ma5_prev
    bull_stack = (
        ma5_now is not None
        and ma10_now is not None
        and ma20_now is not None
        and ma5_now > ma10_now > ma20_now
        and close > ma20_now
    )
    ma20_up = ma20_now is not None and ma20_prev is not None and ma20_now > ma20_prev
    volume_confirm = up_volume >= down_volume and up_volume > 0
    bias = ((close - ma5_now) / ma5_now * 100) if ma5_now else None
    st = "ST" in name.upper()
    flags = []
    if st:
        flags.append("ST，趋势路径排除")
    if bias is not None and bias > 8:
        flags.append("乖离偏大，不作为首选")
    path_a = (
        not st
        and above_ma5
        and ma5_up
        and days_above >= 3
        and bull_stack
        and ma20_up
        and volume_confirm
    )
    return {
        "code": code,
        "name": name,
        "date": bars[-1]["date"],
        "close": round(close, 3),
        "ma5": round(ma5_now, 3) if ma5_now is not None else None,
        "ma10": round(ma10_now, 3) if ma10_now is not None else None,
        "ma20": round(ma20_now, 3) if ma20_now is not None else None,
        "above_ma5": above_ma5,
        "ma5_up": ma5_up,
        "days_above_ma5": days_above,
        "bull_stack": bull_stack,
        "ma20_up": ma20_up,
        "volume_confirm": volume_confirm,
        "bias_ma5_pct": round(bias, 2) if bias is not None else None,
        "path_a": path_a,
        "flags": flags,
    }


def strong_pool(size: int) -> list[str]:
    query = urllib.parse.urlencode(
        {
            "page": "1",
            "num": str(size),
            "sort": "changepercent",
            "asc": "0",
            "node": "hs_a",
            "symbol": "",
            "_s_r_a": "page",
        }
    )
    text = fetch_text(f"{POOL_URL}?{query}")
    try:
        rows = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DataError("涨幅榜返回的不是 JSON") from exc
    codes = []
    for row in rows or []:
        code = str(row.get("code") or "")
        name = str(row.get("name") or "")
        if code.isdigit() and len(code) == 6 and "ST" not in name.upper():
            codes.append(code)
    if not codes:
        raise DataError("涨幅榜为空")
    return codes


def main() -> int:
    parser = argparse.ArgumentParser(description="筛选五日线之上且强趋势的 A 股")
    parser.add_argument("codes", nargs="*", help="6 位代码，可带 SH/SZ 前缀")
    parser.add_argument("--name", action="append", default=[], help="按名称查找后再筛")
    parser.add_argument("--pool", type=int, default=0, help="从涨幅榜取前 N 只再筛，0 表示不拉榜")
    parser.add_argument("--bars", type=int, default=80, help="向前取的日 K 根数")
    args = parser.parse_args()

    if args.pool < 0 or args.pool > 80:
        print("pool 范围为 0 到 80", file=sys.stderr)
        return 2
    if not args.codes and not args.name and args.pool == 0:
        parser.print_help(sys.stderr)
        return 2

    codes: list[str] = []
    errors: list[dict] = []
    for name in args.name:
        try:
            codes.append(lookup_name(name))
        except DataError as exc:
            errors.append({"name": name, "error": str(exc)})
    for raw in args.codes:
        try:
            codes.append(normalize_code(raw))
        except DataError as exc:
            errors.append({"code": raw, "error": str(exc)})
    pooled: set[str] = set()
    if args.pool:
        try:
            for code in strong_pool(args.pool):
                pooled.add(code)
                codes.append(code)
        except DataError as exc:
            errors.append({"pool": args.pool, "error": str(exc)})

    seen: set[str] = set()
    ordered: list[str] = []
    for code in codes:
        if code not in seen:
            seen.add(code)
            ordered.append(code)

    results = []
    for code in ordered:
        try:
            results.append(evaluate(code, args.bars))
        except DataError as exc:
            if code in pooled and "K 线不足" in str(exc):
                continue
            errors.append({"code": code, "error": str(exc)})

    results.sort(
        key=lambda item: (
            not item["path_a"],
            bool(item["flags"]),
            item["bias_ma5_pct"] if item["bias_ma5_pct"] is not None else 999,
        )
    )
    json.dump({"results": results, "errors": errors}, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0 if results else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DataError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
