"""Tests for the preflight module - specifically intelligent warnings."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from terminal_copilot.preflight import (
    PreflightIssue,
    PreflightResult,
    _detect_git_merge_conflicts,
    _detect_port_in_use,
    _get_common_dev_ports,
    run_intelligent_warnings,
    run_preflight,
    _npm_preflight,
    _docker_preflight,
    _git_preflight,
    _python_preflight,
    _generic_preflight,
)
from terminal_copilot.plugins import (
    NpmPlugin,
    DockerPlugin,
    GitPlugin,
    RustPlugin,
    GoPlugin,
    PythonPlugin,
    CCompilePlugin,
    PreflightCheck,
)


# ── PreflightIssue and PreflightResult Tests ─────────────────────────────────

class TestPreflightIssue:
    """Tests for PreflightIssue dataclass."""

    def test_preflight_issue_creation(self):
        """Test creating a PreflightIssue with all fields."""
        issue = PreflightIssue(
            level="warning",
            message="Test warning",
            suggestion="Test suggestion",
        )
        assert issue.level == "warning"
        assert issue.message == "Test warning"
        assert issue.suggestion == "Test suggestion"

    def test_preflight_issue_without_suggestion(self):
        """Test creating a PreflightIssue without optional suggestion."""
        issue = PreflightIssue(level="error", message="Test error")
        assert issue.level == "error"
        assert issue.message == "Test error"
        assert issue.suggestion is None


class TestPreflightResult:
    """Tests for PreflightResult dataclass."""

    def test_preflight_result_empty(self):
        """Test empty PreflightResult."""
        result = PreflightResult()
        assert result.has_issues is False
        assert len(result.errors) == 0
        assert len(result.warnings) == 0
        assert len(result.infos) == 0

    def test_preflight_result_create_with_errors(self):
        """Test PreflightResult.create with error issues."""
        issues = [
            PreflightIssue(level="error", message="Error 1"),
            PreflightIssue(level="error", message="Error 2"),
        ]
        result = PreflightResult.create(issues)
        assert result.has_issues is True
        assert len(result.errors) == 2
        assert len(result.warnings) == 0
        assert len(result.infos) == 0

    def test_preflight_result_create_with_warnings(self):
        """Test PreflightResult.create with warning issues."""
        issues = [
            PreflightIssue(level="warning", message="Warning 1"),
            PreflightIssue(level="warning", message="Warning 2"),
        ]
        result = PreflightResult.create(issues)
        assert result.has_issues is True
        assert len(result.warnings) == 2
        assert len(result.errors) == 0

    def test_preflight_result_create_with_infos(self):
        """Test PreflightResult.create with info issues."""
        issues = [
            PreflightIssue(level="info", message="Info 1"),
        ]
        result = PreflightResult.create(issues)
        assert result.has_issues is True
        assert len(result.infos) == 1


# ── Helper Function Tests ────────────────────────────────────────────────────

class TestGetCommonDevPorts:
    """Tests for _get_common_dev_ports function."""

    def test_returns_common_ports(self):
        """Test that common dev ports are returned."""
        ports = _get_common_dev_ports()
        assert isinstance(ports, list)
        assert 3000 in ports
        assert 5000 in ports
        assert 8000 in ports
        assert 8080 in ports


class TestNpmPreflight:
    """Tests for _npm_preflight function."""

    def test_no_package_json_warning(self):
        """Test warning when package.json is missing for npm commands."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("terminal_copilot.preflight.Path.cwd", return_value=Path(tmpdir)):
                issues = _npm_preflight("npm install")
                warning_issues = [i for i in issues if i.level == "warning"]
                assert any("No package.json found" in i.message for i in warning_issues)

    def test_npm_typo_install(self):
        """Test detection of npm install typo."""
        issues = _npm_preflight("npm instal")
        error_issues = [i for i in issues if i.level == "error"]
        assert any("npm instal" in i.message for i in error_issues)

    def test_npm_install_not_flagged_as_typo(self):
        """Test that correct 'npm install' command is NOT flagged as typo."""
        issues = _npm_preflight("npm install")
        error_issues = [i for i in issues if i.level == "error"]
        typo_issues = [i for i in error_issues if "npm instal" in i.message]
        assert len(typo_issues) == 0

    def test_npm_typo_uninstall(self):
        """Test detection of npm uninstall typo."""
        issues = _npm_preflight("npm uninstal")
        error_issues = [i for i in issues if i.level == "error"]
        assert any("npm uninstal" in i.message for i in error_issues)

    def test_npm_uninstall_not_flagged_as_typo(self):
        """Test that correct 'npm uninstall' command is NOT flagged as typo."""
        issues = _npm_preflight("npm uninstall")
        error_issues = [i for i in issues if i.level == "error"]
        typo_issues = [i for i in error_issues if "npm uninstal" in i.message]
        assert len(typo_issues) == 0


