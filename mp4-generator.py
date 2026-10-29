import sys
import os
import subprocess
from pathlib import Path
import shlex
import shutil
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QLineEdit, QPushButton, QTextEdit, QGridLayout
from PyQt5.QtGui import QColor, QTextCharFormat, QFont, QIcon
from PyQt5.QtCore import QProcess
import json


def app_resources_dir():
    if getattr(sys, "frozen", False) and sys.platform == "darwin":
        return Path(sys.executable).parents[1] / "Resources"
    return Path(__file__).resolve().parent


def resource_path(relative_path):
    return str(app_resources_dir() / relative_path)


def media_binary(name):
    bundled = Path(resource_path(os.path.join("bin", name)))
    if bundled.exists() and os.access(bundled, os.X_OK):
        return str(bundled)

    discovered = shutil.which(name)
    if discovered:
        return discovered

    return name


def build_transcode_commands(version, base_path, video_id, ffmpeg_path):
    base = Path(base_path)
    mp4_output = str(base / f"{video_id}.mp4")
    audio_output = str(base / "audio-only.ts")
    video_output = str(base / "video-only.ts")

    if version == "v3v7":
        manifest = str(base / f"{video_id}.m3u8")
        return [
            (ffmpeg_path, ["-i", manifest, "-c:a", "aac", "-vn", audio_output]),
            (
                ffmpeg_path,
                [
                    "-i",
                    manifest,
                    "-c:v",
                    "libx264",
                    "-copyts",
                    "-muxpreload",
                    "0",
                    "-muxdelay",
                    "0",
                    "-vf",
                    "fps=30",
                    "-an",
                    video_output,
                ],
            ),
            (
                ffmpeg_path,
                [
                    "-i",
                    audio_output,
                    "-i",
                    video_output,
                    "-c",
                    "copy",
                    "-map",
                    "0:a:0",
                    "-map",
                    "1:v:0",
                    "-y",
                    mp4_output,
                ],
            ),
        ]

    if version == "v5":
        manifest = str(base / f"{video_id}-manifest.mpd")
        return [
            (
                ffmpeg_path,
                [
                    "-allowed_extensions",
                    "webm",
                    "-i",
                    manifest,
                    "-c:a",
                    "aac",
                    "-vn",
                    audio_output,
                ],
            ),
            (
                ffmpeg_path,
                [
                    "-allowed_extensions",
                    "webm",
                    "-i",
                    manifest,
                    "-c:v",
                    "libx264",
                    "-copyts",
                    "-muxpreload",
                    "0",
                    "-muxdelay",
                    "0",
                    "-vf",
                    "fps=30",
                    "-an",
                    video_output,
                ],
            ),
            (
                ffmpeg_path,
                [
                    "-i",
                    audio_output,
                    "-i",
                    video_output,
                    "-c",
                    "copy",
                    "-map",
                    "0:a:0",
                    "-map",
                    "1:v:0",
                    "-y",
                    mp4_output,
                ],
            ),
        ]

    raise ValueError(f"Unsupported video version: {version}")


def build_concat_command(m3u8_file, output_file, ffmpeg_path):
    return (
        ffmpeg_path,
        ["-f", "concat", "-safe", "0", "-i", m3u8_file, "-c", "copy", output_file],
    )


def build_ffprobe_command(file_path, ffprobe_path):
    return (
        ffprobe_path,
        ["-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", file_path],
    )


def write_concat_playlist(base_path, playlist_path):
    base = Path(base_path)
    ts_files = sorted(path for path in base.glob("*.ts") if path.is_file())

    if not ts_files:
        raise FileNotFoundError(f"No .ts files found in {base}")

    with open(playlist_path, "w", encoding="utf-8") as playlist:
        for path in ts_files:
            playlist.write(f"file {shlex.quote(path.name)}\n")


def command_to_text(program, args):
    return shlex.join([program, *args])


