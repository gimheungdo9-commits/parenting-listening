"""
본문 가져오기 테스트 도구

사용법 (터미널에서):
    python test_fulltext.py "글 주소"

예:
    python test_fulltext.py "https://blog.naver.com/woojoo_love_love/224412083100"
    python test_fulltext.py "https://cafe.naver.com/imsanbu/79836474"

주소를 안 넣으면 기본 예시 3개(블로그·카페·지식iN 자리)를 차례로 확인합니다.
"""

import sys

import requests

from fulltext import fetch_one


def guess_channel(url: str) -> str:
    if "blog.naver.com" in url:
        return "네이버 블로그"
    if "cafe.naver.com" in url:
        return "네이버 카페 (공개글)"
    if "kin.naver.com" in url:
        return "네이버 지식iN"
    return "알 수 없음"


def check(url: str) -> None:
    channel = guess_channel(url)
    print("=" * 60)
    print("채널:", channel)
    print("주소:", url)
    res = fetch_one(requests.Session(), {"채널": channel, "URL": url})
    print("본문상태:", res.get("status"), "|", res.get("note", ""))
    print("작성일:", res.get("date"))
    body = res.get("body", "")
    print("본문 글자 수:", len(body))
    print("-" * 60)
    print(body[:600] + ("\n...(이하 생략)" if len(body) > 600 else ""))


if __name__ == "__main__":
    urls = sys.argv[1:] or [
        "https://blog.naver.com/woojoo_love_love/224412083100",
        "https://cafe.naver.com/imsanbu/79836474",
    ]
    for u in urls:
        check(u)
