============================================================
  KRAKEN PRIME - Clash of Clans Bot
============================================================

WHAT'S IN THIS FOLDER
  KrakenPrime.exe    The bot itself (that's all you need)

  Everything is built into the exe — Python, all libraries,
  ADB, and Tesseract OCR. No installer, no extra downloads.


FIRST-TIME SETUP (do this once)

  1. Open LDPlayer and set the resolution to 1600 x 900.

  2. In LDPlayer:
     Settings > Other settings > ADB Debugging
     > Enable "Local Connection"
     Then restart LDPlayer.

  3. Open Clash of Clans inside LDPlayer and go to your
     main village screen.


RUNNING THE BOT

  Double-click KrakenPrime.exe

  On first run it creates a "data" folder next to the exe for
  your settings and pinned points, so keep the exe somewhere
  you can write to (e.g. Desktop or a normal folder — not
  inside Program Files).


TROUBLESHOOTING

  Bot doesn't connect to LDPlayer
    - Make sure LDPlayer is running BEFORE starting the bot
    - Resolution must be exactly 1600 x 900
    - ADB Local Connection must be enabled (setup step 2 above)
    - Restart LDPlayer after changing settings

  Windows SmartScreen warning
    Click "More info" > "Run anyway". This appears because
    the exe is not code-signed, not because it is unsafe.

  Antivirus flags the exe
    PyInstaller-built exes are sometimes flagged as false
    positives. Add the exe (or its folder) to your antivirus
    exclusions.

  First launch is slow
    A one-file exe unpacks itself to a temp folder each time
    it starts, so the first launch takes a few extra seconds.


PRIVACY

  The bot sends an anonymous heartbeat (a random ID only) so
  the developer can see how many installs are active.
  No account data, no personal data, no game data is sent.
  Privacy policy: https://kraken.protectiva.site/privacy
