"""xHE-AAC -> plain AAC with Windows' own decoder.

ffmpeg (even 8.x) can't decode all of xHE-AAC: about a quarter of the packets fail and the
result has dropouts. Windows 10/11 ship Fraunhofer's xHE-AAC decoder in Media Foundation, so the
audio is transcoded by Windows (WinRT MediaTranscoder via PowerShell) and ffmpeg only copies the
original's chapters and tags back in - no second encode.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PS_SCRIPT = r"""
param([string]$Src, [string]$Dst, [int]$Bitrate, [int]$Rate)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Transcoding.MediaTranscoder, Windows.Media.Transcoding, ContentType = WindowsRuntime]
$null = [Windows.Media.MediaProperties.MediaEncodingProfile, Windows.Media.MediaProperties, ContentType = WindowsRuntime]
$ext = [System.WindowsRuntimeSystemExtensions].GetMethods()
$opTask = $ext | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
$actTask = $ext | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncActionWithProgress`1' } | Select-Object -First 1
function Await($op, [Type]$type) { $t = $opTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Src)) ([Windows.Storage.StorageFile])
$folder = Await ([Windows.Storage.StorageFolder]::GetFolderFromPathAsync((Split-Path $Dst))) ([Windows.Storage.StorageFolder])
$out = Await ($folder.CreateFileAsync((Split-Path $Dst -Leaf), [Windows.Storage.CreationCollisionOption]::ReplaceExisting)) ([Windows.Storage.StorageFile])
$profile = [Windows.Media.MediaProperties.MediaEncodingProfile]::CreateM4a([Windows.Media.MediaProperties.AudioEncodingQuality]::High)
$profile.Audio.Bitrate = [uint32]$Bitrate
if ($Rate -gt 0) { $profile.Audio.SampleRate = [uint32]$Rate }
$tr = New-Object Windows.Media.Transcoding.MediaTranscoder
$prep = Await ($tr.PrepareFileTranscodeAsync($file, $out, $profile)) ([Windows.Media.Transcoding.PrepareTranscodeResult])
if (-not $prep.CanTranscode) { Write-Error "Windows can't decode this file: $($prep.FailureReason)"; exit 2 }
$t = $actTask.MakeGenericMethod([double]).Invoke($null, @($prep.TranscodeAsync()))
$t.Wait(-1) | Out-Null
if ($t.Status -ne 'RanToCompletion') { Write-Error "transcode ended with $($t.Status)"; exit 3 }
"OK"
"""


def windows_decode_to_aac(src: Path, dst: Path, bitrate_kbps: int, sample_rate: int = 0,
                          cancelled: Callable[[], bool] = lambda: False) -> None:
    """Transcode src (e.g. xHE-AAC) to AAC-LC m4a at dst with Windows' Media Foundation."""
    if os.name != "nt":
        raise OSError("xHE-AAC needs Windows' decoder (Windows 10/11)")
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "xhe.ps1"
        script.write_text(PS_SCRIPT, encoding="utf-8-sig")
        cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
               "-Src", str(src), "-Dst", str(dst), "-Bitrate", str(bitrate_kbps * 1000), "-Rate", str(sample_rate)]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace",
                                creationflags=_NO_WINDOW)
        while proc.poll() is None:
            if cancelled():
                proc.kill()
                Path(dst).unlink(missing_ok=True)
                raise OSError("cancelled")
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
        out, err = proc.communicate()
    if proc.returncode != 0 or not Path(dst).is_file() or Path(dst).stat().st_size == 0:
        Path(dst).unlink(missing_ok=True)
        raise OSError(f"Windows could not convert the xHE-AAC audio: {(err or out).strip()[-300:]}")