class FFmpegGUI(QWidget):
    def __init__(self):
        super().__init__()
        self.process = None
        self.command_index = 0
        self.video_id = ""
        self.initUI()

    def initUI(self):
        self.setWindowTitle('MP4 Generator - created by Sam')
        self.setGeometry(100, 100, 800, 600)
        icon_path = resource_path("mp4-generator.png")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        layout = QVBoxLayout()

        input_layout = QVBoxLayout()
        self.video_id_input = QLineEdit()
        self.video_id_input.setPlaceholderText('Enter Video ID')
        input_layout.addWidget(self.video_id_input)

        button_layout = QGridLayout()

        # Button to transcode the video
        self.transcode_button = QPushButton('Transcode the video')
        self.transcode_button.clicked.connect(self.transcode_video)
        button_layout.addWidget(self.transcode_button, 0, 0)

        # New button to generate playlist and transcode
        self.generate_playlist_button = QPushButton('Generate Playlist and Transcode')
        self.generate_playlist_button.clicked.connect(self.generate_playlist_and_transcode)
        button_layout.addWidget(self.generate_playlist_button, 0, 1)

        self.play_audio_button = QPushButton('Play Audio only')
        self.play_audio_button.clicked.connect(self.play_audio)
        button_layout.addWidget(self.play_audio_button, 1, 0)

        self.play_video_button = QPushButton('Play Video only')
        self.play_video_button.clicked.connect(self.play_video)
        button_layout.addWidget(self.play_video_button, 1, 1)

        self.play_mp4_button = QPushButton('Play the MP4')
        self.play_mp4_button.clicked.connect(self.play_mp4)
        button_layout.addWidget(self.play_mp4_button, 2, 0, 1, 2)

        self.ffprobe_button = QPushButton('Show audio/video details')
        self.ffprobe_button.clicked.connect(self.run_ffprobe)
        button_layout.addWidget(self.ffprobe_button, 3, 0, 1, 2)

        input_layout.addLayout(button_layout)
        layout.addLayout(input_layout)

        self.output_text = QTextEdit()
        self.output_text.setReadOnly(True)
        layout.addWidget(self.output_text)

        self.clear_button = QPushButton('Clear')
        self.clear_button.clicked.connect(self.clear_fields)
        layout.addWidget(self.clear_button)

        self.open_file_button = QPushButton('Open File Location')
        self.open_file_button.clicked.connect(self.open_file_location)
        self.open_file_button.setEnabled(False)
        layout.addWidget(self.open_file_button)

        self.setLayout(layout)

    def update_video_id(self, text):
        self.video_id = text

    def generate_playlist_and_transcode(self):
        self.video_id = self.video_id_input.text()
        if not self.video_id:
            self.log_message("Please enter a Video ID", error=True)
            return

        # Prepare paths
        user_home = os.path.expanduser("~")
        base_path = f"{user_home}/Downloads/{self.video_id}/raw"
        m3u8_file = f"{base_path}/{self.video_id}.m3u8"

        self.log_message(f"Generating playlist: {m3u8_file}")

        try:
            write_concat_playlist(base_path, m3u8_file)
            self.log_message("Playlist created successfully!")
            self.transcode_generated_playlist(m3u8_file)
        except (FileNotFoundError, OSError) as e:
            self.log_message(f"Error creating playlist: {e}", error=True)

    def transcode_generated_playlist(self, m3u8_file):
        user_home = os.path.expanduser("~")
        output_file = f"{user_home}/Downloads/{self.video_id}/raw/{self.video_id}.mp4"
        program, args = build_concat_command(m3u8_file, output_file, media_binary("ffmpeg"))

        self.log_message(f"Running ffmpeg transcode command: {command_to_text(program, args)}")
        
        self.process = QProcess()
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.readyReadStandardError.connect(self.handle_stderr)
        self.process.finished.connect(self.transcode_finished)

        self.process.start(program, args, QProcess.ReadWrite)

    def transcode_finished(self, exit_code, exit_status):
        if exit_code == 0:
            self.log_message("Transcoding completed successfully!", success=True)
            self.open_file_button.setEnabled(True)  # Enable to open the file location
        else:
            self.log_message(f"Error during transcoding. Exit code: {exit_code}", error=True)

    def transcode_video(self):
        self.video_id = self.video_id_input.text()
        if not self.video_id:
            self.log_message("Please enter a Video ID", error=True)
            return

        version = self.detect_video_version()
        if version:
            self.run_ffmpeg_commands(version)
        else:
            self.log_message("Unable to detect video version. Please check the video files.", error=True)

    def detect_video_version(self):
        user_home = os.path.expanduser("~")
        base_path = f"{user_home}/Downloads/{self.video_id}/raw"
        
        self.log_message(f"Checking for files in: {base_path}")

        if not os.path.exists(base_path):
            self.log_message(f"Directory does not exist: {base_path}", error=True)
            return None
        
        m3u8_file = f"{base_path}/{self.video_id}.m3u8"
        mpd_file = f"{base_path}/{self.video_id}-manifest.mpd"
        
        if os.path.exists(m3u8_file):
            self.log_message(f"Found m3u8 file: {m3u8_file}")
            return 'v3v7'
        elif os.path.exists(mpd_file):
            self.log_message(f"Found mpd file: {mpd_file}")
            return 'v5'
        else:
            self.log_message(f"No m3u8 or mpd file found in {base_path}", error=True)
            return None

    def run_ffmpeg_commands(self, version):
        user_home = os.path.expanduser("~")
        base_path = f"{user_home}/Downloads/{self.video_id}/raw"

        try:
            self.commands = build_transcode_commands(
                version, base_path, self.video_id, media_binary("ffmpeg")
            )
        except ValueError as e:
            self.log_message(str(e), error=True)
            return

        self.messages = [
            "Transcoding the audio files process was successful",
            "Transcoding the video files process was successful",
            "Transcoding the MP4 file process was successful"
        ]

        self.command_index = 0
        self.run_next_command()

    def run_next_command(self):
        if self.command_index < len(self.commands):
            self.process = QProcess()
            self.process.readyReadStandardOutput.connect(self.handle_stdout)
            self.process.readyReadStandardError.connect(self.handle_stderr)
            self.process.finished.connect(self.process_finished)

            program, args = self.commands[self.command_index]
            self.log_message(f"Running command: {command_to_text(program, args)}")
            self.process.start(program, args)
        else:
            self.log_message("All commands completed successfully!", success=True)
            self.open_file_button.setEnabled(True)  # Ensure the button remains enabled here

    def handle_stdout(self):
        data = self.process.readAllStandardOutput().data().decode()
        self.log_message(data.strip())

    def handle_stderr(self):
        data = self.process.readAllStandardError().data().decode()
        self.log_message(data.strip())

    def process_finished(self, exit_code, exit_status):
        if exit_code == 0:
            self.log_message(self.messages[self.command_index], success=True)
            self.command_index += 1
            self.run_next_command()
        else:
            self.log_message(f"Error executing command. Exit code: {exit_code}", error=True)

    def log_message(self, message, error=False, success=False, ffprobe=False):
        format = QTextCharFormat()
        if error:
            format.setForeground(QColor(255, 0, 0))  # Red for errors
        elif success:
            format.setForeground(QColor(0, 255, 0))  # Green for success
            format.setFontWeight(QFont.Bold)  # Bold for success
        elif ffprobe:
            format.setForeground(QColor(255, 165, 0))  # Orange for ffprobe output
        format.setFont(QFont("Courier New", 10))  # Use "Courier New" instead of "Courier"
        self.output_text.setCurrentCharFormat(format)
        self.output_text.append(message)
        self.output_text.ensureCursorVisible()

    def clear_fields(self):
        self.video_id_input.clear()
        self.output_text.clear()
        self.open_file_button.setEnabled(False)

    def open_file_location(self):
        self.video_id = self.video_id_input.text()  # Get the current video ID
        if self.video_id:
            user_home = os.path.expanduser("~")
            file_path = f"{user_home}/Downloads/{self.video_id}/raw"  # Set the path to the raw folder
            
            if os.path.exists(file_path):
                if sys.platform == "win32":
                    os.startfile(file_path)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", file_path])
                else:
                    subprocess.Popen(["xdg-open", file_path])
            else:
                self.log_message(f"Folder not found: {file_path}", error=True)
        else:
            self.log_message("Please enter a Video ID before opening the folder.", error=True)

    def play_audio(self):
        self.play_media('audio-only.ts')

    def play_video(self):
        self.play_media('video-only.ts')

    def play_mp4(self):
        self.play_media(f'{self.video_id}.mp4')

    def play_media(self, file_name):
        self.video_id = self.video_id_input.text()
        if not self.video_id:
            self.log_message("Please enter a Video ID", error=True)
            return

        user_home = os.path.expanduser("~")
        file_path = f"{user_home}/Downloads/{self.video_id}/raw/{file_name}"

        if os.path.exists(file_path):
            subprocess.Popen([media_binary("ffplay"), file_path])
            self.log_message(f"Playing {file_name}")
        else:
            self.log_message(f"File not found: {file_path}", error=True)

    def run_ffprobe(self):
        self.video_id = self.video_id_input.text()
        if not self.video_id:
            self.log_message("Please enter a Video ID", error=True)
            return

        user_home = os.path.expanduser("~")
        file_path = f"{user_home}/Downloads/{self.video_id}/raw/{self.video_id}.mp4"

        if not os.path.exists(file_path):
            self.log_message(f"File not found: {file_path}", error=True)
            return

        program, args = build_ffprobe_command(file_path, media_binary("ffprobe"))
        
        try:
            result = subprocess.run([program, *args], check=True, capture_output=True, text=True)
            probe_data = json.loads(result.stdout)

            audio_duration = None
            video_duration = None
            total_duration = probe_data['format']['duration']

            for stream in probe_data['streams']:
                if stream['codec_type'] == 'audio':
                    audio_duration = stream.get('duration', 'N/A')
                elif stream['codec_type'] == 'video':
                    video_duration = stream.get('duration', 'N/A')

            total_seconds = float(total_duration)
            pst_time = self.format_time(total_seconds - 3 * 3600)  # PST is UTC-8
            dst_time = self.format_time(total_seconds - 2 * 3600)  # DST is UTC-7

            output = f"""
            Audio Duration: {audio_duration} seconds = {self.format_time(float(audio_duration) if audio_duration != 'N/A' else 0)}
            Video Duration: {video_duration} seconds = {self.format_time(float(video_duration) if video_duration != 'N/A' else 0)}
            PST Time: {pst_time}
            DST Time: {dst_time}
            Total Duration: {total_duration} seconds = {self.format_time(total_seconds)}
            """

            self.log_message(output, ffprobe=True)
        except subprocess.CalledProcessError as e:
            self.log_message(f"Error running ffprobe: {e}", error=True)

    def format_time(self, seconds):
        hours, remainder = divmod(int(seconds), 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

if __name__ == '__main__':
    app = QApplication(sys.argv)
    ex = FFmpegGUI()
    ex.show()
    sys.exit(app.exec_())