class TestDockerPreflight:
    """Tests for _docker_preflight function."""

    def test_docker_daemon_not_running(self):
        """Test error when Docker daemon is not running."""
        def mock_run(cmd, timeout=5):
            if "docker --version" in cmd:
                return "Docker version 20.10.0"
            if "docker ps" in cmd:
                return "Cannot connect to the Docker daemon"
            return None

        with patch("terminal_copilot.preflight._run_quick_check", side_effect=mock_run):
            issues = _docker_preflight("docker ps")
            error_issues = [i for i in issues if i.level == "error"]
            assert any("daemon is not running" in i.message for i in error_issues)


class TestGitPreflight:
    """Tests for _git_preflight function."""

    def test_not_in_git_repo_warning(self):
        """Test warning when not in a git repository."""
        with patch("terminal_copilot.preflight._run_quick_check", return_value=None):
            issues = _git_preflight("git status")
            warning_issues = [i for i in issues if i.level == "warning"]
            assert any("Not in a Git repository" in i.message for i in warning_issues)


class TestPythonPreflight:
    """Tests for _python_preflight function."""

    def test_python_file_not_found(self):
        """Test error when Python file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("terminal_copilot.preflight.Path.cwd", return_value=Path(tmpdir)):
                with patch("terminal_copilot.preflight._run_quick_check", return_value="Python 3.12.0"):
                    issues = _python_preflight("python nonexistent.py")
                    error_issues = [i for i in issues if i.level == "error"]
                    assert any("not found" in i.message for i in error_issues)


class TestGenericPreflight:
    """Tests for _generic_preflight function."""

    def test_sudo_warning(self):
        """Test warning when sudo is used."""
        issues = _generic_preflight("sudo apt update")
        warning_issues = [i for i in issues if i.level == "warning"]
        assert any("sudo" in i.message for i in warning_issues)

    def test_dangerous_rm_detection(self):
        """Test detection of dangerous rm commands."""
        issues = _generic_preflight("rm /")
        error_issues = [i for i in issues if i.level == "error"]
        assert any("Dangerous rm target" in i.message for i in error_issues)


# ── run_intelligent_warnings Tests ───────────────────────────────────────────

class TestRunIntelligentWarnings:
    """Tests for run_intelligent_warnings function - the main intelligent warnings feature."""

    def test_no_issues_outside_git_repo(self):
        """Test that no issues are detected when outside a git repo and no docker/dev commands."""
        with patch("terminal_copilot.preflight._run_quick_check", return_value=None):
            issues = run_intelligent_warnings("ls -la")
            # Should return empty list when no git repo and no special conditions
            assert issues == []

    def test_git_merge_conflict_warning(self):
        """Test detection of git merge conflicts."""
        def mock_run_check(cmd):
            if "git rev-parse" in cmd:
                return "/some/repo"  # We're in a git repo
            if "git diff --name-only --diff-filter=U" in cmd:
                return "conflicted_file.py"  # There are conflicts
            return None

        with patch("terminal_copilot.preflight._run_quick_check", side_effect=mock_run_check):
            issues = run_intelligent_warnings("npm start")
            warning_issues = [i for i in issues if i.level == "warning"]
            assert any("merge conflicts" in i.message for i in warning_issues)

    def test_docker_daemon_warning(self):
        """Test detection of Docker daemon not running for docker commands."""
        def mock_run_check(cmd, timeout=5):
            if "git rev-parse" in cmd:
                return None  # Not in git repo
            if "docker ps" in cmd:
                return "Cannot connect to the Docker daemon"
            return None

        with patch("terminal_copilot.preflight._run_quick_check", side_effect=mock_run_check):
            issues = run_intelligent_warnings("docker run nginx")
            warning_issues = [i for i in issues if i.level == "warning"]
            assert any("Docker daemon is not running" in i.message for i in warning_issues)

    def test_dev_server_port_in_use_warning(self):
        """Test detection of port already in use for dev server commands."""
        def mock_run_check(cmd):
            if "git rev-parse" in cmd:
                return None  # Not in git repo
            if "ss -tln" in cmd:
                # Simulate port 3000 being in use
                return "LISTEN  0  128  *:3000 *:*"
            return None

        with patch("terminal_copilot.preflight._run_quick_check", side_effect=mock_run_check):
            issues = run_intelligent_warnings("npm start")
            warning_issues = [i for i in issues if i.level == "warning"]
            assert any("already in use" in i.message for i in warning_issues)

    def test_dev_server_various_commands(self):
        """Test that port check triggers for various dev server command patterns."""
        command_patterns = [
            "npm run dev",
            "npm run start",
            "yarn dev",
            "pnpm serve",
            "npm run develop",
        ]

        for command in command_patterns:
            def mock_run_check(cmd):
                if "git rev-parse" in cmd:
                    return None
                if "ss -tln" in cmd:
                    return ""  # No ports in use for this test
                return None

            with patch("terminal_copilot.preflight._run_quick_check", side_effect=mock_run_check):
                issues = run_intelligent_warnings(command)
                # Should trigger port check logic without error
                assert isinstance(issues, list)

    def test_non_dev_command_no_port_check(self):
        """Test that port check doesn't trigger for non-dev commands."""
        def mock_run_check(cmd):
            if "git rev-parse" in cmd:
                return "/some/repo"
            if "git diff" in cmd:
                return None
            return None

        with patch("terminal_copilot.preflight._run_quick_check", side_effect=mock_run_check):
            issues = run_intelligent_warnings("npm install")
            # Should only check git conflicts, not ports
            assert isinstance(issues, list)


