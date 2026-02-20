import pytest
from unittest.mock import patch, MagicMock
from app.tasks.incremental_sync import _detect_changed_files_sync


class TestDetectChangedFiles:
    """测试增量变更检测"""

    @patch("app.tasks.incremental_sync.subprocess.run")
    def test_parse_added_modified_deleted(self, mock_run):
        """解析新增、修改、删除文件"""
        mock_run.side_effect = [
            MagicMock(returncode=0),  # git fetch
            MagicMock(
                returncode=0,
                stdout="A\tsrc/new_file.py\nM\tsrc/old_file.py\nD\tsrc/removed.py",
            ),
        ]
        changes = _detect_changed_files_sync("/repo", "main")
        assert ("A", "src/new_file.py") in changes
        assert ("M", "src/old_file.py") in changes
        assert ("D", "src/removed.py") in changes

    @patch("app.tasks.incremental_sync.subprocess.run")
    def test_no_changes(self, mock_run):
        """无变更时返回空列表"""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout=""),
        ]
        changes = _detect_changed_files_sync("/repo", "main")
        assert changes == []

    @patch("app.tasks.incremental_sync.subprocess.run")
    def test_renamed_files(self, mock_run):
        """重命名文件：拆分为删除旧路径 + 新增新路径"""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=0, stdout="R100\told_name.py\tnew_name.py"),
        ]
        changes = _detect_changed_files_sync("/repo", "main")
        # 重命名拆分为 D(旧路径) + A(新路径)
        assert ("D", "old_name.py") in changes
        assert ("A", "new_name.py") in changes
        # 不应出现原始的 R 类型
        assert not any(ct == "R" for ct, _ in changes)

    @patch("app.tasks.incremental_sync.subprocess.run")
    def test_git_diff_failure_raises(self, mock_run):
        """git diff 失败应抛出 RuntimeError"""
        mock_run.side_effect = [
            MagicMock(returncode=0),
            MagicMock(returncode=1, stderr="fatal: not a git repository"),
        ]
        with pytest.raises(RuntimeError, match="git diff 失败"):
            _detect_changed_files_sync("/not-a-repo", "main")
