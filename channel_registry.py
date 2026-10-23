from channels.naver_blog import NaverBlogChannel
from channels.naver_cafe import NaverCafeChannel
from channels.naver_kin import NaverKinChannel
from channels.youtube_comment import YouTubeCommentChannel
from channels.reddit import RedditChannel
from channels.stubs import CommunityStubChannel, GoogleStubChannel

def get_all_channels():
    return [
        NaverBlogChannel(),
        NaverCafeChannel(),
        NaverKinChannel(),
        YouTubeCommentChannel(),
        RedditChannel(),
        CommunityStubChannel(),
        GoogleStubChannel(),
    ]
