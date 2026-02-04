"""Data Transfer Objects for ReddiScribe."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PostDTO:
    """Reddit post data transfer object."""

    id: str                          # Reddit post ID (e.g., "8xwlg")
    title: str
    selftext: str = ""               # body (empty for link posts)
    author: str = "[deleted]"
    subreddit: str = ""
    score: int = 0                   # approximate (fuzzed by Reddit)
    num_comments: int = 0
    url: str = ""
    permalink: str = ""
    created_utc: float = 0.0
    is_self: bool = True


@dataclass
class CommentDTO:
    """Reddit comment data transfer object."""

    id: str
    author: str = "[deleted]"
    body: str = ""                   # Raw markdown
    score: int = 0
    created_utc: float = 0.0
    depth: int = 0                   # nesting depth (0 = top-level)
    parent_id: str = ""              # parent ID (t3_* or t1_*)
    children: list['CommentDTO'] = field(default_factory=list)
    more_count: int = 0              # count of collapsed comments when kind:"more"


@dataclass
class SummaryDTO:
    """Summary data transfer object."""

    post_id: str
    model_type: str                  # 'summary', 'logic', 'persona'
    text: str
    locale: str = "ko_KR"


@dataclass
class WriterContext:
    """Context for passing data from Reader to Writer.

    Used when user clicks 'Write Comment' or 'Reply' on a post/comment.
    """
    mode: str  # "new_post" | "comment" | "reply"
    subreddit: str = ""
    post_title: str = ""
    post_permalink: str = ""
    post_selftext: str = ""
    comment_id: str = ""
    comment_body: str = ""
    comment_author: str = ""
    parent_thread: list = field(default_factory=list)  # list[dict] for reply thread
