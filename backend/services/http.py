import json

from curl_cffi import requests as curl_requests


def get_trending() -> list[str]:
    try:
        resp = curl_requests.get("https://query2.finance.yahoo.com/v1/finance/trending/US?count=8", impersonate="chrome")
        if resp.status_code != 200:
            return []
        data = json.loads(resp.text)
        results = data.get('finance', {}).get('result', [])
        if not results:
            return []
        quotes = results[0].get('quotes', [])
        return [q['symbol'] for q in quotes]
    except Exception:
        return []