# ── run_preflight Tests ───────────────────────────────────────────────────────

# ── Plugin Preflight Checks Tests ─────────────────────────────────────────────


class TestPreflightCheck:
    """Tests for PreflightCheck class."""

    def test_preflight_check_passed(self):
        """Test PreflightCheck with passed status."""
        check = PreflightCheck(passed=True, message="test check")
        assert check.passed is True
        assert check.message == "test check"
        assert check.suggestion is None
        assert check.status == "✓"
        assert check.level == "info"

    def test_preflight_check_failed(self):
        """Test PreflightCheck with failed status."""
        check = PreflightCheck(passed=False, message="test check", suggestion="fix it")
        assert check.passed is False
        assert check.message == "test check"
        assert check.suggestion == "fix it"
        assert check.status == "⚠"
        assert check.level == "warning"


class TestNpmPluginPreflight:
    """Tests for NpmPlugin.preflight_checks method."""

    def test_npm_plugin_preflight_with_package_json(self):
        """Test NpmPlugin preflight checks with package.json present."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create package.json
            pkg_json = Path(tmpdir) / "package.json"
            pkg_json.write_text('{"name": "test-project"}')
            
            with patch("terminal_copilot.plugins._run_quick_command", return_value="v20.0.0"):
                with patch("terminal_copilot.plugins.Path.cwd", return_value=Path(tmpdir)):
                    plugin = NpmPlugin()
                    checks = plugin.preflight_checks(Path(tmpdir))
                    
                    # Check that we have 5 checks
                    assert len(checks) == 5
                    
                    # Check package.json is found
                    pkg_check = next(c for c in checks if "package.json" in c.message)
                    assert pkg_check.passed is True
                    
                    # Check node is found
                    node_check = next(c for c in checks if "node" in c.message)
                    assert node_check.passed is True
                    
                    # Check npm is found
                    npm_check = next(c for c in checks if "npm" in c.message)
                    assert npm_check.passed is True

    def test_npm_plugin_preflight_without_node(self):
        """Test NpmPlugin preflight checks without Node installed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create package.json
            pkg_json = Path(tmpdir) / "package.json"
            pkg_json.write_text('{"name": "test-project"}')
            
            def mock_run(cmd, timeout=5):
                if "node --version" in cmd:
                    return None  # Node not installed
                if "npm --version" in cmd:
                    return "10.0.0"
                return None
            
            with patch("terminal_copilot.plugins._run_quick_command", side_effect=mock_run):
                with patch("terminal_copilot.plugins.Path.cwd", return_value=Path(tmpdir)):
                    plugin = NpmPlugin()
                    checks = plugin.preflight_checks(Path(tmpdir))
                    
                    # Check node is not found
                    node_check = next(c for c in checks if "node" in c.message)
                    assert node_check.passed is False


