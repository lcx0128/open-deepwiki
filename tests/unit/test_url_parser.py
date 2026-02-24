import pytest
from app.utils.url_parser import parse_repo_url
from app.models.repository import RepoPlatform


class TestParseRepoUrl:
    """测试仓库 URL 解析"""

    def test_github_url(self):
        result = parse_repo_url("https://github.com/owner/repo")
        assert result is not None
        assert result["platform"] == RepoPlatform.GITHUB
        assert result["name"] == "owner/repo"
        assert result["owner"] == "owner"
        assert result["repo"] == "repo"

    def test_github_url_with_git_suffix(self):
        result = parse_repo_url("https://github.com/owner/repo.git")
        assert result is not None
        assert result["name"] == "owner/repo"

    def test_gitlab_url(self):
        result = parse_repo_url("https://gitlab.com/owner/project")
        assert result is not None
        assert result["platform"] == RepoPlatform.GITLAB

    def test_bitbucket_url(self):
        result = parse_repo_url("https://bitbucket.org/owner/repo")
        assert result is not None
        assert result["platform"] == RepoPlatform.BITBUCKET

    def test_custom_url(self):
        result = parse_repo_url("https://git.mycompany.com/team/project")
        assert result is not None
        assert result["platform"] == RepoPlatform.CUSTOM
        assert result["name"] == "team/project"

    def test_invalid_url_returns_none(self):
        assert parse_repo_url("not-a-url") is None
        assert parse_repo_url("") is None
        assert parse_repo_url(None) is None

    def test_ftp_url_returns_none(self):
        assert parse_repo_url("ftp://github.com/owner/repo") is None
