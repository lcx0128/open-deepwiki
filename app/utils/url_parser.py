import re
from typing import Optional, Dict
from app.models.repository import RepoPlatform


# 支持的平台 URL 模式
_PLATFORM_PATTERNS = [
    (RepoPlatform.GITHUB, re.compile(r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?$")),
    (RepoPlatform.GITLAB, re.compile(r"https?://gitlab\.com/(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?$")),
    (RepoPlatform.BITBUCKET, re.compile(r"https?://bitbucket\.org/(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?$")),
]


def parse_repo_url(url: str) -> Optional[Dict[str, str]]:
    """
    解析 Git 仓库 URL，提取平台、owner、repo 名称。

    支持:
    - https://github.com/owner/repo
    - https://github.com/owner/repo.git
    - https://gitlab.com/owner/repo
    - https://bitbucket.org/owner/repo

    返回:
        {"platform": RepoPlatform, "name": "owner/repo", "owner": "owner", "repo": "repo"}
        或 None（无法解析时）
    """
    if not url or not url.startswith("http"):
        return None

    url = url.strip().rstrip("/")

    for platform, pattern in _PLATFORM_PATTERNS:
        match = pattern.match(url)
        if match:
            owner = match.group("owner")
            repo = match.group("repo")
            return {
                "platform": platform,
                "name": f"{owner}/{repo}",
                "owner": owner,
                "repo": repo,
            }

    # 自定义平台：尝试从 URL 路径提取 owner/repo
    custom_match = re.match(
        r"https?://[^/]+/(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?$",
        url
    )
    if custom_match:
        owner = custom_match.group("owner")
        repo = custom_match.group("repo")
        return {
            "platform": RepoPlatform.CUSTOM,
            "name": f"{owner}/{repo}",
            "owner": owner,
            "repo": repo,
        }

    return None
