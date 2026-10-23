# STEINS;GATE Reboot for iPhone

**English** · [中文](README.zh.md)

![iOS 26+](https://img.shields.io/badge/iOS-26%2B-0e1834)
![iPhone 12 or newer](https://img.shields.io/badge/iPhone-A14%20or%20newer-0e1834)
![Windows · macOS · Linux](https://img.shields.io/badge/build%20on-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux-d6b366)
![No game content](https://img.shields.io/badge/game%20content-none%20(bring%20your%20own)-d6b366)

A native iPhone port of **STEINS;GATE (remastered, Chinese release, v1.0.3)** for the Nintendo Switch. It is not an
emulator: the game's own program is rewritten offline to run directly on the iPhone's CPU, and its graphics run on
Metal. On the phone it is called **命运石之门reboot**.

This repository is the **installer kit**. It holds no game content. You bring your own game files and `prod.keys`.
Any Windows, Mac or Linux computer turns them into an iPhone app, and you install that app with Sideloadly.

---

## Contents

- [What you need](#what-you-need)
- [Quick start](#quick-start)
- [Install on the iPhone](#install-on-the-iphone)
- [Playing](#playing)
- [How it works (and why Windows can build an iPhone app)](#how-it-works)
- [Troubleshooting](#troubleshooting)
- [What has been tested](#what-has-been-tested)
- [What is in this repository](#what-is-in-this-repository)
- [Legal](#legal)

## What you need

| | |
|---|---|
| **Game files** | Dumped from your own Switch: the **base game NSP** and the **1.0.3 update NSP**. They must be the same files the port was made from; the kit checks them by SHA-256 and tells you if they differ. NSP only, not XCI. |
| **Keys** | `prod.keys` from the same Switch. |
| **Computer** | Windows 10/11 (x64), a Mac with Apple silicon, or Linux (x64 / arm64). About **10 GB** of free disk space and an internet connection the first time. No Xcode, no developer tools. |
| **iPhone** | iOS 26 or later, an A14 chip or newer (iPhone 12 and later). About **7 GB** free. |
| **Apple ID** | A free one is enough. It is only used to sign the app. |
| **Sideloadly** | From [sideloadly.io](https://sideloadly.io) (Windows / Mac). On Windows it also needs the iTunes and iCloud installers from Apple's website (not the Microsoft Store versions); Sideloadly's site links them. |

## Quick start

1. **Get the kit**: `git clone https://github.com/porter-hub/steinsgate-reboot-ios.git`, or download it as a ZIP from
   GitHub and unpack it.
2. **Add your files**: put both NSPs and `prod.keys` into the **`game-files`** folder. Any file names work, because the
   kit finds files by their content.
3. **Run Start**:

   | System | How |
   |---|---|
   | Windows | Double-click **`Start-Windows.bat`**. If SmartScreen says "Windows protected your PC", choose *More info → Run anyway*. |
   | macOS | Double-click **`Start-macOS.command`**. If macOS refuses to open it, go to *System Settings → Privacy & Security* and click *Open Anyway* at the bottom, then double-click again. |
   | Linux | In a terminal: `bash start-linux.sh` |

   It checks your computer first. If **uv** (which provides Python) or the **.NET 10 runtime** is missing, it asks
   whether to install it with the official installer, into your user folder and without administrator rights. You
   can also say no and install them yourself from the links it prints.

   Then it runs the eight steps on its own, in about 10 minutes. The last line says **`PACK OK`**.
4. **Collect the result** from the **`output`** folder:
   - `SteinsGate.ipa`: the iPhone app (about 40 MB);
   - `game-data/`: seven `.tar` files (about 5.8 GB in total).

If anything goes wrong, the kit stops with a red message that says what and why. Fix it and run Start again. Steps
that are already done are skipped.

## Install on the iPhone

1. **Developer Mode**: on the iPhone, open *Settings → Privacy & Security → Developer Mode* and turn it on (the phone
   restarts). If the switch is not there yet, do step 2 first; it appears after the first install.
2. **Install the app**: connect the iPhone with a cable and tap *Trust This Computer*. Open Sideloadly, drag
   `SteinsGate.ipa` into it, enter your Apple ID and click **Start**.
3. **Trust the developer**: on the iPhone, open *Settings → General → VPN & Device Management*, tap your Apple ID and
   choose **Trust**.
4. **Copy the game data**: the app appears in your computer's file sharing as **命运石之门reboot**. Copy every `.tar`
   file from `output/game-data` into it:
   - **Windows**: in iTunes, click the iPhone icon, then *File Sharing → 命运石之门reboot*, and drag the files into the
     document list. With the *Apple Devices* app instead: select the iPhone, then *Files → 命运石之门reboot*.
   - **macOS**: in Finder, select the iPhone in the sidebar, then *Files*, and drag the files onto
     *命运石之门reboot*.
   - **Linux**: there is no official file sharing. libimobiledevice's `ifuse --documents` can do it (not tested), or
     copy the files from a Windows PC or a Mac.

   Wait until the copy has finished.
5. **Start the game**. The first launch shows two progress screens, *importing game data* and *preparing shaders*
   (about a minute in total, once only), and then the game starts. The `.tar` files are deleted once imported.

> **Every 7 days:** a free Apple ID signs apps for 7 days. When the app stops opening, install `SteinsGate.ipa` again
> with Sideloadly. Your saves and the game data stay. **Do not delete the app first**: deleting it deletes the saves.

## Playing

Touch works everywhere, with no on-screen controller needed:

| Gesture | Does |
|---|---|
| Tap | A: advance the text, confirm |
| Double tap, or two-finger tap | B: back, cancel |
| One-finger drag | The stick, held while your finger stays down: move the cursor, scroll menus, dial the phone |
| Two-finger swipe up / down | Backlog / system menu |
| Long press | Read-text skip on / off (the game's R button) |
| Three-finger tap | Show / hide the on-screen controller |

Two small buttons always stay at the screen edge, on the side away from the Dynamic Island:

- 🎮 cycles the on-screen controller: hidden → top buttons only (skip, force skip, phone, +, −) → everything;
- ⚙ opens the settings: opacity, idle fade, one switch per gesture, the FPS readout, and save export / import.

A game controller works too. The on-screen buttons hide while one is connected.

## How it works

> **"There is no Xcode on Windows. How can it build an iPhone app?"**
>
> It doesn't have to compile anything. The app itself (the code that stands in for the Switch system, the Metal
> graphics, the touch controls) was compiled once on a Mac and is in this repository as `kit/shell/Port-shell.zip`.
> That app is a *shell*: it contains no game, so it can be shared. Everything the computer does with your files is
> data work, which plain Python and .NET can do on any system. Signing, the only step that needs Apple, is done by
> Sideloadly.

```
your base NSP + update NSP + prod.keys
        │  1. check: SHA-256 of both packages must match
        │  2. extract (keys go to a private temp folder and are deleted right after; never printed)
        ▼
game program (ExeFS)                       game data (RomFS, 5.8 GB)
        │  3. rewrite the AArch64 code so it runs     │
        │     natively on iOS (offline; nothing is    │
        │     patched at run time)                    │
        │  4. → Guest.framework                       │
        │  5. translate 487 shaders → Metal source    │
        │  +  the game's own icon                     │  7. → output/game-data/*.tar
        ▼                                             ▼
  kit/shell/Port-shell.zip (the shell)      you copy them into the app;
        │  6. put it all in → output/SteinsGate.ipa    it imports them on first launch
        ▼
Sideloadly signs it with your Apple ID → iPhone
```

Every output is compared, file by file, with SHA-256 values recorded when this kit was built. Those values come from a
run on the developer's own files, and that run's package was then tried on an iPhone. So the package you make is
byte-for-byte the tested one, on any system. If anything differs, the kit stops instead of handing you a package that
might not work.

The shaders are shipped as Metal source because only Apple's tools can compile Metal ahead of time. The iPhone
compiles them once on the first launch (*preparing shaders*) and keeps the result.

## Troubleshooting

| Message or problem | What to do |
|---|---|
| `game-files 文件夹里缺东西` (something missing in game-files) | One of the three files is missing, or an NSP is not the expected dump. Only the Chinese release's base game and its 1.0.3 update work, and only as NSP. |
| `解包失败` (extraction failed) | Usually `prod.keys` is too old for the game, or an NSP is incomplete. Dump the keys again from an up-to-date Switch. |
| `和安装工具预期的结果不一致` (output differs from what the kit expects) | Your files or tools differ from the tested ones. Run Start again; if it repeats, report the screen. |
| `磁盘空间不够` (not enough disk space) | Free about 10 GB on the drive that holds this folder. |
| Sideloadly: *maximum number of apps* | A free Apple ID can have 3 self-signed apps on one device. Remove one. |
| The app shows *还没有游戏数据* (no game data yet) | Copy the `.tar` files (Install step 4), close the app from the app switcher and open it again. |
| The app shows *导入游戏数据失败* (import failed) | The copy was still running, the iPhone ran out of space (about 7 GB needed), or a file is incomplete. Fix that, then close and reopen the app; the import continues where it stopped. |
| The app shows *安装包不完整* (incomplete package) | The IPA lacks some files. Make it again with Start and reinstall. |
| The app stopped opening after a week | Re-install with Sideloadly (see *Every 7 days* above). |

The console messages of the kit and the screens of the app are in Chinese.

## What has been tested

| | |
|---|---|
| ✅ Building on macOS (Apple silicon) | from a fresh clone, byte-for-byte identical to the recorded package |
| ✅ iPhone 17 Pro, iOS 26, free Apple ID | installed, data imported, shaders prepared (487, 29 s, none failed), game runs at 60 fps with sound |
| ✅ The kit holds no game content | every file was byte-scanned against the game files before release |
| ⚠️ Windows | not run yet. Written and checked for Windows: line endings, paths, console encoding. |
| ⚠️ Linux | building only. Installing needs third-party tools (no Sideloadly for Linux); not tested. |
| ⚠️ Other iPhones | not tested. Phones with less memory than the 17 Pro may be less stable. |
| ⚠️ Known issue | leaving the app during a movie and coming back can stop the movie picture (being worked on) |

## What is in this repository

```
README.md, README.zh.md      this guide (English / 中文)
Start-Windows.bat            Windows launcher → kit/bootstrap.ps1
Start-macOS.command          macOS launcher   → kit/bootstrap.sh
start-linux.sh               Linux launcher   → kit/bootstrap.sh
game-files/                  put your NSPs and prod.keys here (ignored by git)
kit/
  pack.py                    the packer (the eight steps above)
  bootstrap.sh / .ps1        checks uv and .NET, offers the official installers
  shell/Port-shell.zip       the content-free app (a zip on purpose: it is not installable as it is)
  src/                       the port's own tools: rewriter, shader chain, key filter, game facts (game.json)
  dotnet/                    the extractor (LibHac) and the shader translator (Ryujinx), run with `dotnet`
  spirv-cross/               SPIRV-Cross for each system, built from one pinned source revision
  expect.json                SHA-256 of every output of the tested build
  dmsh_kit.lock              fingerprints of the tools above
  LICENSES/                  third-party licenses
work/, output/               created when you run Start (ignored by git)
```

## Legal

This repository contains **no game content**: no program, art, text, audio, video, shaders or keys. Every game-specific
file is made on your computer from your own legally obtained copy and stays on your computer and your iPhone. **Do not
share the finished `SteinsGate.ipa` or the game data**: they contain the game. STEINS;GATE is a trademark of its owners;
this is an unofficial fan project, not affiliated with or endorsed by them.

Third-party components and their licenses: [`kit/LICENSES`](kit/LICENSES/README.txt).
