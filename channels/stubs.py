from typing import List, Dict, Any, Tuple
from channels.base import BaseChannel

class CommunityStubChannel(BaseChannel):
    name = "공개 커뮤니티 (네이트판 등)"
    channel_id = "community_stub"

    def check_availability(self, secrets: Dict[str, Any]) -> Tuple[bool, str]:
        return False, "준비 중"

    def search(self, query: str, max_count: int, sort: str, secrets: Dict[str, Any], status_callback=None) -> List[Dict[str, Any]]:
        return []

class RedditStubChannel(BaseChannel):
    name = "해외 포럼 (Reddit 등)"
    channel_id = "reddit_stub"

    def check_availability(self, secrets: Dict[str, Any]) -> Tuple[bool, str]:
        return False, "준비 중"

    def search(self, query: str, max_count: int, sort: str, secrets: Dict[str, Any], status_callback=None) -> List[Dict[str, Any]]:
        return []

class GoogleStubChannel(BaseChannel):
    name = "구글 검색 (유료 API)"
    channel_id = "google_stub"

    def check_availability(self, secrets: Dict[str, Any]) -> Tuple[bool, str]:
        return False, "준비 중"

    def search(self, query: str, max_count: int, sort: str, secrets: Dict[str, Any], status_callback=None) -> List[Dict[str, Any]]:
        return []
