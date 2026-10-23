import requests
import time
import re
import html
from datetime import datetime
from typing import List, Dict, Any, Tuple
from channels.base import BaseChannel

class NaverCafeChannel(BaseChannel):
    name = "네이버 카페 (공개글)"
    channel_id = "naver_cafe"

    def check_availability(self, secrets: Dict[str, Any]) -> Tuple[bool, str]:
        cid = secrets.get("NAVER_CLIENT_ID", "")
        csec = secrets.get("NAVER_CLIENT_SECRET", "")
        if cid and csec and "여기에" not in cid:
            return True, "사용 가능"
        return False, "키 필요"

    def search(self, query: str, max_count: int, sort: str, secrets: Dict[str, Any], status_callback=None) -> List[Dict[str, Any]]:
        headers = {
            "X-NCP-APIGW-API-KEY-ID": secrets.get("NAVER_CLIENT_ID", ""),
            "X-NCP-APIGW-API-KEY": secrets.get("NAVER_CLIENT_SECRET", "")
        }
        url = "https://naverapihub.apigw.ntruss.com/search/v1/cafearticle"
        sort_param = "sim" if sort == "정확도순" else "date"
        results = []
        start = 1
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        while start <= min(max_count, 1000):
            display = min(100, max_count - len(results))
            if display <= 0:
                break
            params = {"query": query, "display": display, "start": start, "sort": sort_param}
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=10)
                if resp.status_code == 429:
                    if status_callback: status_callback("⚠️ 오늘 네이버 API 호출 한도(25,000회)를 초과했습니다.")
                    break
                elif resp.status_code != 200:
                    if status_callback: status_callback(f"네이버 카페 오류: HTTP {resp.status_code}")
                    break

                data = resp.json()
                items = data.get("items", [])
                if not items:
                    break

                for item in items:
                    title = html.unescape(re.sub(r'<[^>]+>', '', item.get("title", ""))).strip()
                    snippet = html.unescape(re.sub(r'<[^>]+>', '', item.get("description", ""))).strip()

                    results.append({
                        "채널": self.name,
                        "검색식": query,
                        "제목": title,
                        "본문요약": snippet,
                        "본문전체": "",
                        "작성자": item.get("cafename", ""),
                        "작성일": "",
                        "URL": item.get("link", ""),
                        "수집일시": now_str
                    })

                start += len(items)
                time.sleep(0.1)
            except Exception as e:
                if status_callback: status_callback(f"카페 수집 중 오류: {e}")
                break

        return results
