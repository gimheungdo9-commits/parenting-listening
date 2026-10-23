import requests
import time
from datetime import datetime
from typing import List, Dict, Any, Tuple
from channels.base import BaseChannel

class RedditChannel(BaseChannel):
    name = "해외 포럼 (Reddit)"
    channel_id = "reddit"

    def check_availability(self, secrets: Dict[str, Any]) -> Tuple[bool, str]:
        # Reddit 공개 검색은 별도 API 키 없이 즉시 사용 가능합니다!
        return True, "사용 가능"

    def search(self, query: str, max_count: int, sort: str, secrets: Dict[str, Any], status_callback=None) -> List[Dict[str, Any]]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 ParentingListeningBot/1.0"
        }
        url = "https://www.reddit.com/search.json"
        sort_param = "relevance" if sort == "정확도순" else "new"
        results = []
        after = None
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        while len(results) < max_count:
            limit = min(100, max_count - len(results))
            params = {"q": query, "sort": sort_param, "limit": limit}
            if after:
                params["after"] = after

            try:
                resp = requests.get(url, headers=headers, params=params, timeout=10)
                if resp.status_code == 429:
                    if status_callback: status_callback("⚠️ Reddit 요청 한도 초과 (잠시 후 다시 시도해 주세요)")
                    break
                elif resp.status_code != 200:
                    if status_callback: status_callback(f"Reddit 오류: HTTP {resp.status_code}")
                    break

                data = resp.json().get("data", {})
                children = data.get("children", [])
                if not children:
                    break

                for post in children:
                    p = post.get("data", {})
                    title = p.get("title", "").strip()
                    selftext = p.get("selftext", "").strip()
                    sub = p.get("subreddit", "")
                    author = p.get("author", "")
                    created_utc = p.get("created_utc", 0)
                    permalink = p.get("permalink", "")
                    full_url = f"https://www.reddit.com{permalink}" if permalink else ""

                    pdate = ""
                    if created_utc:
                        pdate = datetime.fromtimestamp(created_utc).strftime("%Y-%m-%d")

                    snippet = selftext[:300] if selftext else title

                    results.append({
                        "채널": self.name,
                        "검색식": query,
                        "제목": f"[r/{sub}] {title}",
                        "본문요약": snippet,
                        "본문전체": selftext,
                        "작성자": f"u/{author}",
                        "작성일": pdate,
                        "URL": full_url,
                        "수집일시": now_str
                    })

                    if len(results) >= max_count:
                        break

                after = data.get("after")
                if not after:
                    break
                time.sleep(0.5) # Reddit 서버 보호 쉬는 시간

            except Exception as e:
                if status_callback: status_callback(f"Reddit 수집 오류: {e}")
                break

        return results
