import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest


def load_app_module():
    module_path = Path(__file__).with_name("mp4-generator.py")
    spec = importlib.util.spec_from_file_location("mp4_generator", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PackagingHelpersTest(unittest.TestCase):
    def setUp(self):
        self.module = load_app_module()

    def test_resource_path_uses_app_resources_when_frozen(self):
        original_executable = sys.executable
        had_frozen = hasattr(sys, "frozen")
        original_frozen = getattr(sys, "frozen", None)

        try:
            sys.frozen = True
            sys.executable = "/tmp/MP4 Generator.app/Contents/MacOS/MP4 Generator"

            self.assertEqual(
                self.module.resource_path("bin/ffmpeg"),
                "/tmp/MP4 Generator.app/Contents/Resources/bin/ffmpeg",
            )
        finally:
            sys.executable = original_executable
            if had_frozen:
                sys.frozen = original_frozen
            else:
                delattr(sys, "frozen")

    def test_media_binary_prefers_bundled_executable(self):
        original_executable = sys.executable
        had_frozen = hasattr(sys, "frozen")
        original_frozen = getattr(sys, "frozen", None)

        with tempfile.TemporaryDirectory() as tmpdir:
            resources = Path(tmpdir) / "MP4 Generator.app" / "Contents" / "Resources"
            bundled = resources / "bin" / "ffmpeg"
            bundled.parent.mkdir(parents=True)
            bundled.write_text("#!/bin/sh\n", encoding="utf-8")
            bundled.chmod(0o755)

            try:
                sys.frozen = True
                sys.executable = str(
                    resources.parent / "MacOS" / "MP4 Generator"
                )

                self.assertEqual(self.module.media_binary("ffmpeg"), str(bundled))
            finally:
                sys.executable = original_executable
                if had_frozen:
                    sys.frozen = original_frozen
                else:
                    delattr(sys, "frozen")

    def test_transcode_commands_are_argument_vectors(self):
        commands = self.module.build_transcode_commands(
            "v3v7",
            "/tmp/video raw",
            "video 123",
            "/app/Contents/Resources/bin/ffmpeg",
        )

        self.assertEqual(commands[0][0], "/app/Contents/Resources/bin/ffmpeg")
        self.assertEqual(
            commands[0][1],
            [
                "-i",
                "/tmp/video raw/video 123.m3u8",
                "-c:a",
                "aac",
                "-vn",
                "/tmp/video raw/audio-only.ts",
            ],
        )
        self.assertTrue(all(isinstance(args, list) for _, args in commands))

    def test_write_concat_playlist_uses_sorted_ts_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base_path = Path(tmpdir)
            (base_path / "clip 2.ts").write_text("", encoding="utf-8")
            (base_path / "clip 1.ts").write_text("", encoding="utf-8")
            playlist = base_path / "video.m3u8"

            self.module.write_concat_playlist(str(base_path), str(playlist))

            self.assertEqual(
                playlist.read_text(encoding="utf-8"),
                "file 'clip 1.ts'\nfile 'clip 2.ts'\n",
            )


if __name__ == "__main__":
    unittest.main()
