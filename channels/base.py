from abc import ABC, abstractmethod
from typing import List, Dict, Any, Tuple

class BaseChannel(ABC):
    name: str = "기본 채널"
    channel_id: str = "base"

    @abstractmethod
    def check_availability(self, secrets: Dict[str, Any]) -> Tuple[bool, str]:
        """(사용가능여부, 표시상태메시지)를 반환합니다."""
        pass

    @abstractmethod
    def search(self, query: str, max_count: int, sort: str, secrets: Dict[str, Any], status_callback=None) -> List[Dict[str, Any]]:
        """검색을 수행하고 공통 데이터 규격의 딕셔너리 리스트를 반환합니다."""
        pass
