import time
from datetime import datetime
from typing import List, Dict, Any, Tuple
from channels.base import BaseChannel

class YouTubeCommentChannel(BaseChannel):
    name = "유튜브 댓글"
    channel_id = "youtube_comment"

    def check_availability(self, secrets: Dict[str, Any]) -> Tuple[bool, str]:
        key = secrets.get("YOUTUBE_API_KEY", "")
        if key and len(key.strip()) > 10:
            return True, "사용 가능"
        return False, "키 필요"

    def search(self, query: str, max_count: int, sort: str, secrets: Dict[str, Any], status_callback=None) -> List[Dict[str, Any]]:
        api_key = secrets.get("YOUTUBE_API_KEY", "")
        if not api_key:
            return []

        try:
            from googleapiclient.discovery import build
            youtube = build('youtube', 'v3', developerKey=api_key)
        except Exception as e:
            if status_callback: status_callback(f"유튜브 라이브러리 연결 오류: {e}")
            return []

        results = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        order_param = "relevance" if sort == "정확도순" else "date"

        try:
            # 1. 관련 영상 검색 (최대 5개 영상)
            search_response = youtube.search().list(
                q=query,
                part='id,snippet',
                maxResults=5,
                type='video',
                order=order_param,
                regionCode='KR',          # 한국 영상 우선
                relevanceLanguage='ko'    # 한국어 영상 우선
            ).execute()

            for video_item in search_response.get('items', []):
                if len(results) >= max_count:
                    break
                video_id = video_item['id']['videoId']
                video_title = video_item['snippet']['title']

                # 2. 해당 영상의 댓글 수집
                try:
                    comments_response = youtube.commentThreads().list(
                        part='snippet',
                        videoId=video_id,
                        maxResults=min(100, max_count - len(results)),
                        textFormat='plainText'
                    ).execute()

                    for comment_item in comments_response.get('items', []):
                        top_comment = comment_item['snippet']['topLevelComment']['snippet']
                        author = top_comment.get('authorDisplayName', '')
                        text = top_comment.get('textDisplay', '')
                        published_at = top_comment.get('publishedAt', '')[:10]
                        comment_id = comment_item['id']
                        comment_url = f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}"

                        results.append({
                            "채널": self.name,
                            "검색식": query,
                            "제목": f"[{video_title}] 영상의 댓글",
                            "본문요약": text,
                            "본문전체": "",
                            "작성자": author,
                            "작성일": published_at,
                            "URL": comment_url,
                            "수집일시": now_str
                        })

                        if len(results) >= max_count:
                            break
                    time.sleep(0.1)
                except Exception:
                    continue  # 댓글이 막힌 영상 등은 건너뜁니다
        except Exception as e:
            if status_callback: status_callback(f"유튜브 수집 오류: {e}")

        return results
