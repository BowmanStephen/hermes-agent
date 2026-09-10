> **STATUS (2026-09-09):** Reference-only. Hardcoded paths (open-webui-venv-312, ai-services venv, mini IP 100.125.187.18). Superseded by PRD-Mini-Hub-v2; do not execute without path review. NOTE: tome-cli-wrapper.sh execs /Users/stephenbowman/tome-cli.py — a different file than ./tome-cli.py.

#!/Users/stephenbowman/ai-services/bin/python3
"""
tome-cli — Local meeting transcription to Obsidian vault.
Replacement for Tome GUI app — works over SSH, no screen needed.
Uses shared venv: ~/ai-services (has whisper, chromadb, llama-cpp-python)

Usage:
  tome-cli record              # Record from mic until Ctrl+C
  tome-cli transcribe <file>   # Transcribe an existing audio file
  tome-cli --vault <path>      # Set Obsidian vault path
"""

import argparse
import datetime
import os
import subprocess
import sys
import tempfile

# Ensure we use the shared venv packages
sys.path.insert(0, os.path.expanduser("~/ai-services/lib/python3.14/site-packages"))

VAULT_PATH = os.path.expanduser("~/Documents/01-Knowledge-Base/Work/_AZ-Work")

def record_audio(duration=None, output_path=None):
    """Record audio from default microphone."""
    if output_path is None:
        output_path = os.path.join(tempfile.gettempdir(), f"tome_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.wav")
    
    print(f"Recording to {output_path}...")
    print("Press Ctrl+C to stop.")
    
    try:
        # Use sox (rec) or ffmpeg for recording
        if subprocess.run(["which", "rec"], capture_output=True).returncode == 0:
            cmd = ["rec", "-r", "16000", "-c", "1", "-b", "16", output_path]
            if duration:
                cmd.extend(["trim", "0", str(duration)])
            subprocess.run(cmd, check=True)
        elif subprocess.run(["which", "ffmpeg"], capture_output=True).returncode == 0:
            cmd = ["ffmpeg", "-f", "avfoundation", "-i", ":0", "-ar", "16000", "-ac", "1", "-y", output_path]
            if duration:
                cmd = ["ffmpeg", "-f", "avfoundation", "-i", ":0", "-t", str(duration), "-ar", "16000", "-ac", "1", "-y", output_path]
            subprocess.run(cmd, check=True)
        else:
            print("Error: Need 'sox' (rec) or 'ffmpeg' to record audio.")
            print("Install: brew install sox")
            sys.exit(1)
    except KeyboardInterrupt:
        print("\nRecording stopped.")
    
    return output_path

def transcribe(audio_path, model="base"):
    """Transcribe audio using Whisper."""
    import whisper
    
    print(f"Loading Whisper model '{model}'...")
    model_obj = whisper.load_model(model)
    
    print(f"Transcribing {audio_path}...")
    result = model_obj.transcribe(audio_path, language="en")
    
    return result["text"], result.get("segments", [])

def format_tome_markdown(text, segments, source_app="CLI", attendees=None):
    """Format as Tome-style markdown."""
    now = datetime.datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")
    
    # Estimate duration from segments
    duration_sec = 0
    if segments:
        duration_sec = segments[-1].get("end", 0)
    
    minutes = int(duration_sec // 60)
    seconds = int(duration_sec % 60)
    duration_str = f"{minutes}:{seconds:02d}"
    
    md = f"""---
type: meeting
created: "{date_str}"
time: "{time_str}"
duration: "{duration_str}"
source_app: "{source_app}"
attendees: {attendees or '["You"]'}
tags:
  - log/meeting
  - status/inbox
  - source/tome-cli
---

# Call Recording — {date_str} {time_str}

{text}
"""
    return md

def save_to_vault(markdown, vault_path=None):
    """Save markdown to Obsidian vault."""
    if vault_path is None:
        vault_path = VAULT_PATH
    
    now = datetime.datetime.now()
    filename = f"tome_{now.strftime('%Y%m%d_%H%M%S')}.md"
    filepath = os.path.join(vault_path, filename)
    
    os.makedirs(vault_path, exist_ok=True)
    with open(filepath, "w") as f:
        f.write(markdown)
    
    print(f"Saved to: {filepath}")
    return filepath

def main():
    parser = argparse.ArgumentParser(description="Tome CLI — Local transcription to Obsidian")
    parser.add_argument("command", choices=["record", "transcribe"], help="Action")
    parser.add_argument("file", nargs="?", help="Audio file to transcribe")
    parser.add_argument("--model", default="base", choices=["tiny", "base", "small", "medium"], help="Whisper model")
    parser.add_argument("--vault", default=VAULT_PATH, help="Obsidian vault path")
    parser.add_argument("--duration", type=int, help="Recording duration in seconds")
    
    args = parser.parse_args()
    
    if args.command == "record":
        audio_path = record_audio(duration=args.duration)
    elif args.command == "transcribe":
        if not args.file:
            print("Error: Provide audio file path.")
            sys.exit(1)
        audio_path = args.file
    else:
        parser.print_help()
        sys.exit(1)
    
    text, segments = transcribe(audio_path, model=args.model)
    md = format_tome_markdown(text, segments)
    save_path = save_to_vault(md, vault_path=args.vault)
    
    print(f"\nDone. Transcript saved to: {save_path}")

if __name__ == "__main__":
    main()