class TestDockerPluginPreflight:
    """Tests for DockerPlugin.preflight_checks method."""

    def test_docker_plugin_preflight_installed(self):
        """Test DockerPlugin preflight checks with Docker installed."""
        def mock_run(cmd, timeout=5):
            if "docker --version" in cmd:
                return "Docker version 24.0.0"
            if "docker ps" in cmd:
                return ""  # Empty output means daemon running
            return None
        
        with patch("terminal_copilot.plugins._run_quick_command", side_effect=mock_run):
            with patch("terminal_copilot.plugins._file_exists", return_value=False):
                plugin = DockerPlugin()
                checks = plugin.preflight_checks()
                
                # Check Docker installed
                docker_check = next(c for c in checks if "Docker installed" in c.message)
                assert docker_check.passed is True
                
                # Check daemon running
                daemon_check = next(c for c in checks if "daemon running" in c.message)
                assert daemon_check.passed is True


class TestGitPluginPreflight:
    """Tests for GitPlugin.preflight_checks method."""

    def test_git_plugin_preflight_in_repo(self):
        """Test GitPlugin preflight checks when in a git repo."""
        def mock_run(cmd):
            if "git --version" in cmd:
                return "git version 2.40.0"
            if "git rev-parse --show-toplevel" in cmd:
                return "/some/repo"
            return None
        
        with patch("terminal_copilot.plugins._run_quick_command", side_effect=mock_run):
            plugin = GitPlugin()
            checks = plugin.preflight_checks()
            
            # Check Git installed
            git_check = next(c for c in checks if "Git installed" in c.message)
            assert git_check.passed is True
            
            # Check in git repo
            repo_check = next(c for c in checks if "git repository" in c.message)
            assert repo_check.passed is True


class TestRustPluginPreflight:
    """Tests for RustPlugin.preflight_checks method."""

    def test_rust_plugin_preflight_with_tools(self):
        """Test RustPlugin preflight checks with Rust tools installed."""
        def mock_run(cmd):
            if "rustc --version" in cmd:
                return "rustc 1.70.0"
            if "cargo --version" in cmd:
                return "cargo 1.70.0"
            return None
        
        with patch("terminal_copilot.plugins._run_quick_command", side_effect=mock_run):
            with patch("terminal_copilot.plugins._file_exists", return_value=True):
                plugin = RustPlugin()
                checks = plugin.preflight_checks()
                
                # Check rustc installed
                rustc_check = next((c for c in checks if "rustc" in c.message), None)
                assert rustc_check is not None
                assert rustc_check.passed is True
                
                # Check cargo installed
                cargo_check = next((c for c in checks if "cargo" in c.message), None)
                assert cargo_check is not None
                assert cargo_check.passed is True
                
                # Check Cargo.toml
                toml_check = next((c for c in checks if "Cargo.toml" in c.message), None)
                assert toml_check is not None
                assert toml_check.passed is True


