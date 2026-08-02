import unittest
import sys
import os

# Add parent directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from command_registry import resolve_command, CommandDef
from bot import build_session_agy_cmd, build_standalone_agy_cmd, AGY_PATH


class TestBuildCmd(unittest.TestCase):
    def test_t1_empty_session(self):
        session = {"has_active_session": False, "flags": {}}
        cmd = build_session_agy_cmd(session, "hello")
        self.assertEqual(cmd, [AGY_PATH, "-p", "hello"])

    def test_t2_active_session(self):
        session = {"has_active_session": True, "flags": {}}
        cmd = build_session_agy_cmd(session, "next message")
        self.assertEqual(cmd, [AGY_PATH, "--continue", "-p", "next message"])

    def test_t3_model_and_effort(self):
        session = {
            "has_active_session": True,
            "flags": {
                "--model": "gemini-3.1-pro-high",
                "--effort": "high",
            },
        }
        cmd = build_session_agy_cmd(session, "do something")
        self.assertEqual(
            cmd,
            [
                AGY_PATH,
                "--continue",
                "--model",
                "gemini-3.1-pro-high",
                "--effort",
                "high",
                "-p",
                "do something",
            ],
        )

    def test_t4_toggle_bare_flag(self):
        session = {
            "has_active_session": False,
            "flags": {
                "--sandbox": True,
                "--model": "gemini-3.6-flash-medium",
            },
        }
        cmd = build_session_agy_cmd(session, "prompt")
        self.assertIn("--sandbox", cmd)
        self.assertIn("--model", cmd)
        self.assertIn("gemini-3.6-flash-medium", cmd)

    def test_t5_repeatable_add_dir(self):
        session = {
            "has_active_session": False,
            "flags": {
                "--add-dir": ["/project/src", "/project/tests"],
            },
        }
        cmd = build_session_agy_cmd(session, "prompt")
        self.assertEqual(
            cmd,
            [
                AGY_PATH,
                "--add-dir",
                "/project/src",
                "/project/tests",
                "-p",
                "prompt",
            ],
        )

    def test_t6_standalone_models(self):
        models_cmd = resolve_command("models")
        cmd = build_standalone_agy_cmd(models_cmd)
        self.assertEqual(cmd, [AGY_PATH, "models"])

    def test_t7_standalone_with_value(self):
        plugin_install_cmd = resolve_command("plugininstall")
        cmd = build_standalone_agy_cmd(plugin_install_cmd, "myplugin@marketplace")
        self.assertEqual(
            cmd, [AGY_PATH, "plugin", "install", "myplugin@marketplace"]
        )

    def test_t8_conflict_resolution(self):
        # Setting --dangerously-skip-permissions conflicts with --mode
        session = {
            "has_active_session": False,
            "flags": {
                "--mode": "plan",
                "--dangerously-skip-permissions": True,
            },
        }
        # Simulate execute_command clearing conflicts
        yolo_cmd = resolve_command("yolo")
        if yolo_cmd and yolo_cmd.conflicts_with:
            for conf_flag in yolo_cmd.conflicts_with:
                session["flags"].pop(conf_flag, None)

        cmd = build_session_agy_cmd(session, "prompt")
        self.assertNotIn("--mode", cmd)
        self.assertIn("--dangerously-skip-permissions", cmd)

    def test_t9_full_combo(self):
        session = {
            "has_active_session": True,
            "flags": {
                "--model": "claude-sonnet-4-6",
                "--effort": "high",
                "--mode": "plan",
                "--output-format": "json",
                "--add-dir": ["/project/src", "/project/tests"],
                "--sandbox": True,
            },
        }
        cmd = build_session_agy_cmd(session, "complex prompt")
        self.assertIn("--continue", cmd)
        self.assertIn("--model", cmd)
        self.assertIn("claude-sonnet-4-6", cmd)
        self.assertIn("--effort", cmd)
        self.assertIn("high", cmd)
        self.assertIn("--mode", cmd)
        self.assertIn("plan", cmd)
        self.assertIn("--output-format", cmd)
        self.assertIn("json", cmd)
        self.assertIn("--add-dir", cmd)
        self.assertIn("/project/src", cmd)
        self.assertIn("/project/tests", cmd)
        self.assertIn("--sandbox", cmd)
        self.assertEqual(cmd[-2:], ["-p", "complex prompt"])


if __name__ == "__main__":
    unittest.main()
