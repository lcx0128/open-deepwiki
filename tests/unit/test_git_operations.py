import subprocess
import pytest
from unittest.mock import patch, MagicMock
from app.tasks.git_operations import clone_repository, TokenScrubFilter


class TestTokenScrubFilter:
    """测试日志脱敏过滤器"""

    def test_scrub_github_pat(self):
        """GitHub PAT 必须被脱敏"""
        f = TokenScrubFilter()
        record = MagicMock()
        record.msg = "使用 token: ghp_1234567890abcdef1234567890abcdef1234"
        record.args = None
        f.filter(record)
        assert "ghp_" not in record.msg
        assert "[REDACTED]" in record.msg

    def test_scrub_https_token_url(self):
        """HTTPS URL 中的 Token 必须被脱敏"""
        f = TokenScrubFilter()
        record = MagicMock()
        record.msg = "克隆 https://oauth2:ghp_secret@github.com/owner/repo"
        record.args = None
        f.filter(record)
        assert "ghp_secret" not in record.msg
        assert "[REDACTED]" in record.msg

    def test_scrub_gitlab_pat(self):
        """GitLab PAT 必须被脱敏"""
        f = TokenScrubFilter()
        record = MagicMock()
        record.msg = "token: glpat-xxxxxxxxxxxxxxxxxxxx"
        record.args = None
        f.filter(record)
        assert "glpat-" not in record.msg
        assert "[REDACTED]" in record.msg

    def test_normal_log_unchanged(self):
        """不含 Token 的日志不应被修改"""
        f = TokenScrubFilter()
        record = MagicMock()
        record.msg = "正常日志消息"
        record.args = None
        f.filter(record)
        assert record.msg == "正常日志消息"


class TestCloneRepository:
    """测试 Git 克隆逻辑"""

    @patch("app.tasks.git_operations.subprocess.run")
    @patch("app.tasks.git_operations.shutil.rmtree")
    @patch("app.tasks.git_operations.Path")
    def test_public_repo_clone_success(self, mock_path, mock_rmtree, mock_run):
        """公开仓库克隆成功"""
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = False
        mock_path.return_value = mock_path_instance
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        success, _ = clone_repository(
            "https://github.com/owner/repo",
            "/tmp/test_repo",
        )
        assert success is True
        # 验证没有 Token 注入
        call_args = mock_run.call_args[0][0]
        assert "oauth2" not in " ".join(call_args)

    @patch("app.tasks.git_operations.subprocess.run")
    @patch("app.tasks.git_operations.shutil.rmtree")
    @patch("app.tasks.git_operations.Path")
    def test_private_repo_token_injection(self, mock_path, mock_rmtree, mock_run):
        """私有仓库应注入 PAT Token 到 URL"""
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = False
        mock_path.return_value = mock_path_instance
        mock_run.return_value = MagicMock(returncode=0, stderr="")

        success, _ = clone_repository(
            "https://github.com/owner/private-repo",
            "/tmp/test_repo",
            pat_token="ghp_test_token_12345",
        )
        assert success is True
        call_args = mock_run.call_args[0][0]
        clone_url_parts = [a for a in call_args if "github.com" in a]
        assert len(clone_url_parts) > 0
        assert "oauth2:ghp_test_token_12345@" in clone_url_parts[0]

    @patch("app.tasks.git_operations.subprocess.run")
    @patch("app.tasks.git_operations.Path")
    def test_clone_timeout_handling(self, mock_path, mock_run):
        """克隆超时应返回 False"""
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = False
        mock_path.return_value = mock_path_instance
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="git", timeout=600)

        success, _ = clone_repository("https://github.com/owner/repo", "/tmp/test")
        assert success is False

    @patch("app.tasks.git_operations.subprocess.run")
    @patch("app.tasks.git_operations.Path")
    def test_clone_failure_returns_false(self, mock_path, mock_run):
        """克隆失败（非零返回码）应返回 False"""
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = False
        mock_path.return_value = mock_path_instance
        mock_run.return_value = MagicMock(returncode=128, stderr="Repository not found")

        success, _ = clone_repository("https://github.com/owner/repo", "/tmp/test")
        assert success is False
