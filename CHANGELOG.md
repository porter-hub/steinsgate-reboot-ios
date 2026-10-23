# Changelog

All notable changes to the installer kit. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions: [SemVer](https://semver.org/). They stay 0.x until the kit has been confirmed on Windows and on iPhones other
than the iPhone 17 Pro.

A new version is needed whenever anything a user runs or receives changes: the app shell, the tools, the expected
outputs or the documents. Every release is a commit `release: vX.Y.Z` with an annotated tag `vX.Y.Z`.

## [0.1.0] - 2026-10-23

First release.

### Added
- **Packing on Windows, macOS and Linux.** Your own base and 1.0.3 update NSPs plus `prod.keys` become
  `output/SteinsGate.ipa` (about 40 MB) and `output/game-data/*.tar` (about 5.8 GB). It takes eight steps:
  1. check the environment and offer the official uv / .NET installers;
  2. find the packages by SHA-256;
  3. extract (keys only in a private temporary folder);
  4. rewrite the game program;
  5. translate the 487 shaders to Metal source;
  6. assemble the package with the game's own icon;
  7. pack the game data;
  8. print the next steps.
- **Every output is checked by SHA-256** against the build that was run on an iPhone. A from-scratch run is
  byte-for-byte that package, or the kit stops.
- **The app (命运石之门reboot)** checks at launch that the package is complete. On first launch it imports the game
  data from the files you copied in, then prepares the shaders once, with progress screens.
- **Touch controls** (tap, double tap, drag as a held stick, two-finger swipes, long press, three-finger tap), an
  on-screen controller with three states, a settings screen and game controller support.
- **`--selftest`**: checks everything that can be checked without game files. CI runs it on Windows, macOS and Linux.
- **README** in English and Chinese.

### Known issues
- Not yet run end to end on Windows or Linux. The self-test and the Windows-specific code paths are covered; a full
  pack needs game files and has only been run on macOS.
- Leaving the app during a movie and coming back can stop the movie picture.
- Only tested on an iPhone 17 Pro.