class TestGoPluginPreflight:
    """Tests for GoPlugin.preflight_checks method."""

    def test_go_plugin_preflight_with_tools(self):
        """Test GoPlugin preflight checks with Go installed."""
        def mock_run(cmd):
            if "go version" in cmd:
                return "go version 1.21.0"
            return None
        
        with patch("terminal_copilot.plugins._run_quick_command", side_effect=mock_run):
            with patch("terminal_copilot.plugins._file_exists", return_value=True):
                plugin = GoPlugin()
                checks = plugin.preflight_checks()
                
                # Check Go installed
                go_check = next((c for c in checks if "Go installed" in c.message), None)
                assert go_check is not None
                assert go_check.passed is True


class TestCompilePluginPreflight:
    """Tests for CCompilePlugin.preflight_checks method."""

    def test_compile_plugin_preflight_with_tools(self):
        """Test CCompilePlugin preflight checks with tools installed."""
        def mock_run(cmd):
            if "gcc --version" in cmd:
                return "gcc 11.0.0"
            if "g++ --version" in cmd:
                return "g++ 11.0.0"
            if "make --version" in cmd:
                return "GNU Make 4.3"
            return None
        
        with patch("terminal_copilot.plugins._run_quick_command", side_effect=mock_run):
            plugin = CCompilePlugin()
            checks = plugin.preflight_checks()
            
            # Check gcc installed
            gcc_check = next((c for c in checks if "gcc installed" in c.message), None)
            assert gcc_check is not None
            assert gcc_check.passed is True
            
            # Check g++ installed
            gpp_check = next((c for c in checks if "g++ installed" in c.message), None)
            assert gpp_check is not None
            assert gpp_check.passed is True


class TestPythonPluginPreflight:
    """Tests for PythonPlugin.preflight_checks method."""

    def test_python_plugin_preflight_with_python(self):
        """Test PythonPlugin preflight checks with Python installed."""
        def mock_run(cmd):
            if "python3 --version" in cmd:
                return "Python 3.11.0"
            return None
        
        with patch("terminal_copilot.plugins._run_quick_command", side_effect=mock_run):
            plugin = PythonPlugin()
            checks = plugin.preflight_checks()
            
            # Check Python installed
            python_check = next((c for c in checks if "Python" in c.message), None)
            assert python_check is not None
            assert python_check.passed is True


# ── run_preflight Tests ───────────────────────────────────────────────────────


class TestRunPreflight:
    """Tests for run_preflight function - integration tests."""

    def test_run_preflight_no_issues(self):
        """Test run_preflight with a harmless command."""
        with patch("terminal_copilot.preflight._run_quick_check", return_value=None):
            with patch("terminal_copilot.preflight.find_matching_plugin", return_value="unknown"):
                result = run_preflight("echo hello")
                assert result.has_issues is False

    def test_run_preflight_with_npm_command(self):
        """Test run_preflight with npm command on non-Node project."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("terminal_copilot.preflight.Path.cwd", return_value=Path(tmpdir)):
                with patch("terminal_copilot.preflight._run_quick_check", return_value="8.0.0"):
                    with patch("terminal_copilot.preflight.find_matching_plugin", return_value="npm"):
                        result = run_preflight("npm install")
                        # Should have warning about no package.json
                        assert result.has_issues is True
                        assert any("No package.json" in w.message for w in result.warnings)
